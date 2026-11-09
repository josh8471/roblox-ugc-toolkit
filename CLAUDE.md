# CLAUDE.md — roblox-ugc-toolkit

## What this project is
Tools for making Roblox avatar items that people can wear and sell:
- **Classic 2D clothing**: Shirt, Pants, T-Shirt (PNG images)
- **3D rigid accessories**: hats, face items, back items (FBX meshes)
- **A converter** that turns outside 3D models (GLB/FBX/OBJ) into Roblox-ready accessories

Each item is described in a JSON file, and the generators build the files to upload. Personal, machine-specific notes (if any) live in `CLAUDE.local.md`, which is gitignored. Never move personal account details into this file, because the repo is public.

Every file is small, so read files directly.

## Layout
```
designs/*.json        outfit specs (shirt / pants / tshirt), read by tools/rbx_clothing.py
accessories/*.json    accessory specs, read by tools/rbx_accessory.py
output/<name>/        shirt.png, pants.png, tshirt.png, preview.png   (generated, gitignored; never hand-edit)
output/accessories/<name>/  <name>.fbx, texture.png, preview_*.png  (generated, gitignored)
models/               3D models the user brings (GLB/FBX/OBJ); gitignored because their licenses are unknown
templates/            official Roblox R15 Shirt/Pants templates (585x559, gitignored; README explains the download)
docs/images/          README screenshots (committed)
tools/rbx_clothing.py   Python 3 + Pillow (tested: Python 3.14, Pillow 12.2; needs Pillow ≥ 10.1)
tools/rbx_accessory.py  runs INSIDE Blender (bpy/bmesh only, no Pillow); main() is guarded so other scripts can import it
tools/rbx_convert.py    runs INSIDE Blender: turns any outside model into a Roblox-ready accessory FBX (imports rbx_accessory)
README.md             public docs: setup, full JSON option list, upload steps (update it when you add options)
```

## Commands
```bash
python3 tools/rbx_clothing.py                       # build all designs/
python3 tools/rbx_clothing.py designs/x.json        # one design
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup -P tools/rbx_accessory.py            # all
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup -P tools/rbx_accessory.py -- accessories/x.json
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup -P tools/rbx_convert.py -- models/x.glb --name x --type Hat [--width 1.6] [--rotate 180] [--budget 3800]
```
The tools were tested with Blender 5.2.3 on macOS. On macOS, Blender is usually **not on PATH**, so use the full path above; on Windows or Linux, use the `blender` executable. To cut Blender's noise, pipe its output through `grep -E "^\[rbx\]|Traceback|Error"`.

## How to verify changes (do this every time)
- **Clothing:** rebuild, then open `output/<name>/preview.png` with Read. It shows FRONT, BACK and SIDE views. To check template alignment, alpha-composite the output over `templates/Template-*.png`. Only the hands and foot bottoms should show template color.
- **Accessories:** rebuild, then check that the `[rbx] ...` line ends in `[OK]`. Open the `preview_*.png` renders, which show the item on a gray stand-in body. With several items, paste the previews into one contact sheet in a temporary folder.
- Every Roblox upload costs Robux. Never tell the user an item is ready without looking at its preview first.

## tools/rbx_clothing.py: key facts
- `BOXES[part][face] = (x, y, w, h)`, measured pixel-exact from the official template.
  - Parts: `torso`, `right` (right arm or leg, left half of the image), `left`.
  - Faces: `U F D R L B`.
  - The same boxes serve shirts (arms) and pants (legs).
- **Orientation**
  - The torso `R` face is the character's right; it sits to the left of FRONT on the template.
  - Outer limb faces: `OUTER = {"right": "R", "left": "L"}`. Stripes, cargo pockets and shoe accents go on these.
- **Guide lines:** shoes and gloves must stay below limb y=52 (`LIMB_GUIDE`), so `shoe_height` is at most 76. The torso guide line is at y=96.
- `Sheet` gives every face its own small canvas (`sh.face(part, f)`), so drawing is clipped to that face. `render()` pastes the faces into the 585x559 image.
- **Transparent pixels show skin.** That is how short sleeves, crop tops (`length`), shorts (`leg_length`), the V-neck and ripped holes work.
  - `masked()` composites a layer without painting over transparent pixels. Use it for any overlay: patterns, shading, wrinkles.
- **Build flow:**
  - `build_shirt` / `build_pants` merge `SHIRT_PRESETS` / `PANTS_PRESETS[type]` with the spec; the spec wins.
  - Drawing order: base gradient → pattern → details (hood, pocket, zipper, text) → sleeves → `apply_shading` + `WRINKLES` last.
  - `build_tshirt` makes a 512x512 decal.
- **Graphics:**
  - `graphic(im, s, prefix, top, bottom)` draws `<prefix>_text` / `<prefix>_icon`.
  - Icons live in `draw_icon`: star, heart, bolt, diamond, smile.
  - Patterns live in `pattern()`: dots, checker, hstripes, vstripes, plaid, camo.
  - Fonts fall back to the macOS Arial Black / Arial Bold paths, then Windows and Linux fonts.
- **Preview:**
  - Torso = skin, then pants, then shirt on top. Arms come from the shirt only; legs from the pants only.
  - Front view: right arm `F` on the viewer's left. Back view: left arm `B` on the viewer's left. Side view: the character's right side.
- **To add a feature:** add a spec key (and a preset default if needed), draw it in the build function before the shading pass, and document it in README.md.

## tools/rbx_accessory.py: key facts
- **Modeling frame:** 1 Blender unit = 1 stud, the avatar faces **−Y**, Z is up.
  - Head items fit a rounded-cylinder head: radius 0.6, height 1.2, centered at the origin, so its top is z=0.6. The head is a cylinder, not a sphere, so arcs over it must clear its corners. The headphone band uses `tube(..., power=4)`, a superellipse, for this reason.
  - Back items sit behind a 2×1×2 torso centered at the origin; its back surface is y=+0.5.
- **`Builder`** puts every primitive into **one bmesh**, which gives one mesh.
  - Primitives: `sphere`, `ico`, `cylinder(bevel=)`, `box(bevel=)`, `tube` (torus or capped arc; `rz`/`power` for ellipse or superellipse), `band` (flat ring).
  - Coloring uses a **palette texture**. Each named color gets a 64px swatch in a 4×4 grid (a 256px texture, max 16 colors), and the UVs of a part's faces point at its swatch (`slot_uv`).
- **`SHAPES[type] = (fn, asset_type, default_colors)`.** To add an accessory, write `fn(b, spec)` and register it here. The asset type must be a key in `LIMITS` (Hat, Face or Back so far; add the size box when adding a type).
- **Export settings** follow Roblox's docs:
  - Scene `unit_settings.scale_length = 0.01`.
  - FBX `apply_scale_options="FBX_SCALE_UNITS"`, `path_mode="COPY"`, `embed_textures=True`, `add_leaf_bones=False`, `bake_anim=False`.
  - `recalc_face_normals` runs before `to_mesh`. Roblox hides back-facing faces, so flipped normals would make faces invisible.
  - A round-trip test confirmed that re-importing the FBX gives the correct stud dimensions with the texture packed.
- **Automatic checks** in the `[rbx]` line: at most 4,000 triangles, 0 open edges (watertight), and the bounding box within the Normal-scale `LIMITS`.
- **Previews:** Workbench render with an orthographic camera and a gray `REFERENCE` body that is never exported. Blender's engine enum lists only EEVEE, but Workbench still works.

## tools/rbx_convert.py: key facts (outside models)
- **Pipeline:**
  1. Import the model (glTF/FBX/OBJ).
  2. `weld`: bake each piece to world space, merge doubles at 1e-5, fill holes, and make pieces with more than 100 triangles smooth (`DENSE`).
  3. `decimate`: shrink only the pieces with more than 100 triangles, so boxes and panels stay exact. The ratio is (budget − low-poly triangles) / high-poly triangles.
  4. `join` into one mesh.
  5. Scale to 90% of the `LIMITS` box, or use `--width`. Hats are placed at `--seat` above the head center.
  6. Each material color becomes a swatch in one palette texture, via `ra.Builder.slot_uv`. Base Color is converted from linear to sRGB. An image texture is reduced to its average color.
  7. Recalculate normals, check, export FBX, render previews.
- **Gotchas:**
  - Blender's glTF import respects the scene unit scale of 0.01, so imports come in 100× larger. That's harmless because scaling is relative.
  - After editing `me` directly, `ob.dimensions` is stale until `view_layer.update()`. Measure from the vertices instead.
  - Three.js exports face −Y after import (glTF +Z becomes Blender −Y). That's correct for Roblox, so no `--rotate` is needed.
- **Tested on (2026-11-09):** a 60-piece, 15,924-triangle Three.js GLB hat became 1 mesh with 3,776 triangles and 5 colors, and the preview looked the same as the original.

## Roblox facts (checked on create.roblox.com on 2026-11-09; re-check if stale)
- **Accessory fees** ([fees page](https://create.roblox.com/docs/marketplace/marketplace-fees-and-commissions)):
  - Upload fee: 80 Robux per submission (500 with an emissive mask). It isn't refunded if moderation rejects the item.
  - Publishing advance for a non-limited **Hat: 1500 Robux**. It's refundable over time, earned back as rebates from Roblox's commission. A paid limited costs 13000.
  - The creator gets **30%** of each Marketplace sale. The share rises as the price goes above the price floor (1.3× the floor gives 37%, 10× gives 70%). In-game sales give creator 30% plus game owner 40%. The price floor appears in the Creator Dashboard.
- **Upload flow:**
  1. Explorer → right-click the Accessory → **Save to Roblox** → Submit As **Avatar Asset** → Asset type (Hat). Validation runs here.
  2. Enter the Title and Creator → **Submit**. This pays the upload fee and sends the item to moderation.
  3. In the Creator Hub, open Creations → the item (pre-published) → **Manage Item** → Pricing: "Amount Above Price Floor" → **Publish Item**. This pays the advance.
- The docs don't say whether a creator automatically owns their own item. The fallback is buying it.
- Classic Shirt and Pants: 585×559 PNG, 8-bit alpha. T-Shirt: square image (512×512).
- **Upload:** ID verification and **80 Robux** per upload (Creator Dashboard → Avatar Items → Classics).
- **Publish and sell:** 2-step verification, **Roblox Plus or Premium 1000/2200** (Premium 400 is not enough), and a **10–2500 Robux publishing advance**. The membership must stay active for items to stay on sale. Every Roblox Plus tier, including the one with no Robux, allows publishing.
- A game's "Kids & Select publishing fee" is a separate, game-only fee. It doesn't count toward avatar items.
- **Rigid accessory rules:**
  - Single mesh, at most 4,000 triangles, watertight, textures up to 2048px.
  - Material Plastic, Transparency 0, no extra objects.
- **Size boxes for Normal scale** (W×H×D studs): Hat 1.87×2.5×1.87, Face 1.87×1.25×1.25, Back 9.86×8.59×4.87.
- **Head items must touch the head.** Floating items such as halos break the Marketplace policy.
- **Studio steps (Studio, Nov 2026):** Home tab or File menu → Import 3D (Rthro rig scale = Normal) → Avatar tab → **Accessory** button (the Accessory Fitting Tool, which adds the attachment) → **UGC Validation** button to check the item before paying to upload. The Avatar tab also has a **Character** button for adding a test rig.
- **No brand logos, other games' characters or real people in designs.** Roblox removes them, and the creator loses the fees.

## Findings from real Studio tests
- 2026-11-09, night_beanie: the FBX imported correctly. The texture showed and the hat was upright (+Z in Blender became up). On the Normal mannequin in the Accessory Fitting Tool, though, the default placement was **too low and too big**: the beanie covered the whole head and face. It needed scaling to about 80–85% and moving up by hand. Once a good fit is confirmed, consider shrinking the head items about 15% and raising them in `rbx_accessory.py` (the 1.2-stud head stand-in may be larger than the Normal head).

## Not verified yet
- No accessory has been uploaded or published end to end yet.
- It is unknown whether a creator owns their uploaded item automatically or must buy it to wear it. The fallback is to put it on sale and buy it.
