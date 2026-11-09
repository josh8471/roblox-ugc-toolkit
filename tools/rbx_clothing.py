#!/usr/bin/env python3
"""
Roblox classic clothing generator.

Turns a small JSON design file into upload-ready Roblox images:
  shirt.png   585x559 Classic Shirt (R15 template)
  pants.png   585x559 Classic Pants (R15 template)
  tshirt.png  512x512 Classic T-Shirt (optional)
  preview.png front/back mannequin preview so you can check before paying to upload

Usage:
  python3 tools/rbx_clothing.py                 # build every design in designs/
  python3 tools/rbx_clothing.py designs/x.json  # build one design

Transparent pixels show the avatar's skin, which is how short sleeves,
crop tops, shorts and ripped jeans work.
"""
import argparse
import json
import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
W, H = 585, 559

# (x, y, w, h) boxes measured from the official Roblox R15 templates in templates/.
BOXES = {
    "torso": {"U": (231, 8, 128, 64), "F": (231, 74, 128, 128), "D": (231, 204, 128, 64),
              "R": (165, 74, 64, 128), "L": (361, 74, 64, 128), "B": (427, 74, 128, 128)},
    # Right arm (shirt) / right leg (pants): left half of the template.
    "right": {"U": (217, 289, 64, 64), "L": (19, 355, 64, 128), "B": (85, 355, 64, 128),
              "R": (151, 355, 64, 128), "F": (217, 355, 64, 128), "D": (217, 485, 64, 64)},
    # Left arm (shirt) / left leg (pants): right half of the template.
    "left": {"U": (308, 289, 64, 64), "F": (308, 355, 64, 128), "L": (374, 355, 64, 128),
             "B": (440, 355, 64, 128), "R": (506, 355, 64, 128), "D": (308, 485, 64, 64)},
}
SIDES = ("F", "L", "B", "R")
OUTER = {"right": "R", "left": "L"}  # face pointing away from the body
LIMB_GUIDE = 52  # dashed line on limbs: gloves/shoes must stay below this (R15)

FONT_PATHS = [
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "C:/Windows/Fonts/ariblk.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

# --------------------------------------------------------------------------- colors


def hexc(c, a=255):
    if c is None:
        return None
    if isinstance(c, (list, tuple)):
        return tuple(c) if len(c) == 4 else (*c, a)
    c = c.lstrip("#")
    if len(c) == 8:
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4, 6))
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), a)


def mix(c1, c2, t):
    return tuple(round(a + (b - a) * t) for a, b in zip(c1, c2))


def shade(c, f):
    """f < 1 darkens, f > 1 lightens toward white."""
    r, g, b, a = c
    if f <= 1:
        return (int(r * f), int(g * f), int(b * f), a)
    t = f - 1
    return (int(r + (255 - r) * t), int(g + (255 - g) * t), int(b + (255 - b) * t), a)


def color(spec, key, default):
    v = spec.get(key)
    return hexc(v) if v else default


# --------------------------------------------------------------------------- canvas


class Sheet:
    """One template: every face is its own small canvas, so drawing is clipped per face."""

    def __init__(self):
        self.faces = {}

    def face(self, part, f):
        key = (part, f)
        if key not in self.faces:
            _, _, w, h = BOXES[part][f]
            self.faces[key] = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        return self.faces[key]

    def render(self):
        out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        for (part, f), im in self.faces.items():
            x, y, _, _ = BOXES[part][f]
            out.alpha_composite(im, (x, y))
        return out


def rect(im, fill, x0=0, y0=0, x1=None, y1=None, outline=None, width=1):
    w, h = im.size
    x1 = w if x1 is None else x1
    y1 = h if y1 is None else y1
    if x1 <= x0 or y1 <= y0:
        return
    ImageDraw.Draw(im).rectangle([x0, y0, x1 - 1, y1 - 1], fill=fill, outline=outline, width=width)


def gradient(im, c1, c2, y0, y1):
    d = ImageDraw.Draw(im)
    w = im.size[0]
    span = max(1, y1 - y0 - 1)
    for y in range(y0, y1):
        d.line([(0, y), (w - 1, y)], fill=mix(c1, c2, (y - y0) / span))


def masked(im, layer):
    """Composite layer onto im without painting over transparent (skin) pixels."""
    la = ImageChops.multiply(layer.getchannel("A"), im.getchannel("A"))
    layer = layer.copy()
    layer.putalpha(la)
    im.alpha_composite(layer)


def band(im, y0, y1, fill):
    rect(im, fill, 0, y0, None, y1)
    ImageDraw.Draw(im).line([(0, y0), (im.size[0] - 1, y0)], fill=shade(fill, 0.8))


def stripes(im, cfg, y0, y1):
    if not cfg or y1 <= y0:
        return
    n, sw, gap = int(cfg.get("count", 2)), int(cfg.get("width", 4)), int(cfg.get("gap", 3))
    total = n * sw + (n - 1) * gap
    x = (im.size[0] - total) // 2
    for _ in range(n):
        rect(im, hexc(cfg.get("color", "#ffffff")), x, y0, x + sw, y1)
        x += sw + gap


def pattern(im, p, y0, y1, seed=0):
    if not p or y1 <= y0:
        return
    w, h = im.size
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    kind = p.get("type")
    col = hexc(p.get("color", "#ffffff"))
    sz = max(2, int(p.get("size", 12)))
    if kind == "hstripes":
        for y in range(y0, y1, sz * 2):
            d.rectangle([0, y, w - 1, y + sz - 1], fill=col)
    elif kind == "vstripes":
        for x in range(0, w, sz * 2):
            d.rectangle([x, y0, x + sz - 1, y1 - 1], fill=col)
    elif kind == "checker":
        for yy in range(y0, y1, sz):
            for xx in range(0, w, sz):
                if (xx // sz + (yy - y0) // sz) % 2 == 0:
                    d.rectangle([xx, yy, xx + sz - 1, yy + sz - 1], fill=col)
    elif kind == "dots":
        r = max(1, sz // 4)
        for j, yy in enumerate(range(y0 + sz // 2, y1, sz)):
            for xx in range(sz // 2 if j % 2 else 0, w + sz, sz):
                d.ellipse([xx - r, yy - r, xx + r, yy + r], fill=col)
    elif kind == "plaid":
        thick = (*col[:3], 110)
        thin = (*col[:3], 170)
        for v in range(0, max(w, h), sz):
            d.rectangle([v, 0, v + sz // 3, h], fill=thick)
            d.rectangle([0, v, w, v + sz // 3], fill=thick)
            d.line([(v + sz // 2, 0), (v + sz // 2, h)], fill=thin)
            d.line([(0, v + sz // 2), (w, v + sz // 2)], fill=thin)
    elif kind == "camo":
        rnd = random.Random(seed)
        cols = [hexc(c) for c in p.get("colors", ["#4b5320", "#2e3b1f", "#7a6a4f"])]
        for _ in range(int(w * (y1 - y0) / 60)):
            cx, cy = rnd.randint(-4, w + 4), rnd.randint(y0 - 4, y1 + 4)
            rx, ry = rnd.randint(4, sz), rnd.randint(3, max(4, sz * 2 // 3))
            d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=rnd.choice(cols))
    else:
        raise ValueError(f"unknown pattern type: {kind}")
    if y0 > 0:
        d.rectangle([0, 0, w, y0 - 1], fill=(0, 0, 0, 0))
    if y1 < h:
        d.rectangle([0, y1, w, h], fill=(0, 0, 0, 0))
    masked(im, layer)


def apply_shading(im, strength=0.28, wrinkles=()):
    """Darken face edges (rounded look) and add soft fabric folds."""
    w, h = im.size
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for x in range(w):
        t = abs((x + 0.5) / w - 0.5) * 2
        a = int(255 * strength * t ** 2.5)
        if a:
            d.line([(x, 0), (x, h - 1)], fill=(0, 0, 0, a))
    masked(im, layer)
    if wrinkles:
        dark = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        light = Image.new("RGBA", (w, h), (255, 255, 255, 0))
        dd, ld = ImageDraw.Draw(dark), ImageDraw.Draw(light)
        for x0, y0, x1, y1 in wrinkles:
            dd.line([(x0, y0), (x1, y1)], fill=(0, 0, 0, 95), width=2)
            ld.line([(x0, y0 + 2), (x1, y1 + 2)], fill=(255, 255, 255, 50), width=1)
        masked(im, dark.filter(ImageFilter.GaussianBlur(1.1)))
        masked(im, light.filter(ImageFilter.GaussianBlur(0.7)))


WRINKLES = {
    "torso": [(2, 18, 20, 30), (126, 18, 108, 30), (30, 104, 48, 100), (80, 100, 98, 104)],
    "arm": [(8, 56, 28, 62), (36, 62, 56, 57), (14, 70, 30, 73), (10, 20, 24, 24)],
    "leg": [(10, 56, 30, 60), (34, 60, 54, 56), (16, 22, 30, 26), (36, 90, 50, 93)],
}

# --------------------------------------------------------------------------- graphics


def load_font(size, path=None):
    for p in ([path] if path else []) + FONT_PATHS:
        try:
            return ImageFont.truetype(p, size)
        except (OSError, ValueError):
            continue
    return ImageFont.load_default(size=size)


def text_fit(im, text, cx, cy, max_w, max_h, fill, outline=None, font_path=None):
    if not text:
        return
    d = ImageDraw.Draw(im)
    sw = 1 if outline else 0
    for size in range(int(max_h), 5, -1):
        f = load_font(size, font_path)
        l, t, r, b = d.multiline_textbbox((0, 0), text, font=f, stroke_width=sw, align="center", spacing=1)
        if r - l <= max_w and b - t <= max_h:
            break
    d.multiline_text((cx - (r - l) / 2 - l, cy - (b - t) / 2 - t), text, font=f, fill=fill,
                     stroke_width=sw, stroke_fill=outline, align="center", spacing=1)


def draw_icon(im, kind, cx, cy, r, fill, outline=None):
    if not kind:
        return
    d = ImageDraw.Draw(im)
    ow = 2 if outline else 0
    if kind == "star":
        pts = [(cx + (r if i % 2 == 0 else r * 0.45) * math.cos(-math.pi / 2 + i * math.pi / 5),
                cy + (r if i % 2 == 0 else r * 0.45) * math.sin(-math.pi / 2 + i * math.pi / 5)) for i in range(10)]
        d.polygon(pts, fill=fill, outline=outline, width=ow)
    elif kind == "heart":
        s = r / 16
        pts = []
        for i in range(64):
            t = i / 64 * 2 * math.pi
            x = 16 * math.sin(t) ** 3
            y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
            pts.append((cx + x * s, cy - (y + 2.5) * s))
        d.polygon(pts, fill=fill, outline=outline, width=ow)
    elif kind == "bolt":
        shape = [(0.25, -1.0), (-0.55, 0.12), (-0.05, 0.12), (-0.3, 1.0), (0.55, -0.18), (0.05, -0.18), (0.45, -1.0)]
        d.polygon([(cx + x * r, cy + y * r) for x, y in shape], fill=fill, outline=outline, width=ow)
    elif kind == "diamond":
        d.polygon([(cx, cy - r), (cx + r * 0.7, cy), (cx, cy + r), (cx - r * 0.7, cy)],
                  fill=fill, outline=outline, width=ow)
    elif kind == "smile":
        ink = outline or (20, 20, 20, 255)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill, outline=ink, width=max(1, int(r / 8)))
        e = max(1, r * 0.13)
        for ex in (-0.35, 0.35):
            d.ellipse([cx + ex * r - e, cy - 0.3 * r - e * 1.4, cx + ex * r + e, cy - 0.3 * r + e * 1.4], fill=ink)
        d.arc([cx - 0.55 * r, cy - 0.45 * r, cx + 0.55 * r, cy + 0.6 * r], 20, 160, fill=ink, width=max(1, int(r / 7)))
    else:
        raise ValueError(f"unknown icon: {kind}")


def graphic(im, s, prefix, top, bottom, cx=None, max_w=96):
    """Draw <prefix>_icon and/or <prefix>_text stacked inside [top, bottom]."""
    txt, ico = s.get(f"{prefix}_text"), s.get(f"{prefix}_icon")
    if not txt and not ico:
        return
    cx = im.size[0] / 2 if cx is None else cx
    tcol = color(s, "text_color", (255, 255, 255, 255))
    tout = color(s, "text_outline", None)
    icol = color(s, "icon_color", tcol)
    iout = color(s, "icon_outline", None)
    h = bottom - top
    if txt and ico:
        r = min(h * 0.24, max_w / 2)
        draw_icon(im, ico, cx, top + h * 0.3, r, icol, iout)
        text_fit(im, txt, cx, top + h * 0.78, max_w, h * 0.3, tcol, tout, s.get("font"))
    elif ico:
        draw_icon(im, ico, cx, top + h / 2, min(h * 0.42, max_w / 2), icol, iout)
    else:
        text_fit(im, txt, cx, top + h / 2, max_w, min(h * 0.55, 40), tcol, tout, s.get("font"))


# --------------------------------------------------------------------------- shirt

SHIRT_PRESETS = {
    "tee": {"sleeves": "short", "neck": "crew"},
    "longsleeve": {"sleeves": "long", "cuffs": True, "neck": "crew"},
    "hoodie": {"sleeves": "long", "cuffs": True, "hood": True, "pocket": True, "drawstrings": True},
    "jacket": {"sleeves": "long", "cuffs": True, "zipper": True, "collar": True, "side_pockets": True},
    "tank": {"sleeves": "none", "neck": "crew"},
}


def build_shirt(spec):
    s = {**SHIRT_PRESETS.get(spec.get("type", "tee"), {}), **spec}
    base = color(s, "color", hexc("#2b2d42"))
    base2 = color(s, "color2", shade(base, 0.82))
    trim = color(s, "trim", shade(base, 0.72))
    length = int(s.get("length", 128))  # 128 = full, ~90 = crop top
    pat = s.get("pattern")
    sh = Sheet()

    # torso body
    for i, f in enumerate(SIDES + ("U",)):
        im = sh.face("torso", f)
        if f == "U":
            rect(im, base)
            continue
        gradient(im, base, base2, 0, length)
        pattern(im, pat, 0, length, seed=i)
        if s.get("hem", True):
            band(im, length - 9, length, trim)
    if length >= 128:
        rect(sh.face("torso", "D"), shade(base2, 0.9))

    F, B = sh.face("torso", "F"), sh.face("torso", "B")
    dF, dB = ImageDraw.Draw(F), ImageDraw.Draw(B)

    # neckline
    neck = s.get("neck")
    if neck == "crew":
        dF.ellipse([42, -16, 86, 10], outline=trim, width=4)
        dB.ellipse([46, -10, 82, 6], outline=trim, width=3)
    elif neck == "v":
        dF.polygon([(48, 0), (80, 0), (64, 22)], fill=(0, 0, 0, 0))
        dF.line([(46, 0), (64, 24), (82, 0)], fill=trim, width=3)

    # graphic area
    pocket_top = 80
    if s.get("zipper"):
        graphic(F, s, "front", 22, 50, cx=97, max_w=40)
    elif s.get("pocket") and length >= 110:
        graphic(F, s, "front", 16, pocket_top - 4)
    else:
        graphic(F, s, "front", 16, length - 14)

    # hoodie
    if s.get("hood"):
        hood = shade(base, 0.88)
        dB.rounded_rectangle([14, 0, 114, 46], radius=18, fill=hood, outline=shade(base, 0.65), width=2)
        dB.line([(64, 0), (64, 44)], fill=shade(base, 0.7), width=1)
        rect(sh.face("torso", "U"), hood)
        dF.arc([30, -34, 98, 20], 0, 180, fill=shade(base, 0.68), width=6)
    if s.get("pocket") and length >= 110:
        pk = [(36, pocket_top), (92, pocket_top), (108, length - 10), (20, length - 10)]
        dF.polygon(pk, fill=shade(base, 0.94), outline=shade(base, 0.68), width=2)
        dF.line([pk[0], pk[3]], fill=shade(base, 0.55), width=3)
        dF.line([pk[1], pk[2]], fill=shade(base, 0.55), width=3)
    if s.get("drawstrings"):
        sc = color(s, "string_color", hexc("#f2f2f2"))
        for x0, x1 in ((54, 52), (74, 76)):
            dF.line([(x0, 10), (x1, 46)], fill=sc, width=2)
            dF.rectangle([x1 - 1, 44, x1 + 1, 50], fill=shade(sc, 0.7))

    # jacket
    if s.get("zipper"):
        inner = color(s, "inner_color", hexc("#f2f2f2"))
        dF.polygon([(46, 0), (82, 0), (70, 34), (58, 34)], fill=inner)
        dF.line([(64, 32), (64, length - 1)], fill=hexc("#c8c8c8"), width=2)
        dF.line([(66, 32), (66, length - 1)], fill=shade(base, 0.55), width=1)
        dF.rectangle([62, 34, 66, 42], fill=hexc("#b0b0b0"))
    if s.get("collar"):
        cc = shade(base, 0.8)
        dF.polygon([(38, 0), (56, 0), (62, 28), (46, 16)], fill=cc, outline=shade(base, 0.6))
        dF.polygon([(90, 0), (72, 0), (66, 28), (82, 16)], fill=cc, outline=shade(base, 0.6))
        rect(B, cc, 0, 0, None, 8)
    if s.get("side_pockets") and length >= 110:
        for a, b in (((14, 80), (26, 104)), ((114, 80), (102, 104))):
            dF.line([a, b], fill=shade(base, 0.5), width=3)

    # back graphic
    graphic(B, s, "back", 50 if s.get("hood") else 16, length - 14)

    # sleeves
    sleeves = s.get("sleeves", "short")
    sleeve_end = int(s.get("sleeve_length", {"long": 112, "short": 44, "none": 0}[sleeves]))
    scol = color(s, "sleeve_color", base)
    scol2 = color(s, "sleeve_color2", shade(scol, 0.85) if s.get("sleeve_color") else base2)
    cuff = s.get("cuffs") or sleeves == "short"
    for part in ("right", "left"):
        if sleeve_end <= 0:
            break
        rect(sh.face(part, "U"), scol)
        for i, f in enumerate(SIDES):
            im = sh.face(part, f)
            gradient(im, scol, scol2, 0, sleeve_end)
            pattern(im, pat, 0, sleeve_end, seed=10 + i)
            if f == OUTER[part]:
                stripes(im, s.get("stripes"), 0, sleeve_end - (8 if cuff else 0))
            if cuff:
                band(im, sleeve_end - 8, sleeve_end, trim)

    # stripes down the torso sides continue the sleeve stripes
    if s.get("stripes"):
        for f in ("R", "L"):
            stripes(sh.face("torso", f), s["stripes"], 0, length - 9)

    if s.get("shading", True):
        for f in SIDES:
            apply_shading(sh.face("torso", f), 0.22, WRINKLES["torso"] if f in "FB" else WRINKLES["arm"][:2])
        for part in ("right", "left"):
            for f in SIDES:
                apply_shading(sh.face(part, f), 0.3, WRINKLES["arm"])
    return sh.render()


# --------------------------------------------------------------------------- pants

PANTS_PRESETS = {
    "jeans": {"color": "#3d5a80", "stitch": "#d4a35a", "front_pockets": True, "back_pockets": True,
              "belt": "#2b2118", "fly": True},
    "cargo": {"color": "#5b5f45", "cargo_pockets": True, "belt": "#1f1f1f", "fly": True, "back_pockets": True},
    "joggers": {"color": "#3a3a3a", "cuffs": True, "drawstring": True},
    "shorts": {"color": "#2f3e46", "leg_length": 58, "drawstring": True},
    "trackpants": {"color": "#1d3557", "stripes": {"color": "#ffffff", "count": 2}, "cuffs": True,
                   "drawstring": True},
}


def build_pants(spec):
    s = {**PANTS_PRESETS.get(spec.get("type", "jeans"), {}), **spec}
    base = color(s, "color", hexc("#3d5a80"))
    base2 = color(s, "color2", shade(base, 0.85))
    trim = color(s, "trim", shade(base, 0.75))
    stitch = color(s, "stitch", shade(base, 0.6))
    pat = s.get("pattern")
    waist = int(s.get("waist", 80))
    leg_end = int(s.get("leg_length", 128))
    shoes = color(s, "shoes", None)
    sole = color(s, "sole", hexc("#f4f4f4"))
    shoe_h = min(int(s.get("shoe_height", 20)), 128 - LIMB_GUIDE) if shoes else 0
    shoe_top = 128 - shoe_h
    sh = Sheet()

    # waist on the torso (a shirt is drawn over this in game)
    for i, f in enumerate(SIDES):
        im = sh.face("torso", f)
        rect(im, base, 0, waist)
        pattern(im, pat, waist, 128, seed=i)
        if s.get("belt"):
            belt = hexc(s["belt"])
            band(im, waist, waist + 7, belt)
            for x in (14, 40, 88, 114) if f in "FB" else (30,):
                rect(im, base, x, waist - 1, x + 3, waist + 8)
        elif s.get("drawstring"):
            band(im, waist, waist + 8, trim)
    rect(sh.face("torso", "D"), base)
    F, B = sh.face("torso", "F"), sh.face("torso", "B")
    dF, dB = ImageDraw.Draw(F), ImageDraw.Draw(B)
    if s.get("belt"):
        dF.rectangle([57, waist - 1, 71, waist + 8], fill=hexc("#c0c0c0"), outline=hexc("#7a7a7a"))
        dF.rectangle([61, waist + 2, 67, waist + 5], fill=hexc(s["belt"]))
    elif s.get("drawstring"):
        for x0, x1 in ((60, 57), (68, 71)):
            dF.line([(x0, waist + 4), (x1, waist + 26)], fill=hexc("#f2f2f2"), width=2)
    if s.get("fly"):
        dF.line([(68, waist + 9), (68, 120), (62, 127)], fill=stitch, width=1)
    if s.get("front_pockets"):
        dF.arc([-18, waist - 10, 24, waist + 30], 0, 90, fill=stitch, width=1)
        dF.arc([104, waist - 10, 146, waist + 30], 90, 180, fill=stitch, width=1)
    if s.get("back_pockets"):
        for x0 in (18, 78):
            dB.polygon([(x0, waist + 13), (x0 + 32, waist + 13), (x0 + 30, waist + 40), (x0 + 16, waist + 46),
                        (x0 + 2, waist + 40)], outline=stitch)

    # legs
    for part in ("right", "left"):
        rect(sh.face(part, "U"), base)
        for i, f in enumerate(SIDES):
            im = sh.face(part, f)
            d = ImageDraw.Draw(im)
            gradient(im, base, base2, 0, leg_end)
            pattern(im, pat, 0, leg_end, seed=20 + i)
            outer = f == OUTER[part]
            if outer or f == {"right": "L", "left": "R"}[part]:
                d.line([(32, 0), (32, leg_end)], fill=shade(base, 0.7))
                if s.get("stitch") and outer:
                    d.line([(29, 0), (29, leg_end)], fill=stitch)
                    d.line([(35, 0), (35, leg_end)], fill=stitch)
            if outer:
                stripes(im, s.get("stripes"), 0, leg_end)
                if s.get("cargo_pockets") and leg_end >= 90:
                    d.rectangle([14, 40, 50, 80], fill=shade(base, 0.93), outline=shade(base, 0.62), width=2)
                    d.rectangle([12, 36, 52, 46], fill=shade(base, 0.85), outline=shade(base, 0.6), width=1)
                    d.ellipse([30, 39, 34, 43], fill=shade(base, 0.5))
            if leg_end < 128:
                band(im, leg_end - 6, leg_end, trim)
            elif s.get("cuffs"):
                band(im, shoe_top - 10, shoe_top, trim)
            if s.get("ripped") and f == "F" and leg_end >= 90:
                cx, cy = (30, 60) if part == "right" else (34, 74)
                d.ellipse([cx - 13, cy - 7, cx + 13, cy + 7], fill=shade(base, 1.35))
                d.ellipse([cx - 10, cy - 4, cx + 10, cy + 4], fill=(0, 0, 0, 0))
                for dy in (-2, 1):
                    d.line([(cx - 10, cy + dy), (cx + 10, cy + dy)], fill=hexc("#f5f5f5"), width=1)
            # socks + shoes
            if shoes:
                socks = color(s, "socks", None)
                if socks and leg_end < shoe_top:
                    rect(im, socks, 0, shoe_top - 14, None, shoe_top)
                    rect(im, shade(socks, 0.85), 0, shoe_top - 14, None, shoe_top - 11)
                rect(im, shoes, 0, shoe_top)
                rect(im, sole, 0, 123)
                d.line([(0, 122), (63, 122)], fill=shade(shoes, 0.6))
                if f == "F":
                    for k in range(3):
                        y = shoe_top + 3 + k * 4
                        if y < 120:
                            d.line([(24, y), (40, y)], fill=color(s, "laces", sole), width=2)
                accent = color(s, "shoe_accent", None)
                if accent and outer:
                    d.polygon([(10, 121), (52, shoe_top + 4), (56, shoe_top + 8), (18, 121)], fill=accent)
        if shoes:
            D = sh.face(part, "D")
            rect(D, sole)
            for y in range(6, 64, 8):
                ImageDraw.Draw(D).line([(6, y), (57, y)], fill=shade(sole, 0.8), width=2)

    if s.get("shading", True):
        for f in SIDES:
            apply_shading(sh.face("torso", f), 0.22)
        for part in ("right", "left"):
            for f in SIDES:
                wr = list(WRINKLES["leg"])
                if s.get("cuffs") and leg_end >= 128:
                    wr += [(10, shoe_top - 18, 26, shoe_top - 14), (36, shoe_top - 14, 54, shoe_top - 19)]
                apply_shading(sh.face(part, f), 0.3, wr)
    return sh.render()


# --------------------------------------------------------------------------- t-shirt


def build_tshirt(spec):
    size = 512
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if spec.get("bg"):
        ImageDraw.Draw(im).rounded_rectangle([16, 16, size - 16, size - 16], radius=60, fill=hexc(spec["bg"]))
    graphic(im, {**spec, "front_text": spec.get("text"), "front_icon": spec.get("icon")}, "front",
            40, size - 40, max_w=size - 100)
    return im


# --------------------------------------------------------------------------- preview


def preview(shirt, pants, skin="#e8c39e", scale=2):
    skin = hexc(skin)

    def crop(img, part, f):
        x, y, w, h = BOXES[part][f]
        return img.crop((x, y, x + w, y + h))

    def piece(part, f, layers):
        _, _, w, h = BOXES[part][f]
        out = Image.new("RGBA", (w, h), skin)
        for img in layers:
            if img is not None:
                out.alpha_composite(crop(img, part, f))
        return out

    def view(front):
        v = Image.new("RGBA", (256, 334), (0, 0, 0, 0))
        d = ImageDraw.Draw(v)
        d.rounded_rectangle([92, 4, 164, 74], radius=18, fill=skin, outline=shade(skin, 0.8), width=2)
        if front:
            d.ellipse([110, 30, 118, 42], fill=(30, 30, 30, 255))
            d.ellipse([138, 30, 146, 42], fill=(30, 30, 30, 255))
            d.arc([112, 36, 144, 60], 25, 155, fill=(30, 30, 30, 255), width=3)
        tf = "F" if front else "B"
        v.alpha_composite(piece("torso", tf, [pants, shirt]), (64, 76))
        arms = (("right", "F"), ("left", "F")) if front else (("left", "B"), ("right", "B"))
        legs = (("right", "F"), ("left", "F")) if front else (("left", "B"), ("right", "B"))
        v.alpha_composite(piece(*arms[0], [shirt]), (0, 76))
        v.alpha_composite(piece(*arms[1], [shirt]), (192, 76))
        v.alpha_composite(piece(*legs[0], [pants]), (64, 204))
        v.alpha_composite(piece(*legs[1], [pants]), (128, 204))
        return v

    def side():
        # character's right side: the right arm's outer face hides the torso side
        v = Image.new("RGBA", (128, 334), (0, 0, 0, 0))
        ImageDraw.Draw(v).rounded_rectangle([28, 4, 100, 74], radius=18, fill=skin, outline=shade(skin, 0.8), width=2)
        v.alpha_composite(piece("torso", "R", [pants, shirt]), (32, 76))
        v.alpha_composite(piece("right", "R", [shirt]), (32, 76))
        v.alpha_composite(piece("right", "R", [pants]), (32, 204))
        return v

    pad = 24
    views = [(view(True), "FRONT"), (view(False), "BACK"), (side(), "SIDE")]
    canvas = Image.new("RGBA", (pad * 4 + 256 * 2 + 128, pad * 2 + 334 + 24), (238, 240, 243, 255))
    font = load_font(16)
    d = ImageDraw.Draw(canvas)
    x = pad
    for v, label in views:
        canvas.alpha_composite(v, (x, pad))
        d.text((x + v.width // 2, pad + 334 + 12), label, font=font, fill=(90, 95, 105, 255), anchor="mm")
        x += v.width + pad
    return canvas.resize((canvas.width * scale, canvas.height * scale), Image.NEAREST)


# --------------------------------------------------------------------------- main


def build(path, outroot):
    spec = json.loads(Path(path).read_text())
    name = spec.get("name", Path(path).stem)
    out = Path(outroot) / name
    out.mkdir(parents=True, exist_ok=True)
    shirt = build_shirt(spec["shirt"]) if spec.get("shirt") else None
    pants = build_pants(spec["pants"]) if spec.get("pants") else None
    made = []
    if shirt:
        shirt.save(out / "shirt.png"); made.append("shirt.png")
    if pants:
        pants.save(out / "pants.png"); made.append("pants.png")
    if spec.get("tshirt"):
        build_tshirt(spec["tshirt"]).save(out / "tshirt.png"); made.append("tshirt.png")
    if shirt or pants:
        preview(shirt, pants, spec.get("skin", "#e8c39e")).save(out / "preview.png"); made.append("preview.png")
    print(f"{name}: {', '.join(made)} -> {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("designs", nargs="*", help="design JSON files (default: everything in designs/)")
    ap.add_argument("--out", default=str(ROOT / "output"), help="output folder")
    a = ap.parse_args()
    files = a.designs or sorted(str(p) for p in (ROOT / "designs").glob("*.json"))
    if not files:
        sys.exit("no designs found; add a .json file to designs/")
    for f in files:
        build(f, a.out)


if __name__ == "__main__":
    main()
