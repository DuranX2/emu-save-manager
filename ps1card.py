"""
ps1card.py - PlayStation (PS1) memory cards: read, copy, move, delete and export saves safely.

A card is 128 KB: 16 blocks of 8 KB, each 64 frames of 128 bytes. Block 0 is the directory:
frame 0 is the "MC" header, frames 1-15 describe blocks 1-15 (state, size, next block, file name,
XOR checksum). A save uses one or more blocks, linked through the directory; its first block
starts with the "SC" title frame (icon frame count, Shift-JIS title, palette) and up to three
16x16 icon frames. The layout and the way saves are written and deleted follow DuckStation's
memory card code, so DuckStation reads the results exactly as its own.

Card files: raw images (.mcd .mcr .mc .srm .ps .ddf, 131072 bytes), DexDrive .gme and
VGS .mem/.vgs (their headers are kept), and PSP .vmp (read only: it's signed).
Single saves are exported as .mcs (the save's directory frame followed by its blocks), the
format DuckStation exports and imports.

Safety: every change to a card writes a new image to a temporary file, reads it back, then
replaces the card; the card as it was is copied first to <backups>/_ps1_card_safety/<time>/
(newest 20 changes kept). Deleting marks the save deleted the way the console does, so its data
stays on the card until the space is reused. Copies are verified by reading the card back.
"""
from __future__ import annotations

import os
import re
import shutil
import struct
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

CARD_SIZE = 128 * 1024
BLOCK = 8192
FRAME = 128
NUM_BLOCKS = 16
GME_HEADER = 3904
VGS_HEADER = 64
VMP_HEADER = 0x80
RAW_EXT = {".mcd", ".mcr", ".mc", ".srm", ".ps", ".ddf", ".psm", ".mci", ".bin"}
CARD_EXT = RAW_EXT | {".gme", ".mem", ".vgs", ".vmp"}
SAFETY_KEEP = 20
REGIONS = {"BA": "America", "BE": "Europe", "BI": "Japan"}

ST_FIRST, ST_MIDDLE, ST_LAST = 0x51, 0x52, 0x53
ST_FREE = 0xA0


class PS1Error(Exception):
    pass


def checksum(frame: bytes) -> int:
    c = 0
    for b in frame[:FRAME - 1]:
        c ^= b
    return c


def sjis_title(raw: bytes) -> str:
    raw = raw.split(b"\0", 1)[0]
    try:
        s = raw.decode("cp932")
    except UnicodeDecodeError:
        s = raw.decode("shift_jis", "replace")
    return " ".join(unicodedata.normalize("NFKC", s).split())


def product_code(name: str) -> str:
    """'BASLUS-00067DRAX00' -> 'SLUS-00067'."""
    m = re.match(r"^B[AEI]([A-Z]{4})[-_ ]?(\d{3})[.]?(\d{2})", name)
    return f"{m.group(1)}-{m.group(2)}{m.group(3)}" if m else ""


@dataclass
class Icon:
    data: bytes          # RGBA, 16x16
    width: int = 16
    height: int = 16


@dataclass
class PS1Save:
    name: str                       # directory file name, e.g. BASLUS-00067DRAX00
    first: int                      # first block (1-15); 0 for a save in a file
    blocks: list                    # block numbers on the card
    size: int
    title: str = ""
    frames: list = field(default_factory=list)   # [Icon]
    problem: str = ""
    path: Path | None = None        # .mcs file, for saves in the exports folder

    @property
    def n_blocks(self):
        return max(1, len(self.blocks)) if self.blocks else max(1, self.size // BLOCK)

    @property
    def product(self):
        return product_code(self.name)

    @property
    def region(self):
        return REGIONS.get(self.name[:2], "")

    @property
    def key(self):
        return self.name


def parse_title(block: bytes):
    """(title, [Icon]) from a save's first block."""
    if len(block) < 0x200 or block[:2] != b"SC":
        return "", []
    n = {0x11: 1, 0x12: 2, 0x13: 3}.get(block[2], 0)
    title = sjis_title(block[4:68])
    pal = []
    for k in range(16):
        v, = struct.unpack_from("<H", block, 0x60 + 2 * k)
        r, g, b = v & 31, (v >> 5) & 31, (v >> 10) & 31
        pal.append(bytes((r << 3 | r >> 2, g << 3 | g >> 2, b << 3 | b >> 2, 0 if v == 0 else 255)))
    frames = []
    for f in range(n):
        out = bytearray()
        for byte in block[0x80 * (f + 1):0x80 * (f + 2)]:
            out += pal[byte & 15] + pal[byte >> 4]
        frames.append(Icon(bytes(out)))
    return title, frames


# ---------------------------------------------------------------------------
# A card image
# ---------------------------------------------------------------------------
class Card:
    def __init__(self, image: bytes, header: bytes = b"", fmt: str = "raw", path: Path | None = None):
        if len(image) != CARD_SIZE:
            raise PS1Error("not a 128 KB PlayStation memory card")
        self.img = bytearray(image)
        self.header = header
        self.fmt = fmt
        self.path = Path(path) if path else None

    # ---- files -----------------------------------------------------------------
    @classmethod
    def from_bytes(cls, data: bytes, ext: str = ".mcd", path=None) -> "Card":
        ext = ext.lower()
        if len(data) == CARD_SIZE + GME_HEADER and data[GME_HEADER:GME_HEADER + 2] == b"MC":
            return cls(data[GME_HEADER:], data[:GME_HEADER], "gme", path)
        if len(data) == CARD_SIZE + VGS_HEADER and data[VGS_HEADER:VGS_HEADER + 2] == b"MC":
            return cls(data[VGS_HEADER:], data[:VGS_HEADER], "vgs", path)
        if len(data) == CARD_SIZE + VMP_HEADER and data[VMP_HEADER:VMP_HEADER + 2] == b"MC":
            return cls(data[VMP_HEADER:], data[:VMP_HEADER], "vmp", path)
        if len(data) == CARD_SIZE and data[:2] == b"MC":
            return cls(data, b"", "raw", path)
        raise PS1Error("not a PlayStation memory card")

    @classmethod
    def load(cls, path) -> "Card":
        path = Path(path)
        try:
            data = path.read_bytes()
        except OSError as ex:
            raise PS1Error(f"can't read {path.name}: {ex}") from ex
        return cls.from_bytes(data, path.suffix, path)

    @classmethod
    def blank(cls) -> "Card":
        img = bytearray(b"\xff" * CARD_SIZE)

        def frame(n):
            return n * FRAME
        img[0:FRAME] = bytes(FRAME)
        img[0:2] = b"MC"
        img[FRAME - 1] = checksum(img[0:FRAME])
        for n in range(1, 16):
            o = frame(n)
            img[o:o + FRAME] = bytes(FRAME)
            img[o] = ST_FREE
            img[o + 8] = img[o + 9] = 0xFF
            img[o + FRAME - 1] = checksum(img[o:o + FRAME])
        for n in range(16, 36):
            o = frame(n)
            img[o:o + FRAME] = bytes(FRAME)
            img[o:o + 4] = b"\xff\xff\xff\xff"
            img[o + 8] = img[o + 9] = 0xFF
            img[o + FRAME - 1] = checksum(img[o:o + FRAME])
        for n in range(36, 63):
            img[frame(n):frame(n) + FRAME] = bytes(FRAME)
        img[frame(63):frame(64)] = img[0:FRAME]
        return cls(bytes(img))

    @property
    def writable(self):
        return self.fmt != "vmp"

    def to_bytes(self) -> bytes:
        return bytes(self.header) + bytes(self.img)

    # ---- directory -------------------------------------------------------------
    def entry(self, n: int):
        o = n * FRAME
        state, size, nxt = struct.unpack_from("<IIH", self.img, o)
        name = bytes(self.img[o + 0x0A:o + 0x1F]).split(b"\0", 1)[0].decode("ascii", "replace")
        return state, size, nxt, name

    def _set_entry(self, n, state, size=0, nxt=0xFFFF, name=""):
        o = n * FRAME
        self.img[o:o + FRAME] = bytes(FRAME)
        struct.pack_into("<IIH", self.img, o, state, size, nxt)
        nm = name.encode("ascii", "replace")[:20]
        self.img[o + 0x0A:o + 0x0A + len(nm)] = nm
        self.img[o + FRAME - 1] = checksum(self.img[o:o + FRAME])

    def block_bytes(self, b: int) -> bytes:
        return bytes(self.img[b * BLOCK:(b + 1) * BLOCK])

    def free_blocks(self) -> list:
        return [n for n in range(1, 16) if (self.entry(n)[0] & 0xF0) == 0xA0]

    def saves(self) -> list:
        out, used = [], set()
        for n in range(1, 16):
            state, size, nxt, name = self.entry(n)
            if state != ST_FIRST:
                continue
            blocks, problem = [n], ""
            cur = nxt
            while cur < NUM_BLOCKS - 1:
                b = cur + 1
                if b in blocks or len(blocks) >= 15:
                    problem = "its block chain loops"
                    break
                st = self.entry(b)[0]
                if st not in (ST_MIDDLE, ST_LAST):
                    problem = "its block chain is broken"
                    break
                blocks.append(b)
                cur = self.entry(b)[2]
            if size and size != len(blocks) * BLOCK and not problem:
                problem = f"its size ({size} bytes) doesn't match its {len(blocks)} block(s)"
            title, frames = parse_title(self.block_bytes(n))
            used.update(blocks)
            out.append(PS1Save(name, n, blocks, size or len(blocks) * BLOCK, title, frames, problem))
        return out

    def find(self, name: str):
        return next((s for s in self.saves() if s.name == name), None)

    def save_data(self, s: PS1Save) -> bytes:
        return b"".join(self.block_bytes(b) for b in s.blocks)

    def directory_frame(self, s: PS1Save) -> bytes:
        o = s.first * FRAME
        return bytes(self.img[o:o + FRAME])

    # ---- changes ---------------------------------------------------------------
    def write_save(self, name: str, data: bytes) -> PS1Save:
        """Add a save (as DuckStation's WriteFile does): lowest free blocks first."""
        if not data:
            raise PS1Error("the save is empty")
        n = (len(data) + BLOCK - 1) // BLOCK
        free = self.free_blocks()
        if len(free) < n:
            raise PS1Error(f"not enough free blocks ({n} needed, {len(free)} free)")
        # a deleted save with the same name is cleared first (as DuckStation does)
        for k in range(1, 16):
            st, _sz, _nx, nm = self.entry(k)
            if nm == name and st in (0xA1, 0xA2, 0xA3):
                self._clear_chain(k)
        free = self.free_blocks()
        chosen = free[:n]
        for i, b in enumerate(chosen):
            nxt = chosen[i + 1] - 1 if i + 1 < n else 0xFFFF
            if i == 0:
                self._set_entry(b, ST_FIRST, len(data), nxt, name)
            else:
                self._set_entry(b, ST_LAST if i == n - 1 else ST_MIDDLE, 0, nxt)
            chunk = data[i * BLOCK:(i + 1) * BLOCK]
            self.img[b * BLOCK:(b + 1) * BLOCK] = chunk + bytes(BLOCK - len(chunk))
        return self.find(name)

    def _clear_chain(self, first):
        b, seen = first, set()
        while 0 < b < NUM_BLOCKS and b not in seen:
            seen.add(b)
            nxt = self.entry(b)[2]
            self._set_entry(b, ST_FREE)
            b = nxt + 1 if nxt < NUM_BLOCKS - 1 else 0

    def delete_save(self, s: PS1Save):
        """Mark a save deleted, as the console does (the data stays until the space is reused)."""
        for i, b in enumerate(s.blocks):
            state, size, _nxt, name = self.entry(b)
            new = 0xA1 if i == 0 else (0xA3 if i == len(s.blocks) - 1 else 0xA2)
            o = b * FRAME
            struct.pack_into("<IIH", self.img, o, new, size, 0xFFFF)
            self.img[o + FRAME - 1] = checksum(self.img[o:o + FRAME])


# ---------------------------------------------------------------------------
# Places saves live: a card file, or a folder of .mcs exports
# ---------------------------------------------------------------------------
def write_verified(data: bytes, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    if tmp.read_bytes() != data:
        tmp.unlink(missing_ok=True)
        raise PS1Error(f"{dest.name}: the new file didn't read back correctly")
    os.replace(tmp, dest)
    if dest.read_bytes() != data:
        raise PS1Error(f"{dest.name}: the new file didn't read back correctly")


class Safety:
    def __init__(self, backup_dir):
        self.base = Path(backup_dir) / "_ps1_card_safety"
        self.dir = None

    def _dir(self):
        if self.dir is None:
            self.dir = self.base / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            self.dir.mkdir(parents=True, exist_ok=True)
        return self.dir

    def copy(self, path: Path):
        if path.exists():
            shutil.copy2(path, self._unique(self._dir() / path.name))

    def move(self, path: Path):
        shutil.move(str(path), str(self._unique(self._dir() / path.name)))

    @staticmethod
    def _unique(p: Path) -> Path:
        n = 2
        out = p
        while out.exists():
            out = p.with_name(f"{p.stem} ({n}){p.suffix}")
            n += 1
        return out

    def prune(self):
        if self.base.is_dir():
            dirs = sorted(d for d in self.base.iterdir() if d.is_dir())
            for d in dirs[:-SAFETY_KEEP]:
                shutil.rmtree(d, ignore_errors=True)


class CardStore:
    kind = "card"

    def __init__(self, path, label=None):
        self.path = Path(path)
        self.label = label or self.path.name
        self.card = Card.load(self.path)

    def reload(self):
        self.card = Card.load(self.path)

    def saves(self):
        return self.card.saves()

    def free(self):
        return len(self.card.free_blocks())

    def read(self, s: PS1Save):
        return s.name, self.card.save_data(s)

    def find(self, name):
        return self.card.find(name)

    def commit(self, card: Card, safety: Safety):
        if not card.writable:
            raise PS1Error(f"{self.path.name} is a PSP card file, which can't be changed here")
        safety.copy(self.path)
        write_verified(card.to_bytes(), self.path)
        self.reload()


class FolderStore:
    """A folder of .mcs files (one save each)."""
    kind = "exports"

    def __init__(self, path, label="Exported saves"):
        self.path = Path(path)
        self.label = label

    def saves(self):
        out = []
        if not self.path.is_dir():
            return out
        for p in sorted(self.path.iterdir()):
            if p.suffix.lower() != ".mcs" or not p.is_file():
                continue
            s = read_mcs(p)
            if s is not None:
                out.append(s)
        out.sort(key=lambda s: (s.title.lower(), s.name))
        return out

    def free(self):
        return None

    def read(self, s: PS1Save):
        data = s.path.read_bytes()
        return s.name, data[FRAME:FRAME + s.size]

    def find(self, name):
        return next((s for s in self.saves() if s.name == name), None)


def read_mcs(p: Path):
    data = p.read_bytes()
    if len(data) <= FRAME or (len(data) - FRAME) % BLOCK or (len(data) - FRAME) // BLOCK > 15:
        return None
    state, size, _nxt = struct.unpack_from("<IIH", data, 0)
    name = data[0x0A:0x1F].split(b"\0", 1)[0].decode("ascii", "replace")
    if size < BLOCK or size % BLOCK or size // BLOCK > 15 or len(data) < FRAME + size or not name:
        return None
    title, frames = parse_title(data[FRAME:FRAME + BLOCK])
    return PS1Save(name, 0, [], size, title, frames, path=p)


def mcs_bytes(name: str, data: bytes) -> bytes:
    fr = bytearray(FRAME)
    struct.pack_into("<IIH", fr, 0, ST_FIRST, len(data), 0xFFFF)
    nm = name.encode("ascii", "replace")[:20]
    fr[0x0A:0x0A + len(nm)] = nm
    fr[FRAME - 1] = checksum(fr)
    return bytes(fr) + data


def safe_file_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .") or "save"


@dataclass
class Report:
    done: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    refused: list = field(default_factory=list)       # (PS1Save, reason)
    cancelled: bool = False
    safety: Path | None = None


def transfer(src, saves, dst, move: bool, backup_dir, conflict=None) -> Report:
    """Copy (or move) saves from one store to another. conflict(save, existing) ->
    'replace' | 'skip' | 'cancel' (default skip)."""
    rep = Report()
    safety = Safety(backup_dir)
    if src.kind == "card" and dst.kind == "card" and src.path.resolve() == dst.path.resolve():
        rep.refused = [(s, "it's already on this card") for s in saves]
        return rep
    blobs = []
    for s in saves:
        if s.problem:
            rep.refused.append((s, f"the save is damaged ({s.problem})"))
            continue
        blobs.append((s,) + src.read(s))
    if dst.kind == "card":
        card = Card(bytes(dst.card.img), dst.card.header, dst.card.fmt, dst.path)
        for s, name, data in blobs:
            old = card.find(name)
            if old is not None:
                choice = conflict(s, old) if conflict else "skip"
                if choice == "cancel":
                    rep.cancelled = True
                    break
                if choice != "replace":
                    rep.skipped.append(s)
                    continue
                card._clear_chain(old.first)
            try:
                card.write_save(name, data)
            except PS1Error as ex:
                rep.refused.append((s, str(ex)))
                continue
            rep.done.append(s)
        if rep.done:
            dst.commit(card, safety)
            for s in rep.done:                         # verify what's on the card now
                name, data = next((n, d) for x, n, d in blobs if x is s)
                got = dst.find(name)
                if got is None or dst.card.save_data(got)[:len(data)] != data:
                    raise PS1Error(f"{name}: the copy on {dst.label} doesn't match; the card was kept "
                                   f"as it was in {safety.dir}")
    else:
        dst.path.mkdir(parents=True, exist_ok=True)
        for s, name, data in blobs:
            target = dst.path / f"{safe_file_name(name)}.mcs"
            payload = mcs_bytes(name, data)
            if target.exists():
                if target.read_bytes() == payload:
                    rep.skipped.append(s)
                    continue
                old = read_mcs(target)
                choice = conflict(s, old) if (conflict and old) else "skip"
                if choice == "cancel":
                    rep.cancelled = True
                    break
                if choice != "replace":
                    rep.skipped.append(s)
                    continue
                safety.move(target)
            write_verified(payload, target)
            rep.done.append(s)
    if move and rep.done:
        if src.kind == "card":
            card = Card(bytes(src.card.img), src.card.header, src.card.fmt, src.path)
            for s in rep.done:
                cur = card.find(s.name)
                if cur is not None:
                    card.delete_save(cur)
            src.commit(card, safety)
        else:
            for s in rep.done:
                safety.move(s.path)
    rep.safety = safety.dir
    safety.prune()
    return rep


def delete(store, saves, backup_dir) -> Report:
    rep = Report()
    safety = Safety(backup_dir)
    if store.kind == "card":
        card = Card(bytes(store.card.img), store.card.header, store.card.fmt, store.path)
        for s in saves:
            cur = card.find(s.name)
            if cur is not None:
                card.delete_save(cur)
                rep.done.append(s)
        if rep.done:
            store.commit(card, safety)
    else:
        for s in saves:
            safety.move(s.path)
            rep.done.append(s)
    rep.safety = safety.dir
    safety.prune()
    return rep


def new_card(folder, stem="new_card") -> Path:
    """Create a blank card file (stem_1.mcd, stem_2.mcd, …) and return its path."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    n = 1
    while (folder / f"{stem}_{n}.mcd").exists():
        n += 1
    path = folder / f"{stem}_{n}.mcd"
    write_verified(Card.blank().to_bytes(), path)
    return path


def find_cards(folders) -> list:
    """[(label, path)] for every PS1 card file in the given (label prefix, folder) pairs."""
    out = []
    for prefix, folder in folders:
        if not folder or not Path(folder).is_dir():
            continue
        for p in sorted(Path(folder).iterdir()):
            if not p.is_file() or p.suffix.lower() not in CARD_EXT:
                continue
            try:
                size = p.stat().st_size
                if size not in (CARD_SIZE, CARD_SIZE + GME_HEADER, CARD_SIZE + VGS_HEADER, CARD_SIZE + VMP_HEADER):
                    continue
                with open(p, "rb") as f:
                    head = f.read(GME_HEADER + 2)
            except OSError:
                continue
            offs = {CARD_SIZE: 0, CARD_SIZE + GME_HEADER: GME_HEADER, CARD_SIZE + VGS_HEADER: VGS_HEADER,
                    CARD_SIZE + VMP_HEADER: VMP_HEADER}[size]
            if head[offs:offs + 2] == b"MC":
                out.append((f"{prefix}: {p.name}" if prefix else p.name, p))
    return out
