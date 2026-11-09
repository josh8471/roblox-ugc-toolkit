"""
Roblox rigid accessory generator (runs inside Blender).

Builds a low-poly, single-mesh, watertight accessory from a JSON file and exports
an FBX ready for Roblox Studio's 3D Importer + Accessory Fitting Tool.

Usage:
  /Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup \
      -P tools/rbx_accessory.py -- accessories/cozy_beanie.json [more.json ...]

Modeling frame: 1 Blender unit = 1 stud, the avatar faces -Y, Z is up.
Head accessories are modeled around a 1.2-stud head centered at the origin.
Back accessories are modeled behind a 2x1x2 torso centered at the origin.
"""
import json
import math
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parent.parent
MAX_TRIS = 4000
# Normal body-scale bounding boxes (W x H x D studs) from Roblox's accessory specifications.
LIMITS = {"Hat": (1.87, 2.5, 1.87), "Face": (1.87, 1.25, 1.25), "Back": (9.86, 8.59, 4.87)}
PALETTE_GRID, CELL = 4, 64  # 4x4 swatches of 64px -> 256x256 texture


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def M(loc=(0, 0, 0), rot=(0, 0, 0), scale=(1, 1, 1)):
    r = (Matrix.Rotation(math.radians(rot[2]), 4, "Z") @ Matrix.Rotation(math.radians(rot[1]), 4, "Y")
         @ Matrix.Rotation(math.radians(rot[0]), 4, "X"))
    return Matrix.Translation(loc) @ r @ Matrix.Diagonal((*scale, 1))


class Builder:
    """Collects primitives into one bmesh; each part gets a palette color via UVs."""

    def __init__(self, colors):
        self.bm = bmesh.new()
        self.uv = self.bm.loops.layers.uv.new("UVMap")
        self.colors = colors  # name -> hex
        self.slots = {name: i for i, name in enumerate(colors)}
        if len(self.slots) > PALETTE_GRID ** 2:
            raise ValueError("max 16 colors")

    def slot_uv(self, color):
        """UV coordinate at the center of a color's palette swatch."""
        i = self.slots[color]
        return ((i % PALETTE_GRID) + 0.5) / PALETTE_GRID, 1 - ((i // PALETTE_GRID) + 0.5) / PALETTE_GRID

    def _finish(self, verts, color, smooth):
        faces = {f for v in verts for f in v.link_faces}
        u, v = self.slot_uv(color)
        for f in faces:
            f.smooth = smooth
            for loop in f.loops:
                loop[self.uv].uv = (u, v)

    def sphere(self, color, m, segs=24, rings=12, smooth=True):
        r = bmesh.ops.create_uvsphere(self.bm, u_segments=segs, v_segments=rings, radius=1, matrix=m)
        self._finish(r["verts"], color, smooth)

    def ico(self, color, m, subdiv=2, smooth=True):
        r = bmesh.ops.create_icosphere(self.bm, subdivisions=subdiv, radius=1, matrix=m)
        self._finish(r["verts"], color, smooth)

    def cylinder(self, color, m, segs=24, r1=1, r2=None, depth=1, smooth=False, bevel=0.0):
        r = bmesh.ops.create_cone(self.bm, cap_ends=True, cap_tris=False, segments=segs, radius1=r1,
                                  radius2=r1 if r2 is None else r2, depth=depth, matrix=m)
        verts = r["verts"]
        if bevel:
            inv = m.inverted()
            rims = [e for e in {e for v in verts for e in v.link_edges}
                    if abs((inv @ e.verts[0].co).z - (inv @ e.verts[1].co).z) < 1e-5]
            out = bmesh.ops.bevel(self.bm, geom=rims, offset=bevel, segments=3, profile=0.5,
                                  affect="EDGES", clamp_overlap=True)
            verts = [v for v in verts + list(out.get("verts", [])) if v.is_valid]
        self._finish(verts, color, smooth)

    def box(self, color, m, bevel=0.0, smooth=False):
        r = bmesh.ops.create_cube(self.bm, size=1, matrix=m)
        verts = r["verts"]
        if bevel:
            edges = list({e for v in verts for e in v.link_edges})
            out = bmesh.ops.bevel(self.bm, geom=verts + edges, offset=bevel, segments=2,
                                  profile=0.5, affect="EDGES", clamp_overlap=True)
            verts = verts + list(out.get("verts", []))
            verts = [v for v in verts if v.is_valid]
        self._finish(verts, color, smooth)

    def tube(self, color, m, R, r, segs=32, rsegs=10, arc=360.0, start=0.0, smooth=True, rz=None, power=2.0):
        """Torus (arc=360) or capped arc in the XZ plane. rz/power give an ellipse or a squarer superellipse."""
        rz = R if rz is None else rz
        full = arc >= 360
        n = segs if full else segs + 1

        def path(a):
            c, s_ = math.cos(a), math.sin(a)
            e = 2 / power
            return Vector((R * math.copysign(abs(c) ** e, c), 0, rz * math.copysign(abs(s_) ** e, s_)))

        rings = []
        step = math.radians(arc / segs)
        for i in range(n):
            a = math.radians(start) + step * i
            c = path(a)
            t = (path(a + step * 0.5) - path(a - step * 0.5)).normalized()
            normal = Vector((t.z, 0, -t.x)).normalized()
            rings.append([self.bm.verts.new(m @ (c + normal * (math.cos(b) * r) + Vector((0, math.sin(b) * r, 0))))
                          for b in (2 * math.pi * j / rsegs for j in range(rsegs))])
        new = [v for ring in rings for v in ring]
        for i in range(n if full else n - 1):
            a, b = rings[i], rings[(i + 1) % n]
            for j in range(rsegs):
                k = (j + 1) % rsegs
                self.bm.faces.new((a[k], b[k], b[j], a[j]))
        if not full:
            self.bm.faces.new(rings[0])
            self.bm.faces.new(list(reversed(rings[-1])))
        self._finish(new, color, smooth)

    def band(self, color, m, r_in, r_out, height, segs=32):
        """Closed flat ring (like a crown band)."""
        ring = []
        for i in range(segs):
            a = 2 * math.pi * i / segs
            c, s = math.cos(a), math.sin(a)
            ring.append([self.bm.verts.new(m @ Vector((c * rr, s * rr, z)))
                         for rr, z in ((r_out, -height / 2), (r_out, height / 2), (r_in, height / 2), (r_in, -height / 2))])
        for i in range(segs):
            a, b = ring[i], ring[(i + 1) % segs]
            for j in range(4):
                k = (j + 1) % 4
                self.bm.faces.new((a[j], b[j], b[k], a[k]))
        self._finish([v for q in ring for v in q], color, False)

    def palette_image(self, name):
        size = PALETTE_GRID * CELL
        img = bpy.data.images.new(name, size, size, alpha=False)
        px = [0.0] * (size * size * 4)
        for cname, i in self.slots.items():
            r, g, b = hex_rgb(self.colors[cname])
            cx, cy = (i % PALETTE_GRID) * CELL, size - (i // PALETTE_GRID + 1) * CELL
            for y in range(cy, cy + CELL):
                for x in range(cx, cx + CELL):
                    o = (y * size + x) * 4
                    px[o:o + 4] = (r, g, b, 1.0)
        img.pixels = px
        return img


# --------------------------------------------------------------------------- accessory shapes


def beanie(b, s):
    b.sphere("main", M((0, 0, 0.36), scale=(0.64, 0.64, 0.6)), 28, 14)
    b.cylinder("cuff", M((0, 0, 0.31)), segs=28, r1=0.68, depth=0.26, bevel=0.03)
    if s.get("pompom", True):
        b.ico("pom", M((0, 0, 1.06), scale=(0.18,) * 3), subdiv=2)


def cap(b, s):
    b.sphere("main", M((0, 0, 0.34), scale=(0.64, 0.65, 0.5)), 28, 14)
    b.cylinder("brim", M((0, -0.72, 0.25), rot=(-8, 0, 0), scale=(0.62, 0.42, 1)), segs=28, r1=1, depth=0.045, bevel=0.015)
    b.sphere("button", M((0, 0, 0.84), scale=(0.07, 0.07, 0.04)), 12, 6)
    b.cylinder("band", M((0, 0, 0.24)), segs=28, r1=0.645, depth=0.1)


def crown(b, s):
    b.band("gold", M((0, 0, 0.55)), 0.55, 0.62, 0.24, segs=32)
    b.sphere("velvet", M((0, 0, 0.6), scale=(0.56, 0.56, 0.22)), 24, 10)
    n = int(s.get("spikes", 8))
    for i in range(n):
        a = 2 * math.pi * i / n
        x, y = math.cos(a) * 0.585, math.sin(a) * 0.585
        b.cylinder("gold", M((x, y, 0.81)), segs=6, r1=0.1, r2=0.0, depth=0.28)
        b.ico("gold", M((x, y, 0.97), scale=(0.045,) * 3), subdiv=1)
    for i, a in enumerate((270, 0, 90, 180)):
        r = math.radians(a)
        b.ico("gem" if i % 2 == 0 else "gem2", M((math.cos(r) * 0.62, math.sin(r) * 0.62, 0.55), scale=(0.07, 0.04, 0.07),
                                                 rot=(0, 0, a + 90)), subdiv=1)


def headphones(b, s):
    b.tube("main", M((0, 0, -0.05)), R=0.7, rz=0.76, power=4, r=0.05, segs=40, rsegs=10, arc=186, start=-3)
    b.sphere("cushion", M((0, 0, 0.71), scale=(0.28, 0.09, 0.05)), 16, 8)
    for side in (-1, 1):
        b.cylinder("cup", M((side * 0.7, 0, -0.05), rot=(0, 90, 0)), segs=24, r1=0.25, depth=0.16, bevel=0.03)
        b.cylinder("cushion", M((side * 0.6, 0, -0.05), rot=(0, 90, 0)), segs=24, r1=0.21, depth=0.07, smooth=True)
        b.cylinder("accent", M((side * 0.785, 0, -0.05), rot=(0, 90, 0)), segs=24, r1=0.16, depth=0.02)


def sunglasses(b, s):
    for side in (-1, 1):
        x = side * 0.25
        b.cylinder("lens", M((x, -0.64, 0.08), rot=(90, 0, 0), scale=(1.18, 1, 1)), segs=24, r1=0.19, depth=0.03)
        b.tube("frame", M((x, -0.64, 0.08), scale=(1.18, 1, 1)), R=0.2, r=0.025, segs=24, rsegs=6)
        b.box("frame", M((side * 0.62, -0.17, 0.14), scale=(0.035, 0.9, 0.05)))
    b.cylinder("frame", M((0, -0.64, 0.16), rot=(0, 90, 0)), segs=8, r1=0.025, depth=0.12)


def wings(b, s):
    n = int(s.get("feathers", 7))
    for side in (-1, 1):
        b.sphere("main", M((side * 0.32, 0.62, 0.35), scale=(0.16, 0.08, 0.16)), 12, 6)
        for k in range(n):
            t = k / max(1, n - 1)
            ang = 15 + 55 * t
            length = 1.1 + 0.9 * (1 - abs(t - 0.6))
            r = math.radians(ang)
            cx = side * (0.3 + math.cos(r) * length * 0.55)
            cz = 0.35 + math.sin(r) * length * 0.55
            b.sphere("main" if k % 2 == 0 else "accent",
                     M((cx, 0.62 + 0.02 * k, cz), rot=(0, -side * ang, 0), scale=(length * 0.55, 0.04, 0.19)), 14, 7)


def backpack(b, s):
    b.box("main", M((0, 0.8, -0.05), scale=(1.3, 0.5, 1.5)), bevel=0.1)
    b.box("accent", M((0, 0.8, 0.62), scale=(1.34, 0.54, 0.32)), bevel=0.08)
    b.box("pocket", M((0, 1.1, -0.35), scale=(0.95, 0.16, 0.6)), bevel=0.06)
    b.box("accent", M((0, 1.12, -0.06), scale=(0.97, 0.18, 0.06)))
    b.tube("accent", M((0, 0.8, 0.78)), R=0.2, r=0.035, segs=14, rsegs=6, arc=180)
    for side in (-1, 1):
        b.box("strap", M((side * 0.45, 0.05, 1.03), scale=(0.2, 1.2, 0.06)))


SHAPES = {
    "beanie": (beanie, "Hat", {"main": "#1f2433", "cuff": "#2b3247", "pom": "#ffd23f"}),
    "cap": (cap, "Hat", {"main": "#d62839", "brim": "#1b1b1b", "button": "#1b1b1b", "band": "#b11f2e"}),
    "crown": (crown, "Hat", {"gold": "#f2c14e", "velvet": "#7b1e3a", "gem": "#3a86ff", "gem2": "#e63946"}),
    "headphones": (headphones, "Hat", {"main": "#222222", "cup": "#2d2d2d", "cushion": "#111111", "accent": "#ff5d8f"}),
    "sunglasses": (sunglasses, "Face", {"lens": "#141414", "frame": "#f2f2f2"}),
    "wings": (wings, "Back", {"main": "#ffffff", "accent": "#dfe7ff"}),
    "backpack": (backpack, "Back", {"main": "#3a86ff", "accent": "#1d3557", "pocket": "#4f97ff", "strap": "#1d3557"}),
}

# --------------------------------------------------------------------------- scene helpers


def clear_scene():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.images, bpy.data.cameras, bpy.data.lights):
        for d in list(coll):
            coll.remove(d)


def textured_material(name, img):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Roughness"].default_value = 0.8
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    tex.interpolation = "Closest"
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    return mat


def make_object(name, bm, mat):
    me = bpy.data.meshes.new(name)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)  # every face points outward
    bm.normal_update()
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    me.materials.append(mat)
    return ob


def reference_body(kind):
    """Gray stand-in head/torso for previews only (never exported)."""
    b = Builder({"skin": "#c9c9c9"})
    if kind == "Back":
        b.box("skin", M((0, 0, 0), scale=(2, 1, 2)), bevel=0.05)
        b.cylinder("skin", M((0, 0, 1.6)), segs=32, r1=0.6, depth=1.2, bevel=0.2, smooth=True)
    else:
        b.cylinder("skin", M((0, 0, 0)), segs=32, r1=0.6, depth=1.2, bevel=0.2, smooth=True)
        b.box("skin", M((0, 0, -1.6), scale=(2, 1, 2)), bevel=0.05)
    img = b.palette_image("ref_tex")
    return make_object("REFERENCE", b.bm, textured_material("ref_mat", img))


def render(path, target, view):
    sc = bpy.context.scene
    try:
        sc.render.engine = "BLENDER_WORKBENCH"
        sc.display.shading.light = "STUDIO"
        sc.display.shading.color_type = "TEXTURE"
        sc.display.shading.show_cavity = True
    except TypeError:
        sc.render.engine = "BLENDER_EEVEE"
    sc.render.resolution_x = sc.render.resolution_y = 700
    sc.render.film_transparent = False
    world = bpy.data.worlds.get("World") or bpy.data.worlds.new("World")
    sc.world = world
    world.color = (0.93, 0.94, 0.95)
    cam_data = bpy.data.cameras.new("cam")
    cam_data.type = "ORTHO"
    cam = bpy.data.objects.new("cam", cam_data)
    sc.collection.objects.link(cam)
    sc.camera = cam
    corners = [target.matrix_world @ Vector(c) for c in target.bound_box]
    center = sum(corners, Vector()) / 8
    size = max((max(c[i] for c in corners) - min(c[i] for c in corners)) for i in range(3))
    yaw = {"front": -30, "back": 150, "side": 90}[view]
    r = math.radians(yaw)
    direction = Vector((math.sin(r), -math.cos(r), 0.45)).normalized()
    cam.location = center + direction * 20
    cam.rotation_euler = (-direction).to_track_quat("-Z", "Y").to_euler()
    cam_data.ortho_scale = max(size * 1.5, 2.6)
    sc.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    bpy.data.objects.remove(cam, do_unlink=True)


# --------------------------------------------------------------------------- main


def build(path):
    spec = json.loads(Path(path).read_text())
    kind = spec["type"]
    fn, asset_type, default_colors = SHAPES[kind]
    name = spec.get("name", Path(path).stem)
    out = ROOT / "output" / "accessories" / name
    out.mkdir(parents=True, exist_ok=True)

    clear_scene()
    sc = bpy.context.scene
    sc.unit_settings.system = "METRIC"
    sc.unit_settings.scale_length = 0.01  # Roblox: 1 Blender unit = 1 stud with FBX Unit Scale

    colors = {**default_colors, **spec.get("colors", {})}
    b = Builder(colors)
    fn(b, spec)
    img = b.palette_image(f"{name}_texture")
    img.filepath_raw = str(out / "texture.png")
    img.file_format = "PNG"
    img.save()
    open_edges = sum(1 for e in b.bm.edges if len(e.link_faces) != 2)
    ob = make_object(name, b.bm, textured_material(f"{name}_mat", img))

    tris = sum(len(p.vertices) - 2 for p in ob.data.polygons)
    dims = ob.dimensions  # x=width, y=depth, z=height in this frame
    w, h, d = dims.x, dims.z, dims.y
    lw, lh, ld = LIMITS[asset_type]
    problems = []
    if tris > MAX_TRIS:
        problems.append(f"too many triangles ({tris} > {MAX_TRIS})")
    if open_edges:
        problems.append(f"{open_edges} open edges (mesh not watertight)")
    if w > lw or h > lh or d > ld:
        problems.append(f"bigger than {asset_type} box {lw}x{lh}x{ld} (scale it down in the Fitting Tool)")

    for o in bpy.data.objects:
        o.select_set(o == ob)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.export_scene.fbx(
        filepath=str(out / f"{name}.fbx"), use_selection=True, object_types={"MESH"},
        apply_scale_options="FBX_SCALE_UNITS", path_mode="COPY", embed_textures=True,
        mesh_smooth_type="FACE", add_leaf_bones=False, bake_anim=False,
    )

    ref = reference_body(asset_type)
    for view in ("front", "back") if asset_type == "Back" else ("front", "side"):
        render(out / f"preview_{view}.png", ob if asset_type != "Back" else ref, view)
    bpy.data.objects.remove(ref, do_unlink=True)

    status = "OK" if not problems else "CHECK: " + "; ".join(problems)
    print(f"[rbx] {name}: {asset_type}, {tris} tris, {w:.2f}x{h:.2f}x{d:.2f} studs -> {out}  [{status}]")


def main():
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    files = args or sorted(str(p) for p in (ROOT / "accessories").glob("*.json"))
    for f in files:
        build(f)


if __name__ == "__main__":  # Blender's -P runs scripts as __main__; rbx_convert.py imports this file
    main()
