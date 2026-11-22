"""PS2 save icons: icon.sys (two-line title, background corners, lights, icon file names) and the
3D icon format (shapes, normals, UVs, colours, animation keys, plain and RLE-compressed
textures), morphing, and the renderer (the front face shows the texture's red half, the icon
turns, lights shade it, smooth edges). Icons are generated cubes, not real game data."""
import os, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ps2icon as P
import savefixtures as F

# 1. icon.sys
s = P.parse_icon_sys(F.ps2_icon_sys("ＳＰＡＣＥ ＧＡＭＥ", "ステージ３", icon="view.ico", delete="del.ico"))
assert (s.title1, s.title2, s.title) == ("SPACE GAME", "ステージ3", "SPACE GAME ステージ3"), (s.title1, s.title2)
assert s.bg == [(120, 0, 0), (0, 120, 0), (0, 0, 120), (120, 120, 0)]         # PS2 0..128 scale doubled
assert s.bg_alpha == 128 and len(s.light_dirs) == 3 and abs(s.light_colors[0][0] - 0.8) < 1e-6
assert (s.icon_normal, s.icon_copy, s.icon_delete) == ("view.ico", "view.ico", "del.ico")
one = P.parse_icon_sys(F.ps2_icon_sys("ONE LINE", ""))
assert (one.title1, one.title2) == ("ONE LINE", "")
assert P.parse_icon_sys(b"PS2D" + b"\0" * 10) is None and P.parse_icon_sys(b"x" * 964) is None
print("1. icon.sys: two-line Shift-JIS title, background corners, lights, icon names: ok")

# 2. icon files: plain and compressed textures decode to the same picture
plain = P.parse_icon(F.ps2_icon())
comp = P.parse_icon(F.ps2_icon(compressed=True))
assert len(plain.shapes) == 1 and len(plain.shapes[0]) == 36 and plain.has_texture
assert plain.texture == comp.texture
tex = plain.texture
assert tuple(tex[0:4]) == (255, 0, 0, 255) and tuple(tex[4 * 127:4 * 128]) == (0, 0, 255, 255)
assert plain.shapes[0][0] == (-1.0, -1.0, 1.0) and plain.normals[0] == (0.0, 0.0, 1.0)
assert abs(plain.uvs[1][0] - 0.48) < 0.001 and plain.colors[0] == (128, 128, 128, 128)
flat = P.parse_icon(F.ps2_icon(textured=False))
assert flat.texture is None
for bad in (b"", b"\0" * 20, b"\x00\x00\x01\x00" + b"\xff" * 16, F.ps2_icon()[:200]):
    try:
        P.parse_icon(bad); raise SystemExit("bad icon accepted")
    except P.IconError:
        pass
assert len(P.decode_rle(b"\x05\x00")) == P.TEX_BYTES                    # short data is padded, not a crash
print("2. icon models: shapes, normals, UVs, colours; plain and RLE textures; damaged files refused: ok")

# 3. morph animation follows the keys
anim = P.parse_icon(F.ps2_icon(shapes=2))
assert len(anim.shapes) == 2 and [f[0] for f in anim.frames] == [0, 1] and anim.frame_length == 60
x0 = P.shape_at(anim, 0.0)[0][0]
xmid = P.shape_at(anim, 0.5)[0][0]
x1 = P.shape_at(anim, 1.0)[0][0]
assert abs(x0 + 1.0) < 1e-6 and abs(xmid + 0.5) < 1e-6 and abs(x1 + 1.0) < 1e-6, (x0, xmid, x1)
assert abs(P.shape_at(anim, 0.25)[0][0] + 0.75) < 1e-6
print("3. morph animation blends the shapes by their keys (full size -> half -> full): ok")

# 4. rendering
t0 = time.time()
img = P.render(plain, size=96, angle=0.0, sysinfo=s, tilt=0.0)
dt = time.time() - t0
assert img.size == (96, 96) and img.mode == "RGBA"
r, g, b, a = img.getpixel((48, 48))
assert a == 255 and r > 120 and r > 3 * max(g, b), (r, g, b, a)          # front face: red half
assert img.getpixel((1, 1))[3] == 0                                      # transparent around it
side = P.render(plain, size=96, angle=math.pi / 2 if (math := __import__("math")) else 0, sysinfo=s, tilt=0.0)
r2, g2, b2, a2 = side.getpixel((48, 48))
assert a2 == 255 and b2 > 120 and b2 > 3 * max(r2, g2), (r2, g2, b2)     # turned: a blue side
dark = P.parse_icon_sys(F.ps2_icon_sys())
dark.light_colors = [(0.0, 0.0, 0.0)] * 2 + [(0.05, 0.05, 0.05)]
dark.ambient = (0.1, 0.1, 0.1)
dim = P.render(plain, size=96, sysinfo=dark, tilt=0.0).getpixel((48, 48))
assert dim[0] < r * 0.5, (dim, r)                                        # lights matter
edge = [img.getpixel((x, 48))[3] for x in range(96)]
assert any(0 < v < 255 for v in edge)                                    # anti-aliased edges
fimg = P.render(flat, size=64, tilt=0.0)
assert fimg.getpixel((32, 32))[3] == 255                                 # untextured icons draw too
small = P.render(anim, size=64, phase=0.5, tilt=0.0)
big = P.render(anim, size=64, phase=0.0, tilt=0.0)
assert small.getpixel((17, 32))[3] == 0 and big.getpixel((17, 32))[3] == 255   # morphed smaller
print(f"4. rendering: textured front face, turning, lighting, smooth edges, morphing ({dt * 1000:.0f} ms): ok")

# 5. background gradient and finding a save's icon files
gimg = P.gradient(s, 40, 20)
assert gimg.getpixel((0, 0))[0] > 100 and gimg.getpixel((39, 19))[1] > 100
files = {"ICON.SYS": F.ps2_icon_sys(icon="view.ico", delete="del.ico"), "VIEW.ICO": F.ps2_icon(),
         "del.ico": F.ps2_icon(compressed=True), "DATA": b"x"}
found = P.icon_files(files, P.parse_icon_sys(files["ICON.SYS"]))
assert set(found) == {"normal", "copy", "delete"} and found["delete"] == files["del.ico"]
assert set(P.icon_files({"x.ico": F.ps2_icon()}, None)) == {"normal"}
print("5. background gradient from the corner colours; icon files found case-insensitively: ok")
print("test_ps2icon OK")
