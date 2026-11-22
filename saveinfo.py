"""
saveinfo.py - work out which game (and which save) each save file belongs to.

Many saves already carry their game's name, a description and an icon; this reads them:
  * GameCube (Dolphin, PrimeHack): .gci files and whole-card .raw images. Each save has a
    game ID (GZLE01), two comment lines ("The Legend of Zelda" / "File 2"), a banner and icons.
  * Wii: title/<type>/<id>/data/banner.bin has the title, a subtitle and the icon; the folder
    name is the game ID in hex.
  * PSP (PPSSPP) and PS3 (RPCS3): PARAM.SFO (game title, save title, detail line) and ICON0.PNG.
    PS3 trophies: TROPCONF.SFM names the game.
  * PS1 cards (.mcd/.mcr): each save's product code, in-game title and icon.
  * Save states named after a game ID or serial (Dolphin, PPSSPP, DuckStation, PCSX2, Azahar),
    and RetroArch / melonDS files named after the ROM.
  * Switch (Yuzu), 3DS (Azahar), Wii U (Cemu) and Xbox 360 (Xenia) only have a title ID in the
    folder name; titledb.py turns those into names.
Everything is grouped into SaveGroups (one game's save, with each file's role). No Tk here.
"""
from __future__ import annotations

import re
import struct
import unicodedata
import zlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

MAX_READ = 4 * 1024 * 1024            # never read more than this from one save file


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
@dataclass
class Icon:
    """Either PNG bytes (kind 'png') or raw RGBA pixels (kind 'rgba')."""
    kind: str
    data: bytes
    width: int = 0
    height: int = 0


@dataclass
class Inner:
    """A save inside a memory card image (shown under the card, not a file of its own)."""
    title: str                       # the game's name ("" until known)
    detail: str = ""                 # the save's own text
    game_id: str = ""
    icon: Icon | None = None
    blocks: int = 0

    def label(self) -> str:
        return self.title or self.detail or self.game_id


@dataclass
class SaveGroup:
    key: str                         # unique in one emulator's list
    system: str                      # "GameCube", "Wii", "PSP", ...
    game_id: str = ""                # GZLE01, ULUS10041, 0100F2C0115B6000 …
    title: str = ""                  # the game's name, if known
    detail: str = ""                 # what this save is: "File 2", "Slot 1", "Chapter 3 · 12:30"
    kind: str = "save"               # save | state | card | trophy | profile
    icon: Icon | None = None
    banner: Icon | None = None
    files: list = field(default_factory=list)       # [(location label, rel posix path, role)]
    inner: list = field(default_factory=list)       # [Inner] for card images
    id_kind: str = ""                # what game_id is, for titledb: gc, wii, ps1, ps2, psp, ps3,
                                     # switch, 3ds, 3ds-unique, wiiu, x360
    needs_title: bool = False        # title should come from titledb (not from the save itself)

    def label(self) -> str:
        if self.title:
            return self.title
        return f"{self.system} game {self.game_id}" if self.game_id else self.system


# ---------------------------------------------------------------------------
# Little helpers
# ---------------------------------------------------------------------------
def _read(path: Path, n: int = MAX_READ) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read(n)
    except OSError:
        return b""


def _cstr(b: bytes, enc: str = "latin-1") -> str:
    b = b.split(b"\0", 1)[0]
    try:
        s = b.decode(enc)
    except UnicodeDecodeError:
        s = b.decode("latin-1", "replace")
    return " ".join(s.split())


def _sjis(b: bytes) -> str:
    """Shift-JIS text as PS1 / Japanese GameCube saves use it; full-width letters become normal."""
    b = b.split(b"\0", 1)[0]
    try:
        s = b.decode("cp932")                  # Microsoft's Shift-JIS, as the consoles' fonts map it
    except UnicodeDecodeError:
        s = b.decode("shift_jis", "replace")
    s = unicodedata.normalize("NFKC", s)
    return " ".join(s.split())


def _rgb5a3(v: int):
    if v & 0x8000:
        r, g, b = (v >> 10) & 31, (v >> 5) & 31, v & 31
        return (r << 3 | r >> 2, g << 3 | g >> 2, b << 3 | b >> 2, 255)
    a, r, g, b = (v >> 12) & 7, (v >> 8) & 15, (v >> 4) & 15, v & 15
    return (r * 17, g * 17, b * 17, a << 5 | a << 2 | a >> 1)


def _decode_rgb5a3(buf: bytes, w: int, h: int) -> bytes | None:
    """GameCube/Wii RGB5A3 texture (4x4 tiles, big endian) -> RGBA."""
    if len(buf) < w * h * 2:
        return None
    out = bytearray(w * h * 4)
    i = 0
    for ty in range(0, h, 4):
        for tx in range(0, w, 4):
            for y in range(ty, ty + 4):
                for x in range(tx, tx + 4):
                    v = buf[i] << 8 | buf[i + 1]
                    i += 2
                    o = (y * w + x) * 4
                    out[o:o + 4] = bytes(_rgb5a3(v))
    return bytes(out)


def _decode_ci8(buf: bytes, pal: bytes, w: int, h: int) -> bytes | None:
    """GameCube CI8 texture (8x4 tiles) with an RGB5A3 palette -> RGBA."""
    if len(buf) < w * h or len(pal) < 512:
        return None
    colors = [bytes(_rgb5a3(pal[2 * k] << 8 | pal[2 * k + 1])) for k in range(256)]
    out = bytearray(w * h * 4)
    i = 0
    for ty in range(0, h, 4):
        for tx in range(0, w, 8):
            for y in range(ty, ty + 4):
                for x in range(tx, tx + 8):
                    o = (y * w + x) * 4
                    out[o:o + 4] = colors[buf[i]]
                    i += 1
    return bytes(out)


def png_bytes(rgba: bytes, w: int, h: int) -> bytes:
    """A minimal PNG encoder (so icons can be shown without Pillow)."""
    raw = b"".join(b"\0" + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


# ---------------------------------------------------------------------------
# GameCube
# ---------------------------------------------------------------------------
GC_BLOCK = 0x2000


@dataclass
class GCSave:
    game_id: str                     # GZLE01
    filename: str
    title: str
    detail: str
    blocks: int
    icon: Icon | None = None
    banner: Icon | None = None


def parse_gc_entry(hdr: bytes, data: bytes) -> GCSave | None:
    """One GameCube directory entry (64 bytes) and its save data."""
    if len(hdr) < 0x40 or hdr[:4] == b"\xff\xff\xff\xff":
        return None
    code, maker = hdr[0:4], hdr[4:6]
    if not all(0x20 <= c < 0x7F for c in code + maker):
        return None
    game_id = (code + maker).decode("ascii")
    fname = _cstr(hdr[0x08:0x28])
    img_off, = struct.unpack(">I", hdr[0x2C:0x30])
    icon_fmt, anim, = struct.unpack(">HH", hdr[0x30:0x34])
    blocks, = struct.unpack(">H", hdr[0x38:0x3A])
    com_off, = struct.unpack(">I", hdr[0x3C:0x40])
    sjis = code[3:4] == b"J"
    title = detail = ""
    if com_off + 64 <= len(data):
        dec = _sjis if sjis else (lambda b: _cstr(b, "cp1252"))
        title, detail = dec(data[com_off:com_off + 32]), dec(data[com_off + 32:com_off + 64])
    icon = banner = None
    if img_off != 0xFFFFFFFF and img_off < len(data):
        try:
            banner, icon = _gc_images(data, img_off, hdr[7] & 3, icon_fmt, anim)
        except (IndexError, ValueError):
            pass
    return GCSave(game_id, fname, title, detail, blocks, icon, banner)


def _gc_images(data, off, banner_fmt, icon_fmt, anim):
    banner = None
    if banner_fmt == 1:                                    # CI8 + palette
        px = _decode_ci8(data[off:off + 3072], data[off + 3072:off + 3584], 96, 32)
        off += 3584
        banner = Icon("rgba", px, 96, 32) if px else None
    elif banner_fmt == 2:                                  # RGB5A3
        px = _decode_rgb5a3(data[off:off + 6144], 96, 32)
        off += 6144
        banner = Icon("rgba", px, 96, 32) if px else None
    frames = []
    for i in range(8):
        fmt, speed = (icon_fmt >> 2 * i) & 3, (anim >> 2 * i) & 3
        if fmt == 0 or (speed == 0 and i > 0):
            break
        frames.append((fmt, off))
        off += {1: 1024, 2: 2048, 3: 1536}[fmt]
        if speed == 0:
            break
    shared_pal = data[off:off + 512]                       # after all icons, for CI8 "shared"
    icon = None
    if frames:
        fmt, o = frames[0]
        if fmt == 2:
            px = _decode_rgb5a3(data[o:o + 2048], 32, 32)
        elif fmt == 3:
            px = _decode_ci8(data[o:o + 1024], data[o + 1024:o + 1536], 32, 32)
        else:
            px = _decode_ci8(data[o:o + 1024], shared_pal, 32, 32)
        icon = Icon("rgba", px, 32, 32) if px else None
    return banner, icon


def parse_gci(path: Path) -> GCSave | None:
    b = _read(path)
    return parse_gc_entry(b[:0x40], b[0x40:]) if len(b) > 0x40 else None


def parse_gc_card(path: Path) -> list[GCSave] | None:
    """A whole GameCube memory card image (.raw/.gcp/.mci); None if it isn't one."""
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size < 5 * GC_BLOCK or size % GC_BLOCK or size > 16 * 1024 * 1024 + GC_BLOCK:
        return None
    b = _read(path, size)
    dirs = [b[GC_BLOCK * k:GC_BLOCK * (k + 1)] for k in (1, 2)]
    counters = [struct.unpack(">h", d[0x1FFA:0x1FFC])[0] for d in dirs]
    d = dirs[0] if counters[0] >= counters[1] else dirs[1]
    out = []
    for n in range(127):
        e = d[n * 0x40:(n + 1) * 0x40]
        if e[:4] == b"\xff\xff\xff\xff":
            continue
        first, = struct.unpack(">H", e[0x36:0x38])
        count, = struct.unpack(">H", e[0x38:0x3A])
        if not (5 <= first < size // GC_BLOCK) or count == 0:
            continue
        s = parse_gc_entry(e, b[first * GC_BLOCK:(first + count) * GC_BLOCK])
        if s:
            out.append(s)
    return out


# ---------------------------------------------------------------------------
# Wii
# ---------------------------------------------------------------------------
def parse_wii_banner(path: Path):
    """banner.bin -> (title, subtitle, icon, banner) or None."""
    b = _read(path, 0xA0 + 0x6000 + 0x1200)
    if len(b) < 0xA0 or b[:4] != b"WIBN":
        return None

    def u16(s):
        try:
            return " ".join(s.decode("utf-16-be").split("\0", 1)[0].split())
        except UnicodeDecodeError:
            return ""
    title, sub = u16(b[0x20:0x60]), u16(b[0x60:0xA0])
    banner = icon = None
    px = _decode_rgb5a3(b[0xA0:0xA0 + 0x6000], 192, 64)
    if px:
        banner = Icon("rgba", px, 192, 64)
    px = _decode_rgb5a3(b[0x60A0:0x60A0 + 0x1200], 48, 48)
    if px:
        icon = Icon("rgba", px, 48, 48)
    return title, sub, icon, banner


def wii_game_code(hex8: str) -> str:
    try:
        code = bytes.fromhex(hex8).decode("ascii")
    except (ValueError, UnicodeDecodeError):
        return hex8.upper()
    return code if all(c.isalnum() for c in code) else hex8.upper()


# ---------------------------------------------------------------------------
# PSP / PS3: PARAM.SFO, trophies
# ---------------------------------------------------------------------------
def parse_sfo(path_or_bytes) -> dict:
    b = path_or_bytes if isinstance(path_or_bytes, (bytes, bytearray)) else _read(path_or_bytes, 64 * 1024)
    if len(b) < 0x14 or b[:4] != b"\0PSF":
        return {}
    key_tab, data_tab, n = struct.unpack("<III", b[8:20])
    out = {}
    for i in range(min(n, 256)):
        e = b[0x14 + 16 * i:0x14 + 16 * (i + 1)]
        if len(e) < 16:
            break
        koff, fmt, dlen, _dmax, doff = struct.unpack("<HHIII", e)
        key = _cstr(b[key_tab + koff:key_tab + koff + 64])
        raw = b[data_tab + doff:data_tab + doff + dlen]
        if fmt == 0x0404 and len(raw) >= 4:
            out[key] = struct.unpack("<I", raw[:4])[0]
        elif fmt in (0x0204, 0x0004):
            try:
                out[key] = raw.split(b"\0", 1)[0].decode("utf-8").strip()
            except UnicodeDecodeError:
                out[key] = raw.split(b"\0", 1)[0].decode("latin-1").strip()
    return out


def parse_tropconf(path: Path) -> str:
    t = _read(path, 512 * 1024).decode("utf-8", "replace")
    m = re.search(r"<title-name>(.*?)</title-name>", t, re.S)
    if not m:
        return ""
    s = m.group(1)
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'")):
        s = s.replace(a, b)
    return " ".join(s.split())


# ---------------------------------------------------------------------------
# PS1 memory cards
# ---------------------------------------------------------------------------
PS1_CARD = 128 * 1024


def ps1_serial(name: str) -> str:
    """'BASLUS-00067DRAX' -> 'SLUS-00067'."""
    m = re.match(r"^B[AEIJ]([A-Z]{4})[-_ ]?(\d{3})[.]?(\d{2})", name)
    return f"{m.group(1)}-{m.group(2)}{m.group(3)}" if m else ""


def parse_ps1_card(path: Path) -> list[Inner] | None:
    try:
        size = path.stat().st_size
    except OSError:
        return None
    b = _read(path, PS1_CARD + 0x1000)
    off = 0
    if size == PS1_CARD + 3904 and b[:2] != b"MC":           # DexDrive .gme header
        off = 3904
    elif size == PS1_CARD + 64 and b[64:66] == b"MC":         # .vmp / .mcx style headers
        off = 64
    card = b[off:off + PS1_CARD]
    if len(card) < PS1_CARD or card[:2] != b"MC":
        return None
    out = []
    for i in range(1, 16):
        fr = card[0x80 * i:0x80 * (i + 1)]
        state, fsize = struct.unpack("<II", fr[:8])
        if state != 0x51:
            continue
        fname = _cstr(fr[0x0A:0x1F], "ascii")
        blk = card[0x2000 * i:0x2000 * (i + 1)]
        title = _sjis(blk[4:68]) if blk[:2] == b"SC" else ""
        icon = _ps1_icon(blk) if blk[:2] == b"SC" else None
        # the save's own text ("CASTLEVANIA-1 ALUCARD 200%") is its detail; the game's name
        # comes from the product code via titledb
        out.append(Inner(title="", detail=title or fname, game_id=ps1_serial(fname),
                         icon=icon, blocks=max(1, fsize // 0x2000)))
    return out


def _ps1_icon(blk: bytes) -> Icon | None:
    if len(blk) < 0x100:
        return None
    pal = []
    for k in range(16):
        v, = struct.unpack("<H", blk[0x60 + 2 * k:0x62 + 2 * k])
        r, g, b = v & 31, (v >> 5) & 31, (v >> 10) & 31
        pal.append(bytes((r << 3 | r >> 2, g << 3 | g >> 2, b << 3 | b >> 2, 0 if v == 0 else 255)))
    out = bytearray()
    for byte in blk[0x80:0x100]:
        out += pal[byte & 15] + pal[byte >> 4]
    return Icon("rgba", bytes(out), 16, 16)


# ---------------------------------------------------------------------------
# Roles for files inside a save folder
# ---------------------------------------------------------------------------
_ROLES = {
    "param.sfo": "Save information (title, description)", "icon0.png": "Icon",
    "icon1.pmf": "Animated icon", "icon1.pam": "Animated icon", "pic1.png": "Background picture",
    "snd0.at3": "Background sound", "param.pbp": "Save information", "banner.bin": "Title, description and icon",
    "tropconf.sfm": "Trophy list", "tropusr.dat": "Trophies earned", "troptrns.dat": "Trophy history",
    "trophy.trp": "Trophy pictures", "icon.sys": "Icon settings", "savedata.bin": "Save data",
    "data.bin": "Save data", "system.dat": "Save data", "saveinfo.xml": "Save information",
}


def role_for(name: str, kind: str = "save") -> str:
    low = name.lower()
    if low in _ROLES:
        return _ROLES[low]
    ext = PurePosixPath(low).suffix
    if ext in (".png", ".jpg", ".jpeg", ".bmp"):
        return "Screenshot" if kind == "state" else "Picture"
    if ext in (".ico", ".icn"):
        return "Icon"
    if ext == ".undo":
        return "Previous state (undo)"
    if ext == ".backup":
        return "Previous state (backup)"
    if ext == ".dtm":
        return "Input recording"
    return "Save state" if kind == "state" else "Save data"


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------
_RE_DOLPHIN_STATE = re.compile(r"^([A-Z0-9]{6})\.s(\d{2})(\.dtm)?$", re.I)
_RE_PPSSPP_STATE = re.compile(r"^([A-Z]{4}\d{5})_([\d.]+)_(\d+)\.(ppst|jpg|ppst\.undo|undo)$", re.I)
_RE_DUCK_STATE = re.compile(r"^([A-Z]{4}-\d{5})_(resume|\d+)\.sav$", re.I)
_RE_PCSX2_STATE = re.compile(r"^([A-Z]{4}-\d{5}) \(([0-9A-F]{8})\)\.(\d+)\.p2s(\.backup)?$", re.I)
_RE_CITRA_STATE = re.compile(r"^(0004[0-9A-F]{12})\.(\d+)\.cst$", re.I)
_RE_RA = re.compile(r"^(.+?)\.(srm|sav|rtc|eep|fla|sra|mpk|state(\d*)(\.auto)?(\.png)?|ml(\d)|mcr)$", re.I)
_RE_HEX16 = re.compile(r"^[0-9A-Fa-f]{16}$")
_RE_HEX8 = re.compile(r"^[0-9A-Fa-f]{8}$")
_RE_SERIAL = re.compile(r"^([A-Z]{4})-?(\d{5})")

_GC_EXT = {".gci"}
_GC_CARD_EXT = {".raw", ".gcp", ".mci"}
_PS1_CARD_EXT = {".mcd", ".mcr", ".mc", ".gme", ".vmp", ".mcx", ".srm"}

SWITCH_SYSTEM_SAVES = re.compile(r"^0{8}")               # 0000000000000000 = the "device" save folder


def psp_serial(folder: str) -> str:
    m = re.match(r"^([A-Z]{4})(\d{5})", folder.upper())
    return f"{m.group(1)}{m.group(2)}" if m else ""


def identify(emu: str, located: list) -> tuple[list[SaveGroup], list]:
    """located: [(location label, base Path, [rel posix str, ...])].
    Returns (groups, leftover) where leftover is [(label, rel)] not grouped."""
    groups: dict[str, SaveGroup] = {}
    leftover = []
    learned = {}                  # game id -> title seen in this emulator's own saves

    def grp(key, **kw) -> SaveGroup:
        g = groups.get(key)
        if g is None:
            g = groups[key] = SaveGroup(key=key, **kw)
        return g

    pending_states = []           # resolved after saves (they borrow titles from saves)

    for label, base, rels in located:
        if base is None or not rels:           # location not found on this machine
            continue
        base = Path(base)
        by_dir: dict[str, list[str]] = {}
        for rel in rels:
            by_dir.setdefault(str(PurePosixPath(rel).parent), []).append(rel)
        for rel in rels:
            p = PurePosixPath(rel)
            parts = p.parts
            name, low, ext = p.name, p.name.lower(), p.suffix.lower()
            full = base / rel
            handled = True

            # --- GameCube ---------------------------------------------------
            if ext in _GC_EXT:
                s = parse_gci(full)
                if s:
                    g = grp(f"gc:{label}:{rel}", system="GameCube", game_id=s.game_id,
                            title=s.title, detail=s.detail, icon=s.icon, banner=s.banner,
                            id_kind="gc", needs_title=not s.title)
                    g.files.append((label, rel, "Save data"))
                    if s.title:
                        learned.setdefault(s.game_id, s.title)
                        learned.setdefault(s.game_id[:4], s.title)
                else:
                    handled = False
            elif ext in _GC_CARD_EXT and emu in ("Dolphin", "PrimeHack"):
                saves = parse_gc_card(full)
                if saves is not None:
                    g = grp(f"gccard:{label}:{rel}", system="GameCube", kind="card",
                            title=f"Memory card {_card_name(name)}",
                            detail=f"{len(saves)} save(s)")
                    g.files.append((label, rel, "Memory card image"))
                    for s in saves:
                        g.inner.append(Inner(s.title, s.detail or s.filename, s.game_id, s.icon, s.blocks))
                        if s.title:
                            learned.setdefault(s.game_id, s.title)
                else:
                    handled = False
            # --- PS1 memory cards ------------------------------------------
            elif ext in _PS1_CARD_EXT and (ext != ".srm" or _size(full) in (PS1_CARD, PS1_CARD + 64)):
                saves = parse_ps1_card(full)
                if saves is not None:
                    g = grp(f"ps1card:{label}:{rel}", system="PlayStation", kind="card",
                            title=p.stem, detail=f"Memory card · {len(saves)} save(s)")
                    g.files.append((label, rel, "Memory card image"))
                    g.inner.extend(saves)
                else:
                    handled = False
            else:
                handled = False
            if handled:
                continue

            # --- Wii: title/<hi>/<lo>/data/... ------------------------------
            m = _find_seq(parts, lambda a, b, c: a.lower() == "title" and _RE_HEX8.match(b) and _RE_HEX8.match(c))
            if m is not None and emu in ("Dolphin", "PrimeHack"):
                hi, lo = parts[m + 1].lower(), parts[m + 2].lower()
                root = "/".join(parts[:m + 3])
                g = grp(f"wii:{label}:{root}", system="Wii", game_id=wii_game_code(lo),
                        id_kind="wii", needs_title=True)
                if hi == "00010005":
                    g.detail = "Downloadable content"
                if low == "banner.bin":
                    info = parse_wii_banner(full)
                    if info:
                        g.title, sub, g.icon, g.banner = info[0], info[1], info[2], info[3]
                        g.detail = sub or g.detail
                        g.needs_title = not g.title
                        if g.title:
                            learned.setdefault(g.game_id, g.title)
                g.files.append((label, rel, role_for(name)))
                continue

            # --- 3DS (Azahar): title/0004xxxx/<lo>/... and extdata ----------
            if emu == "Azahar":
                m = _find_seq(parts, lambda a, b, c: a.lower() == "title" and _RE_HEX8.match(b)
                              and _RE_HEX8.match(c) and b.lower().startswith("0004"))
                if m is not None:
                    tid = (parts[m + 1] + parts[m + 2]).upper()
                    root = "/".join(parts[:m + 3])
                    g = grp(f"3ds:{label}:{root}", system="3DS", game_id=tid, id_kind="3ds",
                            needs_title=True)
                    sub = parts[m + 3].lower() if len(parts) > m + 4 else ""
                    g.files.append((label, rel, "Save data" if sub == "data" else
                                    "Installed content" if sub == "content" else "Save data"))
                    continue
                m = _find_seq(parts, lambda a, b, c: a.lower() == "extdata" and _RE_HEX8.match(b) and _RE_HEX8.match(c))
                if m is not None:
                    xid = parts[m + 2].upper()
                    root = "/".join(parts[:m + 3])
                    g = grp(f"3dsx:{label}:{root}", system="3DS", game_id=xid, id_kind="3ds-unique",
                            detail="Extra data", needs_title=True)
                    g.files.append((label, rel, "Extra data"))
                    continue
                ms = _RE_CITRA_STATE.match(name)
                if ms:
                    tid = ms.group(1).upper()
                    g = grp(f"3dsstate:{label}:{tid}:{ms.group(2)}", system="3DS", game_id=tid,
                            id_kind="3ds", kind="state", detail=f"Save state, slot {int(ms.group(2))}",
                            needs_title=True)
                    g.files.append((label, rel, "Save state"))
                    continue

            # --- Switch (Yuzu): .../<user>/<16-hex title id>/... -------------
            if emu == "Yuzu":
                if label == "Profiles":
                    g = grp(f"yuzuprof:{label}", system="Switch", kind="profile", title="User profile pictures")
                    g.files.append((label, rel, "Profile picture"))
                    continue
                idx = next((k for k, part in enumerate(parts[:-1]) if _RE_HEX16.match(part)
                            and not SWITCH_SYSTEM_SAVES.match(part)), None)
                if idx is not None:
                    tid = parts[idx].upper()
                    root = "/".join(parts[:idx + 1])
                    g = grp(f"switch:{label}:{root}", system="Switch", game_id=tid, id_kind="switch",
                            needs_title=True)
                    g.files.append((label, rel, "Save data"))
                    continue

            # --- Wii U (Cemu): <hi>/<lo>/user/<persistent id or common>/... -
            if emu == "Cemu":
                m = _find_seq(parts, lambda a, b, c: _RE_HEX8.match(a) and _RE_HEX8.match(b) and c.lower() in ("user", "meta"))
                if m is not None:
                    tid = (parts[m] + parts[m + 1]).upper()
                    root = "/".join(parts[:m + 2])
                    g = grp(f"wiiu:{label}:{root}", system="Wii U", game_id=tid, id_kind="wiiu",
                            needs_title=True)
                    who = parts[m + 3] if len(parts) > m + 4 else ""
                    role = ("Shared by all users" if who.lower() == "common" else
                            f"Save data (user {who})" if who else role_for(name))
                    if parts[m + 2].lower() == "meta":
                        role = "Save information"
                    g.files.append((label, rel, role))
                    continue

            # --- Xbox 360 (Xenia): <xuid>/<title id>/00000001/<save>/... ----
            if emu == "Xenia":
                m = _find_seq(parts, lambda a, b: _RE_HEX8.match(a) and b == "00000001")
                if m is not None and len(parts) > m + 2:
                    tid = parts[m].upper()
                    save = parts[m + 2]
                    root = "/".join(parts[:m + 3])
                    g = grp(f"x360:{label}:{root}", system="Xbox 360", game_id=tid, id_kind="x360",
                            detail=save if save != name else "", needs_title=True)
                    g.files.append((label, rel, "Save data"))
                    continue
                m = _find_seq(parts, lambda a, b: a.upper() == "FFFE07D1" and b == "00010000")
                if m is not None:
                    g = grp(f"x360prof:{label}", system="Xbox 360", kind="profile", title="Xbox 360 profile")
                    g.files.append((label, rel, "Profile"))
                    continue

            # --- PSP / PS3 save folders: a folder with PARAM.SFO ------------
            folder = str(p.parent)
            siblings = by_dir.get(folder, [])
            if folder != "." and any(PurePosixPath(s).name.upper() == "PARAM.SFO" for s in siblings):
                system = "PS3" if emu == "RPCS3" else "PSP"
                g = groups.get(f"sfo:{label}:{folder}")
                if g is None:
                    sfo = parse_sfo(base / folder / "PARAM.SFO")
                    serial = (sfo.get("TITLE_ID") or sfo.get("SAVEDATA_DIRECTORY", "")[:9]
                              or psp_serial(p.parent.name) or p.parent.name[:9])
                    serial = serial.upper()
                    title = sfo.get("TITLE", "")
                    detail = " · ".join(x for x in (sfo.get("SAVEDATA_TITLE") or sfo.get("SUB_TITLE", ""),
                                                    sfo.get("SAVEDATA_DETAIL") or sfo.get("DETAIL", ""))
                                        if x)
                    icon_path = base / folder / "ICON0.PNG"
                    icon = None
                    if icon_path.is_file():
                        data = _read(icon_path, 512 * 1024)
                        icon = Icon("png", data) if data[:8] == b"\x89PNG\r\n\x1a\n" else None
                    g = grp(f"sfo:{label}:{folder}", system=system, game_id=serial, title=title,
                            detail=_one_line(detail), icon=icon, id_kind=system.lower(),
                            needs_title=not title)
                    if title:
                        learned.setdefault(serial, title)
                        learned.setdefault(serial.replace("-", ""), title)
                g.files.append((label, rel, role_for(name)))
                continue

            # --- PS3 trophies ----------------------------------------------
            if emu == "RPCS3" and folder != "." and any(PurePosixPath(s).name.upper() == "TROPCONF.SFM" for s in siblings):
                g = groups.get(f"trophy:{label}:{folder}")
                if g is None:
                    title = parse_tropconf(base / folder / "TROPCONF.SFM")
                    icon_path = base / folder / "ICON0.PNG"
                    data = _read(icon_path, 512 * 1024) if icon_path.is_file() else b""
                    g = grp(f"trophy:{label}:{folder}", system="PS3", kind="trophy",
                            game_id=p.parent.name.upper(), title=title, detail="Trophies",
                            icon=Icon("png", data) if data[:4] == b"\x89PNG" else None)
                g.files.append((label, rel, role_for(name)))
                continue

            # --- save states (named after a game ID) and ROM-named saves ---
            pending_states.append((label, rel))

    for label, rel in pending_states:
        p = PurePosixPath(rel)
        name = p.name
        g = None
        m = _RE_DOLPHIN_STATE.match(name)
        if m and emu in ("Dolphin", "PrimeHack"):
            gid = m.group(1).upper()
            g = grp(f"dstate:{label}:{gid}:{m.group(2)}", system="GameCube / Wii", game_id=gid,
                    id_kind="gc", kind="state", detail=f"Save state, slot {int(m.group(2))}",
                    title=learned.get(gid) or learned.get(gid[:4], ""))
            g.files.append((label, rel, "Input recording" if m.group(3) else "Save state"))
        m = None if g else _RE_PPSSPP_STATE.match(name)
        if m:
            gid = m.group(1).upper()
            slot = int(m.group(3))
            g = grp(f"pstate:{label}:{gid}:{slot}", system="PSP", game_id=gid, id_kind="psp",
                    kind="state", detail=f"Save state, slot {slot + 1}", title=learned.get(gid, ""))
            g.files.append((label, rel, role_for(name, "state")))
        m = None if g else _RE_DUCK_STATE.match(name)
        if m and emu in ("DuckStation",):
            gid = m.group(1).upper()
            slot = m.group(2).lower()
            g = grp(f"dkstate:{label}:{gid}:{slot}", system="PlayStation", game_id=gid, id_kind="ps1",
                    kind="state", detail="Resume state" if slot == "resume" else f"Save state, slot {slot}")
            g.files.append((label, rel, "Save state"))
        m = None if g else _RE_PCSX2_STATE.match(name)
        if m:
            gid = m.group(1).upper()
            slot = int(m.group(3))
            g = grp(f"p2state:{label}:{gid}:{slot}", system="PlayStation 2", game_id=gid, id_kind="ps2",
                    kind="state", detail=f"Save state, slot {slot}")
            g.files.append((label, rel, "Previous state (backup)" if m.group(4) else "Save state"))
        if g is None and emu in ("RetroArch", "melonDS"):
            m = _RE_RA.match(name)
            if m:
                stem, ext = m.group(1), m.group(2).lower()
                # one row per game: its in-game save and its states (they're named after the ROM)
                g = grp(f"ra:{p.parent}/{stem}", system="RetroArch" if emu == "RetroArch" else "DS",
                        title=_clean_rom_name(stem))
                g.files.append((label, rel, _ra_role(ext)))
        if g is None:
            leftover.append((label, rel))

    # states and ROM-named files pick up titles learned from the saves above
    for g in groups.values():
        if g.key.startswith("ra:"):
            roles = [r for *_x, r in g.files]
            saves = sum(r in ("Save data", "Clock data") for r in roles)
            states = sum(r.startswith("Save state") for r in roles)
            bits = (["In-game save"] if saves else []) + (
                [f"{states} save state{'s' if states != 1 else ''}"] if states else [])
            g.detail = " · ".join(bits)
            g.kind = "save" if saves else "state"
        if not g.title and g.game_id:
            g.title = learned.get(g.game_id) or learned.get(g.game_id.replace("-", ""), "")
        g.needs_title = not g.title and bool(g.id_kind and g.game_id)
    return list(groups.values()), leftover


def _ra_role(ext: str) -> str:
    if ext.endswith(".png"):
        return "Screenshot"
    if ext.startswith("state"):
        m = re.match(r"state(\d*)(\.auto)?", ext)
        if m.group(2):
            return "Save state (automatic)"
        return f"Save state, slot {m.group(1) or 0}"
    if ext.startswith("ml"):
        return f"Save state, slot {ext[2:]}"
    if ext == "rtc":
        return "Clock data"
    return "Save data"


def _card_name(fname: str) -> str:
    m = re.match(r"^MemoryCard([AB])(?:\.([A-Z]{3}))?", fname, re.I)
    if m:
        return m.group(1).upper() + (f" ({m.group(2).upper()})" if m.group(2) else "")
    return PurePosixPath(fname).stem


def _clean_rom_name(stem: str) -> str:
    s = re.sub(r"\s*\[[^\]]*\]", "", stem)
    return " ".join(s.split()) or stem


def _one_line(s: str) -> str:
    return " ".join(s.split())


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return -1


def _find_seq(parts, test):
    n = test.__code__.co_argcount
    for k in range(len(parts) - n + 1):
        try:
            if test(*parts[k:k + n]):
                return k
        except IndexError:
            continue
    return None


def apply_titles(groups: list[SaveGroup], titles) -> None:
    """Fill in game names from a TitleDB (or anything with .lookup(id_kind, game_id))."""
    if titles is None:
        return
    for g in groups:
        if g.needs_title and g.game_id:
            t = titles.lookup(g.id_kind, g.game_id)
            if t:
                g.title = t
        for inner in g.inner:
            if inner.game_id and not inner.title:
                t = titles.lookup("ps1" if g.system == "PlayStation" else "gc", inner.game_id)
                if t:
                    inner.title = t
