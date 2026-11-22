"""Builds small fake save files in the real formats (GameCube GCI and card images, Wii banner.bin,
PARAM.SFO, PS1 cards, disc headers, 3DS/Switch ROM headers) for the save-identification tests.
Everything is generated here: no real game data."""
import struct
from pathlib import Path


def rgb5a3_opaque(r, g, b):          # 0..31 each
    return 0x8000 | r << 10 | g << 5 | b


def encode_rgb5a3(pixels, w, h):
    """pixels[y][x] = 16-bit RGB5A3 value -> GameCube 4x4-tiled big-endian texture."""
    out = bytearray()
    for ty in range(0, h, 4):
        for tx in range(0, w, 4):
            for y in range(ty, ty + 4):
                for x in range(tx, tx + 4):
                    out += struct.pack(">H", pixels[y][x])
    return bytes(out)


def encode_ci8(indices, w, h):
    out = bytearray()
    for ty in range(0, h, 4):
        for tx in range(0, w, 8):
            for y in range(ty, ty + 4):
                for x in range(tx, tx + 8):
                    out.append(indices[y][x])
    return bytes(out)


RED, GREEN, BLUE = rgb5a3_opaque(31, 0, 0), rgb5a3_opaque(0, 31, 0), rgb5a3_opaque(0, 0, 31)


def quad_image(w, h, a, b):
    """Left half colour a, right half colour b (so tiling mistakes show)."""
    return [[a if x < w // 2 else b for x in range(w)] for y in range(h)]


def gci(code=b"GZLE", maker=b"01", fname=b"gczelda2", title="The Legend of Zelda",
        detail="File 2", icon="rgb", banner=True, enc="cp1252", blocks=2):
    """A .gci file (64-byte header + data)."""
    data = bytearray(blocks * 0x2000)
    off = 0
    flags = 0
    if banner:
        data[off:off + 6144] = encode_rgb5a3(quad_image(96, 32, RED, BLUE), 96, 32)
        off += 6144
        flags = 2
    img_off = 0
    if icon == "rgb":
        data[off:off + 2048] = encode_rgb5a3(quad_image(32, 32, GREEN, RED), 32, 32)
        off += 2048
        icon_fmt, anim = 2, 3
    elif icon == "ci8shared":
        idx = [[1 if x < 16 else 2 for x in range(32)] for _ in range(32)]
        data[off:off + 1024] = encode_ci8(idx, 32, 32)
        off += 1024
        pal = bytearray(512)
        struct.pack_into(">H", pal, 2, BLUE)
        struct.pack_into(">H", pal, 4, GREEN)
        data[off:off + 512] = pal
        off += 512
        icon_fmt, anim = 1, 3
    else:
        icon_fmt, anim = 0, 0
    com = off
    data[com:com + 32] = title.encode(enc)[:32].ljust(32, b"\0")
    data[com + 32:com + 64] = detail.encode(enc)[:32].ljust(32, b"\0")
    hdr = bytearray(b"\xff" * 0x40)
    hdr[0:4], hdr[4:6], hdr[6], hdr[7] = code, maker, 0xFF, flags
    hdr[8:0x28] = fname.ljust(32, b"\0")
    struct.pack_into(">IIHHBBHHHI", hdr, 0x28, 0, img_off, icon_fmt, anim, 4, 0, 5, blocks, 0xFFFF, com)
    return bytes(hdr), bytes(data)


def gci_file(path, **kw):
    h, d = gci(**kw)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(h + d)


def gc_card(path, saves):
    """saves: list of (header, data) from gci(). A 59-block card (Memory Card 59) image."""
    nblocks = 64
    card = bytearray(b"\0" * (nblocks * 0x2000))
    d1 = bytearray(b"\xff" * 0x2000)
    d2 = bytearray(b"\xff" * 0x2000)
    first = 5
    for n, (h, data) in enumerate(saves):
        cnt = len(data) // 0x2000
        e = bytearray(h)
        struct.pack_into(">H", e, 0x36, first)
        struct.pack_into(">H", e, 0x38, cnt)
        d1[n * 0x40:(n + 1) * 0x40] = e
        card[first * 0x2000:(first + cnt) * 0x2000] = data
        first += cnt
    struct.pack_into(">h", d1, 0x1FFA, 7)            # newer directory
    d2[0:0x40] = gci(code=b"GOLD", title="Old directory")[0]
    struct.pack_into(">h", d2, 0x1FFA, 3)
    card[0x2000:0x4000], card[0x4000:0x6000] = d1, d2
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(card)


def wii_banner(path, title="Super Mario Galaxy", sub="Mario: 3 stars"):
    b = bytearray(0xA0 + 0x6000 + 0x1200)
    b[0:4] = b"WIBN"
    b[0x20:0x20 + len(title) * 2] = title.encode("utf-16-be")
    b[0x60:0x60 + len(sub) * 2] = sub.encode("utf-16-be")
    b[0xA0:0x60A0] = encode_rgb5a3(quad_image(192, 64, BLUE, GREEN), 192, 64)
    b[0x60A0:0x60A0 + 0x1200] = encode_rgb5a3(quad_image(48, 48, RED, GREEN), 48, 48)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(bytes(b))


def sfo(entries):
    """entries: dict key -> str or int (PSP/PS3 PARAM.SFO)."""
    keys = b""
    data = b""
    index = b""
    for k, v in entries.items():
        koff = len(keys)
        keys += k.encode() + b"\0"
        if isinstance(v, int):
            raw, fmt = struct.pack("<I", v), 0x0404
        else:
            raw, fmt = v.encode("utf-8") + b"\0", 0x0204
        maxlen = (len(raw) + 3) & ~3
        index += struct.pack("<HHIII", koff, fmt, len(raw), maxlen, len(data))
        data += raw.ljust(maxlen, b"\0")
    while len(keys) % 4:
        keys += b"\0"
    key_tab = 0x14 + len(index)
    data_tab = key_tab + len(keys)
    return b"\0PSF" + struct.pack("<IIII", 0x0101, key_tab, data_tab, len(entries)) + index + keys + data


def tiny_png(r=255, g=0, b=0):
    import saveinfo
    return saveinfo.png_bytes(bytes((r, g, b, 255)) * 16, 4, 4)


def ps1_card(path, saves):
    """saves: list of (filename like 'BASLUS-00067DRAX00', in-game title)."""
    card = bytearray(128 * 1024)
    card[0:2] = b"MC"
    for i in range(1, 16):
        struct.pack_into("<I", card, 0x80 * i, 0xA0)
    for n, (fname, title) in enumerate(saves, start=1):
        fr = 0x80 * n
        struct.pack_into("<IIH", card, fr, 0x51, 0x2000, 0xFFFF)
        card[fr + 0x0A:fr + 0x0A + len(fname)] = fname.encode("ascii")
        blk = 0x2000 * n
        card[blk:blk + 2] = b"SC"
        card[blk + 2], card[blk + 3] = 0x11, 1
        t = title.encode("cp932")[:64]
        card[blk + 4:blk + 4 + len(t)] = t
        struct.pack_into("<H", card, blk + 0x62, 31)          # colour 1 = red
        struct.pack_into("<H", card, blk + 0x64, 31 << 10)    # colour 2 = blue
        for k in range(0x80):
            card[blk + 0x80 + k] = 0x21                       # pixels 1,2,1,2…
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(bytes(card))


def gc_disc(path, game_id=b"GZLE01", title=b"ZELDA THE WIND WAKER", wii=False, offset=0):
    h = bytearray(0x440)
    h[0:6] = game_id
    if wii:
        h[0x18:0x1C] = b"\x5D\x1C\x9E\xA3"
    else:
        h[0x1C:0x20] = b"\xC2\x33\x9F\x3D"
    h[0x20:0x20 + len(title)] = title
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(b"\0" * offset + bytes(h) + b"\0" * 4096)


def n3ds_cci(path, program_id):
    b = bytearray(0x4000 + 0x400)
    b[0x100:0x104] = b"NCSD"
    struct.pack_into("<II", b, 0x120, 0x4000 // 0x200, 1)
    b[0x4100:0x4104] = b"NCCH"
    struct.pack_into("<Q", b, 0x4118, program_id)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(bytes(b))


def n3ds_cia(path, program_id, title):
    al = lambda x: (x + 63) & ~63
    hdr, cert, tik, tmd, meta, csize = 0x2020, 0xA00, 0x350, 0xB34, 0x3AC0, 0x400
    coff = al(al(al(al(hdr) + cert) + tik) + tmd)
    moff = al(coff + csize)
    b = bytearray(moff + meta)
    struct.pack_into("<IHHIIIIQ", b, 0, hdr, 0, 0, cert, tik, tmd, meta, csize)
    b[coff + 0x100:coff + 0x104] = b"NCCH"
    struct.pack_into("<Q", b, coff + 0x118, program_id)
    smdh = bytearray(0x36C0)
    smdh[0:4] = b"SMDH"
    t = title.encode("utf-16-le")
    smdh[8 + 0x200 + 0x80:8 + 0x200 + 0x80 + len(t)] = t
    b[moff + 0x400:moff + 0x400 + 0x36C0] = smdh
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(bytes(b))


def nsp(path, title_id):
    names = [b"0123456789abcdef0123456789abcdef.nca", (title_id + "0000000000000000.tik").encode()]
    strtab = b"\0".join(names) + b"\0"
    while len(strtab) % 16:
        strtab += b"\0"
    entries = b""
    off = 0
    so = 0
    for nm in names:
        entries += struct.pack("<QQII", off, 16, so, 0)
        off += 16
        so += len(nm) + 1
    b = b"PFS0" + struct.pack("<III", len(names), len(strtab), 0) + entries + strtab + b"\0" * 32
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(b)


# ---------------------------------------------------------------------------
# PS2 save icons
# ---------------------------------------------------------------------------
def ps2_icon_sys(title1="SPACE GAME", title2="Stage 3", icon="icon.ico", copy="", delete="",
                 bg=((60, 0, 0), (0, 60, 0), (0, 0, 60), (60, 60, 0))):
    b = bytearray(964)
    b[0:4] = b"PS2D"
    t1, t2 = title1.encode("cp932"), title2.encode("cp932")
    struct.pack_into("<H", b, 6, len(t1) if title2 else 0)
    struct.pack_into("<I", b, 0x0C, 0x80)
    for k, (r, g, bb) in enumerate(bg):
        struct.pack_into("<4I", b, 0x10 + 16 * k, r, g, bb, 0)
    for k, v in enumerate([(0, 0, 1, 0), (0.5, 0.5, 0.5, 0), (-0.5, 0.5, 0.5, 0)]):
        struct.pack_into("<4f", b, 0x50 + 16 * k, *v)
    for k, v in enumerate([(0.8, 0.8, 0.8, 0), (0.2, 0.2, 0.2, 0), (0.1, 0.1, 0.1, 0)]):
        struct.pack_into("<4f", b, 0x80 + 16 * k, *v)
    struct.pack_into("<4f", b, 0xB0, 0.3, 0.3, 0.3, 0)
    title = (t1 + t2)[:67]
    b[0xC0:0xC0 + len(title)] = title
    for off, name in ((0x104, icon), (0x144, copy or icon), (0x184, delete or icon)):
        b[off:off + len(name)] = name.encode("ascii")
    return bytes(b)


def _cube_tris(s=1.0):
    """12 triangles of a cube, with per-face UV quads; front face (+z) uses the left half."""
    faces = [  # (normal, four corners)
        ((0, 0, 1), [(-s, -s, s), (s, -s, s), (s, s, s), (-s, s, s)]),
        ((0, 0, -1), [(s, -s, -s), (-s, -s, -s), (-s, s, -s), (s, s, -s)]),
        ((1, 0, 0), [(s, -s, s), (s, -s, -s), (s, s, -s), (s, s, s)]),
        ((-1, 0, 0), [(-s, -s, -s), (-s, -s, s), (-s, s, s), (-s, s, -s)]),
        ((0, -1, 0), [(-s, -s, -s), (s, -s, -s), (s, -s, s), (-s, -s, s)]),
        ((0, 1, 0), [(-s, s, s), (s, s, s), (s, s, -s), (-s, s, -s)]),
    ]
    tris = []
    for fi, (n, q) in enumerate(faces):
        u0, u1 = (0.02, 0.48) if fi == 0 else (0.52, 0.98)
        uv = [(u0, 0.02), (u1, 0.02), (u1, 0.98), (u0, 0.98)]
        for a, b, c in ((0, 1, 2), (0, 2, 3)):
            tris += [(q[a], n, uv[a]), (q[b], n, uv[b]), (q[c], n, uv[c])]
    return tris


def ps2_icon(shapes=1, compressed=False, textured=True, color=(128, 128, 128, 128)):
    """A textured cube icon. Texture: left half red, right half blue (16-bit, red in low bits).
    Shape 2 (if any) is the cube at half size, with animation keys morphing 0 -> 1 -> 0."""
    tris = _cube_tris()
    tex_type = 0x07 if textured else 0x03
    if textured and compressed:
        tex_type = 0x0F
    out = bytearray(struct.pack("<IIIfI", 0x00010000, shapes, tex_type, 1.0, len(tris)))
    for pos, n, uv in tris:
        for sh in range(shapes):
            k = 1.0 if sh == 0 else 0.5
            out += struct.pack("<hhhH", *(int(c * k * 4096) for c in pos), 0)
        out += struct.pack("<hhhH", *(int(c * 4096) for c in n), 0)
        out += struct.pack("<hh", int(uv[0] * 4096), int(uv[1] * 4096))
        out += bytes(color)
    out += struct.pack("<IIfII", 1, 60, 1.0, 0, shapes if shapes > 1 else 1)
    if shapes > 1:
        out += struct.pack("<II", 0, 3) + struct.pack("<6f", 0, 1, 30, 0, 60, 1)
        out += struct.pack("<II", 1, 3) + struct.pack("<6f", 0, 0, 30, 1, 60, 0)
    else:
        out += struct.pack("<II", 0, 1) + struct.pack("<ff", 0, 1)
    if textured:
        red, blue = 31, 31 << 10
        row = [red] * 64 + [blue] * 64
        if compressed:
            # per row: repeat red 64 times, then 2 literal blues, then repeat blue 62 times
            body = b""
            for _ in range(128):
                body += struct.pack("<HH", 64, red)
                body += struct.pack("<H", 0x10000 - 2) + struct.pack("<HH", blue, blue)
                body += struct.pack("<HH", 62, blue)
            out += struct.pack("<I", len(body)) + body
        else:
            out += b"".join(struct.pack("<H", v) for _ in range(128) for v in row)
    return bytes(out)
