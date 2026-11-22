"""
ps2icon.py - PS2 save icons: icon.sys and the 3D icon files (.ico/.icn), and a small renderer.

Every PS2 save carries its own icon: icon.sys names the title (two lines), the background
gradient (four corner colours), three lights and the ambient light, and the icon files to use
for the normal / copy / delete views. Each icon file is a 3D model: one or more "shapes" (for
morph animation), per-vertex normals, texture coordinates and colours, animation keys, and a
128x128 texture (16-bit, optionally run-length compressed).

render() draws a model with Pillow only (no OpenGL): textured triangles sorted back to front,
lit with the save's own lights, drawn at twice the size and scaled down for smooth edges.
Formats as documented by the PS2 homebrew community (psdevwiki "ICN Files", mymc's ps2icon
notes) and as read by the MIT-licensed ps2mc crate.
"""
from __future__ import annotations

import math
import struct
import unicodedata
from dataclasses import dataclass, field

try:
    from PIL import Image, ImageDraw
except Exception:  # noqa - rendering needs Pillow; parsing doesn't
    Image = ImageDraw = None

ICON_MAGIC = 0x00010000
TEX_W = TEX_H = 128
TEX_BYTES = TEX_W * TEX_H * 2
FIXED = 4096.0                     # coordinates, normals and UVs are 4.12 fixed point


class IconError(Exception):
    pass


# ---------------------------------------------------------------------------
# icon.sys
# ---------------------------------------------------------------------------
@dataclass
class IconSys:
    title1: str = ""
    title2: str = ""
    bg_alpha: int = 128                                   # 0 clear .. 128 opaque
    bg: list = field(default_factory=lambda: [(40, 40, 60)] * 4)   # TL, TR, BL, BR (0..255)
    light_dirs: list = field(default_factory=list)        # 3 x (x, y, z)
    light_colors: list = field(default_factory=list)      # 3 x (r, g, b) 0..1
    ambient: tuple = (0.35, 0.35, 0.35)
    icon_normal: str = ""
    icon_copy: str = ""
    icon_delete: str = ""

    @property
    def title(self) -> str:
        return " ".join(t for t in (self.title1, self.title2) if t)


def _sjis(b: bytes) -> str:
    try:
        s = b.decode("cp932")
    except UnicodeDecodeError:
        s = b.decode("shift_jis", "replace")
    return " ".join(unicodedata.normalize("NFKC", s).split())


def _gs_colour(v: int) -> int:
    """icon.sys colours use the PS2's 0..128 scale (128 = full); some saves use 0..255."""
    return max(0, min(255, v * 2 if v <= 128 else v))


def parse_icon_sys(data: bytes) -> IconSys | None:
    if len(data) < 0x1C4 or data[:4] != b"PS2D":
        return None
    brk, = struct.unpack_from("<H", data, 6)
    alpha, = struct.unpack_from("<I", data, 0x0C)
    bg = []
    for k in range(4):
        r, g, b, _a = struct.unpack_from("<4I", data, 0x10 + 16 * k)
        bg.append((_gs_colour(r), _gs_colour(g), _gs_colour(b)))
    vec = [struct.unpack_from("<4f", data, 0x50 + 16 * k) for k in range(7)]
    dirs = [tuple(v[:3]) for v in vec[0:3]]
    cols = [tuple(max(0.0, min(1.0, c)) for c in v[:3]) for v in vec[3:6]]
    amb = tuple(max(0.0, min(1.0, c)) for c in vec[6][:3])
    raw = bytes(data[0xC0:0xC0 + 68]).split(b"\0", 1)[0]
    if 0 < brk < len(raw):
        t1, t2 = _sjis(raw[:brk]), _sjis(raw[brk:])
    else:
        t1, t2 = _sjis(raw), ""

    def name(off):
        return bytes(data[off:off + 64]).split(b"\0", 1)[0].decode("latin-1", "replace")
    return IconSys(t1, t2, min(int(alpha), 128), bg, dirs, cols, amb,
                   name(0x104), name(0x144), name(0x184))


# ---------------------------------------------------------------------------
# Icon models
# ---------------------------------------------------------------------------
@dataclass
class Icon:
    shapes: list            # [shape][vertex] -> (x, y, z)
    normals: list           # [vertex] -> (x, y, z)
    uvs: list               # [vertex] -> (u, v) in 0..1
    colors: list            # [vertex] -> (r, g, b, a) 0..255
    tex_type: int
    texture: bytes | None   # RGBA, 128x128
    frame_length: int = 1
    anim_speed: float = 1.0
    frames: list = field(default_factory=list)   # [(shape id, [(time, value), …])]

    @property
    def has_texture(self) -> bool:
        return self.texture is not None


def decode_rle(data: bytes) -> bytes:
    out = bytearray()
    pos, n = 0, len(data)
    while pos + 2 <= n and len(out) < TEX_BYTES:
        code, = struct.unpack_from("<H", data, pos)
        pos += 2
        if code & 0x8000:                                  # copy the next words as they are
            words = 0x10000 - code
            out += data[pos:pos + 2 * words]
            pos += 2 * words
        elif code:                                         # repeat the next word `code` times
            out += data[pos:pos + 2] * code
            pos += 2
    if len(out) < TEX_BYTES:
        out += b"\0" * (TEX_BYTES - len(out))
    return bytes(out[:TEX_BYTES])


def texture_rgba(raw: bytes) -> bytes:
    """16-bit pixels, red in the low bits -> RGBA (always opaque, as the browser draws them)."""
    out = bytearray(TEX_W * TEX_H * 4)
    lut = [(v << 3) | (v >> 2) for v in range(32)]
    for i in range(TEX_W * TEX_H):
        p = raw[2 * i] | raw[2 * i + 1] << 8
        o = 4 * i
        out[o] = lut[p & 31]
        out[o + 1] = lut[(p >> 5) & 31]
        out[o + 2] = lut[(p >> 10) & 31]
        out[o + 3] = 255
    return bytes(out)


def parse_icon(data: bytes) -> Icon:
    if len(data) < 20:
        raise IconError("too short")
    magic, nshapes, tex_type, _unk, nverts = struct.unpack_from("<IIIfI", data, 0)
    if magic != ICON_MAGIC:
        raise IconError("not a PS2 icon")
    if not (1 <= nshapes <= 32) or nverts > 30000 or nverts % 3:
        raise IconError("unexpected icon header")
    stride = 8 * nshapes + 16
    off = 20
    if off + stride * nverts > len(data):
        raise IconError("icon data is cut off")
    shapes = [[None] * nverts for _ in range(nshapes)]
    normals, uvs, colors = [None] * nverts, [None] * nverts, [None] * nverts
    for v in range(nverts):
        for s in range(nshapes):
            x, y, z, _w = struct.unpack_from("<hhhH", data, off)
            shapes[s][v] = (x / FIXED, y / FIXED, z / FIXED)
            off += 8
        nx, ny, nz, _w = struct.unpack_from("<hhhH", data, off)
        u, t = struct.unpack_from("<hh", data, off + 8)
        colors[v] = tuple(data[off + 12:off + 16])
        normals[v] = (nx / FIXED, ny / FIXED, nz / FIXED)
        uvs[v] = (u / FIXED, t / FIXED)
        off += 16
    frame_length, anim_speed, frames = 1, 1.0, []
    if off + 20 <= len(data):
        tag, frame_length, anim_speed, _play, nframes = struct.unpack_from("<IIfII", data, off)
        if tag == 1 and nframes < 1000:
            off += 20
            for _ in range(nframes):
                if off + 8 > len(data):
                    break
                shape_id, nkeys = struct.unpack_from("<II", data, off)
                off += 8
                if nkeys > 10000 or off + 8 * nkeys > len(data):
                    break
                keys = [struct.unpack_from("<ff", data, off + 8 * k) for k in range(nkeys)]
                off += 8 * nkeys
                frames.append((shape_id, keys))
    texture = None
    if tex_type & 0b100:
        try:
            if tex_type & 0b1000:
                size, = struct.unpack_from("<I", data, off)
                raw = decode_rle(data[off + 4:off + 4 + size])
            else:
                raw = data[off:off + TEX_BYTES]
                raw = raw + b"\0" * (TEX_BYTES - len(raw))
            texture = texture_rgba(raw)
        except struct.error:
            texture = None
    return Icon(shapes, normals, uvs, colors, tex_type, texture, max(1, frame_length),
                anim_speed if math.isfinite(anim_speed) and anim_speed > 0 else 1.0, frames)


# ---------------------------------------------------------------------------
# Animation
# ---------------------------------------------------------------------------
def _key_value(keys, t):
    if not keys:
        return 0.0
    if t <= keys[0][0]:
        return keys[0][1]
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        if t0 <= t <= t1:
            return v0 if t1 == t0 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return keys[-1][1]


def shape_at(icon: Icon, phase: float) -> list:
    """Vertex positions at a point (0..1) through the icon's morph animation."""
    if len(icon.shapes) == 1:
        return icon.shapes[0]
    t = phase * icon.frame_length
    weights = [0.0] * len(icon.shapes)
    for shape_id, keys in icon.frames:
        if 0 <= shape_id < len(weights):
            weights[shape_id] = max(0.0, _key_value(keys, t))
    if sum(weights) <= 1e-6:                     # no usable keys: blend through the shapes in order
        pos = phase * len(icon.shapes)
        a = int(pos) % len(icon.shapes)
        b = (a + 1) % len(icon.shapes)
        f = pos - int(pos)
        weights = [0.0] * len(icon.shapes)
        weights[a], weights[b] = 1 - f, f
    total = sum(weights)
    active = [(s, w / total) for s, w in enumerate(weights) if w > 1e-6]
    if len(active) == 1:
        return icon.shapes[active[0][0]]
    out = []
    for v in range(len(icon.shapes[0])):
        x = y = z = 0.0
        for s, w in active:
            px, py, pz = icon.shapes[s][v]
            x += px * w
            y += py * w
            z += pz * w
        out.append((x, y, z))
    return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
DEFAULT_LIGHTS = ([(0.0, 0.0, 1.0), (-0.6, -0.5, 0.6), (0.6, -0.4, 0.4)],
                  [(0.65, 0.65, 0.65), (0.25, 0.25, 0.3), (0.2, 0.2, 0.2)], (0.35, 0.35, 0.35))


def _norm(v):
    l = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) or 1.0
    return (v[0] / l, v[1] / l, v[2] / l)


def bounds(icon: Icon):
    """Centre and radius of the icon's first shape (used to frame every frame the same)."""
    pts = icon.shapes[0]
    if not pts:
        return (0.0, 0.0, 0.0), 1.0
    xs, ys, zs = zip(*pts)
    c = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2)
    r = max(math.sqrt((x - c[0]) ** 2 + (y - c[1]) ** 2 + (z - c[2]) ** 2) for x, y, z in pts)
    return c, r or 1.0


_TEX_CACHE = {}


def _texture_image(icon: Icon):
    key = id(icon.texture)
    im = _TEX_CACHE.get(key)
    if im is None:
        im = Image.frombytes("RGBA", (TEX_W, TEX_H), icon.texture).convert("RGB")
        if len(_TEX_CACHE) > 64:
            _TEX_CACHE.clear()
        _TEX_CACHE[key] = im
    return im


def render(icon: Icon, size: int = 128, angle: float = 0.0, phase: float = 0.0,
           sysinfo: IconSys | None = None, supersample: int = 2, tilt: float = -0.2):
    """The icon as a Pillow RGBA image, turned `angle` radians about the vertical axis."""
    if Image is None:
        raise IconError("Pillow is needed to draw PS2 icons")
    S = size * supersample
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    pts = shape_at(icon, phase)
    centre, radius = bounds(icon)
    ca, sa, ct, st = math.cos(angle), math.sin(angle), math.cos(tilt), math.sin(tilt)

    def xf(p):                                   # rotate about Y, then tilt about X
        x, y, z = p[0] - centre[0], p[1] - centre[1], p[2] - centre[2]
        x, z = x * ca + z * sa, -x * sa + z * ca
        y, z = y * ct - z * st, y * st + z * ct
        return x, y, z

    if sysinfo and sysinfo.light_dirs and any(any(c) for c in sysinfo.light_colors):
        dirs, cols, amb = sysinfo.light_dirs, sysinfo.light_colors, sysinfo.ambient
    else:
        dirs, cols, amb = DEFAULT_LIGHTS
    lights = [(_norm(d), c) for d, c in zip(dirs, cols) if any(c)]
    dist = radius * 4.0                          # gentle perspective
    scale = S * 0.44 / radius

    proj, depth = [], []
    for p in pts:
        x, y, z = xf(p)
        k = dist / max(dist - z, 1e-3)
        proj.append((S / 2 + x * k * scale, S / 2 + y * k * scale))   # PS2 icons: +y is down
        depth.append(z)
    tris = []
    for i in range(0, len(pts) - 2, 3):
        a, b, c = proj[i], proj[i + 1], proj[i + 2]
        area = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        if abs(area) < 0.25:
            continue
        tris.append(((depth[i] + depth[i + 1] + depth[i + 2]) / 3, i))
    tris.sort()                                  # far to near

    tex = _texture_image(icon) if icon.texture is not None else None
    luts = {}
    for _z, i in tris:
        a, b, c = proj[i], proj[i + 1], proj[i + 2]
        # light from the triangle's average normal, turned with the model
        n = [0.0, 0.0, 0.0]
        col = [0.0, 0.0, 0.0]
        for v in (i, i + 1, i + 2):
            nx, ny, nz = xf((icon.normals[v][0] + centre[0], icon.normals[v][1] + centre[1],
                             icon.normals[v][2] + centre[2]))
            n[0] += nx
            n[1] += ny
            n[2] += nz
            vc = icon.colors[v]
            col[0] += vc[0]
            col[1] += vc[1]
            col[2] += vc[2]
        n = _norm(n)
        shade = list(amb)
        for (lx, ly, lz), lc in lights:
            # the sign convention of icon.sys light directions isn't documented: light both
            # sides of a triangle rather than risk drawing an icon black
            d = abs(n[0] * lx + n[1] * ly + n[2] * lz)
            shade[0] += lc[0] * d
            shade[1] += lc[1] * d
            shade[2] += lc[2] * d
        # vertex colours: 128 means "as is" on the PS2
        mul = [min(2.0, shade[k]) * min(2.0, col[k] / (3 * 128.0)) for k in range(3)]
        q = tuple(max(0, min(40, int(m * 20 + 0.5))) for m in mul)
        poly = [a, b, c]
        if tex is None:
            fill = tuple(min(255, int(255 * q[k] / 20)) for k in range(3)) + (255,)
            ImageDraw.Draw(img).polygon(poly, fill=fill, outline=fill)
            continue
        x0 = int(math.floor(min(a[0], b[0], c[0])))
        y0 = int(math.floor(min(a[1], b[1], c[1])))
        x1 = int(math.ceil(max(a[0], b[0], c[0]))) + 1
        y1 = int(math.ceil(max(a[1], b[1], c[1]))) + 1
        w, h = x1 - x0, y1 - y0
        if w <= 0 or h <= 0 or w > 4 * S or h > 4 * S:
            continue
        coeffs = _affine(poly, [icon.uvs[i], icon.uvs[i + 1], icon.uvs[i + 2]], x0, y0)
        if coeffs is None:
            continue
        patch = tex.transform((w, h), Image.AFFINE, coeffs, resample=Image.BILINEAR)
        if q != (20, 20, 20):
            lut = luts.get(q)
            if lut is None:
                lut = luts[q] = [min(255, int(v * q[k] / 20)) for k in range(3) for v in range(256)]
            patch = patch.point(lut)
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).polygon([(x - x0, y - y0) for x, y in poly], fill=255, outline=255)
        img.paste(patch, (x0, y0), mask)
    if supersample > 1:
        img = img.resize((size, size), Image.LANCZOS)
    return img


def _affine(poly, uv, x0, y0):
    """Coefficients mapping output pixels (relative to x0, y0) to texture pixels."""
    (ax, ay), (bx, by), (cx, cy) = [(x - x0, y - y0) for x, y in poly]
    det = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)
    if abs(det) < 1e-9:
        return None
    (au, av), (bu, bv), (cu, cv) = [(u * TEX_W, v * TEX_H) for u, v in uv]
    # solve u = A x + B y + C (and the same for v) through the three corners
    A = ((bu - au) * (cy - ay) - (cu - au) * (by - ay)) / det
    B = ((cu - au) * (bx - ax) - (bu - au) * (cx - ax)) / det
    C = au - A * ax - B * ay
    D = ((bv - av) * (cy - ay) - (cv - av) * (by - ay)) / det
    E = ((cv - av) * (bx - ax) - (bv - av) * (cx - ax)) / det
    F = av - D * ax - E * ay
    return (A, B, C, D, E, F)


def gradient(sysinfo: IconSys | None, w: int, h: int):
    """The save's background: its four corner colours blended across (w x h)."""
    if Image is None:
        raise IconError("Pillow is needed")
    tl, tr, bl, br = (sysinfo.bg if sysinfo else [(40, 40, 60)] * 4)
    small = Image.new("RGB", (2, 2))
    small.putdata([tl, tr, bl, br])
    return small.resize((w, h), Image.BILINEAR)


def icon_files(files: dict, sysinfo: IconSys | None) -> dict:
    """{'normal'|'copy'|'delete': bytes} from a save's files ({name: bytes}), case-insensitively."""
    low = {k.lower(): v for k, v in files.items()}
    out = {}
    names = ((sysinfo.icon_normal, sysinfo.icon_copy, sysinfo.icon_delete) if sysinfo else ("", "", ""))
    for kind, name in zip(("normal", "copy", "delete"), names):
        if name and name.lower() in low:
            out[kind] = low[name.lower()]
    if "normal" not in out:                     # no icon.sys, or it names a missing file
        for k, v in low.items():
            if k.endswith((".ico", ".icn")) and v[:4] == b"\x00\x00\x01\x00":
                out["normal"] = v
                break
    return out
