"""
Convert any downloaded/generated 3D model (GLB, GLTF, FBX, OBJ) into a Roblox rigid
accessory FBX (runs inside Blender).

  /Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup -P tools/rbx_convert.py -- \
      model.glb --name my_hat --type Hat [--width 1.6] [--budget 3800] [--rotate 180]

What it does:
  1. imports the model and welds each piece closed (merges split vertices, fills holes)
  2. reduces dense round parts (spheres, capsules) so the total stays under the triangle budget
  3. joins everything into ONE mesh
  4. turns every material color into a swatch of one small palette texture (one material)
  5. scales it to fit the Roblox size box for its type, faces it toward -Y, exports FBX + previews

Limits: image-textured materials are reduced to their average color (fine for flat-color models
like most AI or Three.js exports; detailed textures would need a proper bake).
"""
import argparse
import math
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rbx_accessory as ra  # noqa: E402  (shared palette, export checks, preview renderer)

DENSE = 100  # pieces with more triangles than this get reduced; boxes and flat panels are left alone


def parse():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser(prog="rbx_convert.py")
    ap.add_argument("model")
    ap.add_argument("--name", help="output name (default: file name)")
    ap.add_argument("--type", default="Hat", choices=sorted(ra.LIMITS))
    ap.add_argument("--width", type=float, help="final width in studs (default: 90%% of the size box)")
    ap.add_argument("--budget", type=int, default=3800, help="target triangle count (Roblox max is 4000)")
    ap.add_argument("--rotate", type=float, default=0.0, help="spin around the up axis, degrees, if the front is wrong")
    ap.add_argument("--seat", type=float, default=0.1,
                    help="Hat/Face previews: height of the model's lowest point above the head center")
    return ap.parse_args(argv)


def linear_to_srgb(c):
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def material_hex(mat, warnings):
    rgb = (0.8, 0.8, 0.8)
    if mat is not None:
        rgb = tuple(mat.diffuse_color[:3])
        if mat.use_nodes:
            bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
            if bsdf:
                inp = bsdf.inputs["Base Color"]
                rgb = tuple(inp.default_value[:3])
                img_node = next((n for n in mat.node_tree.nodes if n.type == "TEX_IMAGE" and n.image), None)
                if inp.is_linked and img_node:
                    px = list(img_node.image.pixels)
                    n = max(1, len(px) // 4)
                    rgb = tuple(sum(px[i::4]) / n for i in range(3))  # already display-space for 8-bit images
                    warnings.append(f"material '{mat.name}' had an image texture; using its average color")
                    return "#%02x%02x%02x" % tuple(round(max(0, min(1, c)) * 255) for c in rgb)
    return "#%02x%02x%02x" % tuple(round(max(0, min(1, linear_to_srgb(c))) * 255) for c in rgb)


def import_model(path):
    ext = Path(path).suffix.lower()
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=str(path))
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(path))
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=str(path))
    else:
        sys.exit(f"unsupported file type: {ext}")
    return [o for o in bpy.data.objects if o.type == "MESH"]


def weld(objs):
    """Bake each piece to world space as its own closed, plain mesh object."""
    dg = bpy.context.evaluated_depsgraph_get()
    out = []
    for o in objs:
        ev = o.evaluated_get(dg)
        bm = bmesh.new()
        bm.from_mesh(ev.to_mesh())
        ev.to_mesh_clear()
        bm.transform(o.matrix_world)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
        holes = [e for e in bm.edges if e.is_boundary]
        if holes:
            bmesh.ops.holes_fill(bm, edges=holes, sides=0)
        me = bpy.data.meshes.new(o.name + "_w")
        bm.to_mesh(me)
        bm.free()
        for slot in o.material_slots:
            me.materials.append(slot.material)
        tris = sum(len(p.vertices) - 2 for p in me.polygons)
        smooth = tris > DENSE
        me.polygons.foreach_set("use_smooth", [smooth] * len(me.polygons))
        nob = bpy.data.objects.new(o.name + "_w", me)
        bpy.context.scene.collection.objects.link(nob)
        out.append((nob, tris))
    for o in objs:
        bpy.data.objects.remove(o, do_unlink=True)
    return out


def decimate(parts, budget):
    low = sum(t for _, t in parts if t <= DENSE)
    high = sum(t for _, t in parts if t > DENSE)
    if low + high <= budget or high == 0:
        return
    ratio = max(0.04, (budget - low) / high)
    dg = bpy.context.evaluated_depsgraph_get()
    for ob, t in parts:
        if t <= DENSE:
            continue
        mod = ob.modifiers.new("reduce", "DECIMATE")
        mod.ratio = ratio
        mod.use_collapse_triangulate = True
        dg.update()
        new = bpy.data.meshes.new_from_object(ob.evaluated_get(dg))
        ob.modifiers.clear()
        old = ob.data
        ob.data = new
        bpy.data.meshes.remove(old)


def join(objs):
    for o in bpy.data.objects:
        o.select_set(o in objs)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.join()
    return bpy.context.view_layer.objects.active


def count_tris(me):
    return sum(len(p.vertices) - 2 for p in me.polygons)


def main():
    a = parse()
    src = Path(a.model).resolve()
    name = a.name or src.stem.replace("-", "_").replace(" ", "_")
    out = ra.ROOT / "output" / "accessories" / name
    out.mkdir(parents=True, exist_ok=True)
    warnings = []

    ra.clear_scene()
    sc = bpy.context.scene
    sc.unit_settings.system = "METRIC"
    sc.unit_settings.scale_length = 0.01  # Roblox: 1 Blender unit = 1 stud with FBX Unit Scale

    objs = import_model(src)
    if not objs:
        sys.exit("no meshes found in the file")
    for o in [o for o in bpy.data.objects if o.type != "MESH"]:
        o.hide_render = True
    before_pieces = len(objs)
    dg = bpy.context.evaluated_depsgraph_get()
    before_tris = sum(count_tris(o.evaluated_get(dg).data) for o in objs)

    parts = weld(objs)
    decimate(parts, a.budget)
    ob = join([p for p, _ in parts])
    ob.name = name
    me = ob.data
    if count_tris(me) > a.budget:  # second pass if the first estimate overshot
        decimate([(ob, count_tris(me) + DENSE + 1)], a.budget)
        me = ob.data
    for o in [o for o in bpy.data.objects if o.type != "MESH"]:
        bpy.data.objects.remove(o, do_unlink=True)

    # orient, scale, place
    pts = [v.co.copy() for v in me.vertices]
    mn = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    mx = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    size = mx - mn
    lw, lh, ld = ra.LIMITS[a.type]
    s = a.width / size.x if a.width else 0.9 * min(lw / size.x, lh / size.z, ld / size.y)
    center = (mn + mx) / 2
    if a.type == "Back":
        place = Vector((0, 0.55 + size.y * s / 2, 0.3))
    else:
        place = Vector((0, 0, a.seat + size.z * s / 2))
    me.transform(Matrix.Translation(place) @ Matrix.Rotation(math.radians(a.rotate), 4, "Z")
                 @ Matrix.Diagonal((s, s, s, 1)) @ Matrix.Translation(-center))
    ob.matrix_world = Matrix.Identity(4)

    # palette texture: one swatch per distinct material color
    slot_hex = [material_hex(sl.material, warnings) for sl in ob.material_slots] or ["#cccccc"]
    colors = {}
    for hx in slot_hex:
        colors.setdefault(hx, hx)
    if len(colors) > ra.PALETTE_GRID ** 2:
        sys.exit(f"{len(colors)} colors; the palette holds 16")
    pal = ra.Builder(colors)
    pal.bm.free()
    uv_layer = me.uv_layers.get("UVMap") or me.uv_layers.new(name="UVMap")
    for extra in [l for l in me.uv_layers if l != uv_layer]:
        me.uv_layers.remove(extra)
    uv_layer = me.uv_layers["UVMap"]
    for poly in me.polygons:
        uv = pal.slot_uv(slot_hex[min(poly.material_index, len(slot_hex) - 1)])
        for li in poly.loop_indices:
            uv_layer.data[li].uv = uv
    img = pal.palette_image(f"{name}_texture")
    img.filepath_raw = str(out / "texture.png")
    img.file_format = "PNG"
    img.save()
    me.materials.clear()
    me.materials.append(ra.textured_material(f"{name}_mat", img))
    me.polygons.foreach_set("material_index", [0] * len(me.polygons))

    # clean normals so Roblox never shows inside-out faces
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    open_edges = sum(1 for e in bm.edges if e.is_boundary)
    bad_edges = sum(1 for e in bm.edges if len(e.link_faces) > 2)
    bm.to_mesh(me)
    bm.free()
    me.update()

    bpy.context.view_layer.update()  # refresh bounds after editing mesh data directly
    tris = count_tris(me)
    vs = [v.co for v in me.vertices]
    w, h, d = (max(v[i] for v in vs) - min(v[i] for v in vs) for i in (0, 2, 1))
    problems = []
    if tris > ra.MAX_TRIS:
        problems.append(f"too many triangles ({tris} > {ra.MAX_TRIS})")
    if open_edges:
        problems.append(f"{open_edges} open edges")
    if w > lw or h > lh or d > ld:
        problems.append(f"bigger than {a.type} box {lw}x{lh}x{ld}")

    for o in bpy.data.objects:
        o.select_set(o == ob)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.export_scene.fbx(
        filepath=str(out / f"{name}.fbx"), use_selection=True, object_types={"MESH"},
        apply_scale_options="FBX_SCALE_UNITS", path_mode="COPY", embed_textures=True,
        mesh_smooth_type="FACE", add_leaf_bones=False, bake_anim=False,
    )

    ref = ra.reference_body(a.type)
    views = ("front", "back") if a.type == "Back" else ("front", "side", "back")
    for view in views:
        ra.render(out / f"preview_{view}.png", ob if a.type != "Back" else ref, view)
    bpy.data.objects.remove(ref, do_unlink=True)

    for wmsg in warnings:
        print(f"[rbx] note: {wmsg}")
    if bad_edges:
        print(f"[rbx] note: {bad_edges} edges shared by 3+ faces (overlapping pieces; usually fine for rigid accessories)")
    status = "OK" if not problems else "CHECK: " + "; ".join(problems)
    print(f"[rbx] {name}: {before_pieces} pieces / {before_tris} tris -> 1 mesh / {tris} tris, "
          f"{len(colors)} colors, {w:.2f}x{h:.2f}x{d:.2f} studs ({a.type}) -> {out}  [{status}]")


main()
