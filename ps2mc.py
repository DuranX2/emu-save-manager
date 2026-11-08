"""
ps2mc.py - read and write PlayStation 2 memory card images (.ps2).

Supports standard 8 MB cards with ECC (528-byte pages, what PCSX2 creates) and
ECC-less images (512-byte pages).  Saves can be listed, exported to / imported
from the EMS (.psu) format, copied between cards and deleted.  New blank 8 MB
cards can be created.

Format reference: Ross Ridge's public-domain "PlayStation 2 Memory Card File
System" description (from mymc).  No GUI dependencies.
"""

import os
import shutil
import struct
import tempfile
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional

MAGIC = b"Sony PS2 Memory Card Format"          # 27 bytes, followed by a space
VERSION = b"1.2.0.0"

DF_PROTECTED = 0x0008
DF_FILE = 0x0010
DF_DIR = 0x0020
DF_PSX = 0x1000
DF_HIDDEN = 0x2000
DF_EXISTS = 0x8000

MODE_DIR = 0x8427
MODE_DOTDOT = 0xA427
MODE_FILE = 0x8497

ENTRY_SIZE = 512
END = 0xFFFFFFFF       # end of chain in the FAT / "unused" in pointer tables
FREE = 0x7FFFFFFF      # free FAT entry (most significant bit clear)
ALLOCATED = 0x80000000

JST = timezone(timedelta(hours=9))


class CardError(Exception):
    pass


# ---------------------------------------------------------------------------
# ECC: a Hamming code over each 128-byte chunk of a page (3 bytes per chunk)
# ---------------------------------------------------------------------------
_PAR = [bin(i).count("1") & 1 for i in range(256)]
_CPMASKS = (0x55, 0x33, 0x0F, 0x00, 0xAA, 0xCC, 0xF0)
_COL = [sum(_PAR[b & m] << i for i, m in enumerate(_CPMASKS)) for b in range(256)]
_ecc_cache = {}


def ecc_chunk(chunk) -> bytes:
    """3 ECC bytes for a 128-byte chunk."""
    chunk = bytes(chunk)
    r = _ecc_cache.get(chunk)
    if r is None:
        cp, lp0, lp1 = 0x77, 0x7F, 0x7F
        for i, b in enumerate(chunk):
            cp ^= _COL[b]
            if _PAR[b]:
                lp0 ^= ~i
                lp1 ^= i
        r = bytes((cp & 0xFF, lp0 & 0x7F, lp1 & 0x7F))
        if len(_ecc_cache) < 4096:
            _ecc_cache[chunk] = r
    return r


def ecc_page(data: bytes) -> bytes:
    """12 ECC bytes for a 512-byte page."""
    return b"".join(ecc_chunk(data[i * 128:(i + 1) * 128]) for i in range(4))


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def now_stamp() -> bytes:
    t = datetime.now(JST)
    return bytes((0, t.second, t.minute, t.hour, t.day, t.month)) + struct.pack("<H", t.year)


def stamp_to_dt(b: bytes) -> Optional[datetime]:
    try:
        return datetime(struct.unpack_from("<H", b, 6)[0], b[5], b[4], b[3], b[2], b[1])
    except (ValueError, struct.error, IndexError):
        return None


def check_name(name: str):
    bad = set('/\\?*')
    if not name or name in (".", "..") or len(name.encode("latin-1", "replace")) > 31:
        raise CardError(f"Invalid name: {name!r}")
    if any(c in bad or ord(c) < 32 for c in name):
        raise CardError(f"Invalid characters in name: {name!r}")


def title_from_icon_sys(data: bytes) -> str:
    """Human readable save title from an icon.sys file."""
    if len(data) < 0x100 or data[:4] != b"PS2D":
        return ""
    brk = struct.unpack_from("<H", data, 6)[0]
    raw = bytes(data[0xC0:0xC0 + 68]).split(b"\0", 1)[0]
    if 0 < brk < len(raw):
        raw = raw[:brk] + b" " + raw[brk:]
    try:
        text = raw.decode("shift_jis", errors="replace")
    except Exception:
        text = raw.decode("latin-1", errors="replace")
    return unicodedata.normalize("NFKC", text).strip()


# ---------------------------------------------------------------------------
# Directory entries
# ---------------------------------------------------------------------------
@dataclass
class DirEntry:
    mode: int = 0
    length: int = 0
    created: bytes = bytes(8)
    cluster: int = 0
    dir_entry: int = 0
    modified: bytes = bytes(8)
    attr: int = 0
    name: str = ""


def parse_entry(b) -> DirEntry:
    mode, _unused, length = struct.unpack_from("<HHI", b, 0)
    cluster, dir_entry = struct.unpack_from("<II", b, 0x10)
    (attr,) = struct.unpack_from("<I", b, 0x20)
    name = bytes(b[0x40:0x60]).split(b"\0", 1)[0].decode("latin-1")
    return DirEntry(mode, length, bytes(b[8:16]), cluster, dir_entry,
                    bytes(b[0x18:0x20]), attr, name)


def pack_entry(e: DirEntry) -> bytes:
    b = bytearray(ENTRY_SIZE)
    struct.pack_into("<HHI", b, 0, e.mode, 0, e.length)
    b[8:16] = e.created
    struct.pack_into("<II", b, 0x10, e.cluster, e.dir_entry)
    b[0x18:0x20] = e.modified
    struct.pack_into("<I", b, 0x20, e.attr)
    nb = e.name.encode("latin-1", "replace")[:31]
    b[0x40:0x40 + len(nb)] = nb
    return bytes(b)


# ---------------------------------------------------------------------------
# Raw page / cluster writing (also used while formatting)
# ---------------------------------------------------------------------------
def _write_page(raw: bytearray, stride: int, page: int, data: bytes):
    off = page * stride
    raw[off:off + 512] = data
    if stride == 528:
        raw[off + 512:off + 524] = ecc_page(data)


def _write_cluster(raw: bytearray, stride: int, cluster: int, data: bytes):
    data = bytes(data).ljust(1024, b"\0")
    _write_page(raw, stride, cluster * 2, data[:512])
    _write_page(raw, stride, cluster * 2 + 1, data[512:1024])


# ---------------------------------------------------------------------------
# Save containers
# ---------------------------------------------------------------------------
@dataclass
class PSUFile:
    name: str
    mode: int
    created: bytes
    modified: bytes
    attr: int
    data: bytes


@dataclass
class PSUSave:
    dirname: str
    mode: int
    created: bytes
    modified: bytes
    attr: int
    files: List[PSUFile] = field(default_factory=list)
    dot: Optional[DirEntry] = None
    dotdot: Optional[DirEntry] = None

    def title(self) -> str:
        for f in self.files:
            if f.name.lower() == "icon.sys":
                return title_from_icon_sys(f.data)
        return ""

    def size_kb(self) -> int:
        return -(-(len(self.files) + 2) // 2) + sum(-(-len(f.data) // 1024) for f in self.files)


@dataclass
class SaveInfo:
    dirname: str
    title: str
    size_kb: int
    files: int
    modified: Optional[datetime]
    protected: bool
    psx: bool
    hidden: bool


def parse_psu(data: bytes) -> PSUSave:
    if len(data) < ENTRY_SIZE * 3:
        raise CardError("File is too small to be a .psu save")
    head = parse_entry(data[:ENTRY_SIZE])
    if not head.mode & DF_DIR or not (3 <= head.length <= 4096):
        raise CardError("Not a valid .psu save file")
    check_name(head.name)
    save = PSUSave(head.name, head.mode, head.created, head.modified, head.attr)
    off = ENTRY_SIZE
    for _ in range(head.length):
        if off + ENTRY_SIZE > len(data):
            raise CardError("The .psu file is truncated")
        e = parse_entry(data[off:off + ENTRY_SIZE])
        off += ENTRY_SIZE
        if e.mode & DF_FILE:
            payload = data[off:off + e.length]
            if len(payload) != e.length:
                raise CardError("The .psu file is truncated")
            off += -(-e.length // 1024) * 1024
            check_name(e.name)
            save.files.append(PSUFile(e.name, e.mode, e.created, e.modified, e.attr, bytes(payload)))
        elif e.name == ".":
            save.dot = e
        elif e.name == "..":
            save.dotdot = e
    return save


def build_psu(save: PSUSave) -> bytes:
    out = bytearray()
    out += pack_entry(DirEntry(save.mode, len(save.files) + 2, save.created, 0, 0,
                               save.modified, save.attr, save.dirname))
    for name, ent, mode in (("." , save.dot, MODE_DIR), ("..", save.dotdot, MODE_DOTDOT)):
        out += pack_entry(DirEntry(ent.mode if ent else mode, 0,
                                   ent.created if ent else save.created, 0, 0,
                                   ent.modified if ent else save.modified, 0, name))
    for f in save.files:
        out += pack_entry(DirEntry(f.mode, len(f.data), f.created, 0, 0, f.modified, f.attr, f.name))
        out += f.data + bytes(-len(f.data) % 1024)
    return bytes(out)


# ---------------------------------------------------------------------------
# The card
# ---------------------------------------------------------------------------
class PS2Card:
    def __init__(self, data):
        self.raw = bytearray(data)
        self._parse()

    # ---- construction ------------------------------------------------------
    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            return cls(f.read())

    @classmethod
    def create_blank(cls):
        """A freshly formatted standard 8 MB card with ECC (like PCSX2 makes)."""
        cpc, stride = 8192, 528
        alloc_offset, alloc_end = 41, 8135
        ifc_cluster = 8
        fat_clusters = list(range(9, 41))
        erased = b"\xFF" * 512
        page = erased + ecc_page(erased) + b"\xFF" * 4
        raw = bytearray(page * (cpc * 2))

        sb = bytearray(b"\xFF" * 512)
        sb[0:27] = MAGIC
        sb[27] = 0x20
        sb[28:40] = VERSION.ljust(12, b"\0")
        struct.pack_into("<HHHH", sb, 0x28, 512, 2, 16, 0xFF00)
        struct.pack_into("<IIIIII", sb, 0x30, cpc, alloc_offset, alloc_end, 0, 1023, 1022)
        struct.pack_into("<32I", sb, 0x50, *([ifc_cluster] + [END] * 31))
        struct.pack_into("<32I", sb, 0xD0, *([END] * 32))
        sb[0x150] = 2
        sb[0x151] = 0x2B
        _write_page(raw, stride, 0, bytes(sb))

        ifc = fat_clusters + [END] * (256 - len(fat_clusters))
        _write_cluster(raw, stride, ifc_cluster, struct.pack("<256I", *ifc))

        # Entries past alloc_end don't correspond to real clusters: keep them non-free.
        fat = [FREE] * alloc_end + [END] * (len(fat_clusters) * 256 - alloc_end)
        fat[0] = END                                   # the root directory's only cluster
        for i, c in enumerate(fat_clusters):
            _write_cluster(raw, stride, c, struct.pack("<256I", *fat[i * 256:(i + 1) * 256]))

        ts = now_stamp()
        root = (pack_entry(DirEntry(MODE_DIR, 2, ts, 0, 0, ts, 0, "."))
                + pack_entry(DirEntry(MODE_DOTDOT, 0, ts, 0, 0, ts, 0, "..")))
        _write_cluster(raw, stride, alloc_offset, root)
        return cls(raw)

    # ---- parsing -----------------------------------------------------------
    def _parse(self):
        raw = self.raw
        if len(raw) < 1056 or bytes(raw[:27]) != MAGIC:
            raise CardError("Not a formatted PS2 memory card image")
        page_len, ppc, ppb = struct.unpack_from("<HHH", raw, 0x28)
        cpc, alloc_offset, alloc_end, rootdir = struct.unpack_from("<IIII", raw, 0x30)
        if page_len != 512 or ppc != 2 or ppb != 16:
            raise CardError("Unsupported memory card geometry")
        if rootdir != 0:
            raise CardError("Unsupported root directory location")
        pages = cpc * ppc
        if len(raw) == pages * 528:
            self.stride = 528
        elif len(raw) == pages * 512:
            self.stride = 512
        else:
            raise CardError("File size does not match the card geometry")
        self.has_ecc = self.stride == 528
        self.cluster_size = 1024
        self.clusters_per_card = cpc
        self.alloc_offset = alloc_offset
        self.n_alloc = min(alloc_end, cpc - alloc_offset)
        self.ifc_list = struct.unpack_from("<32I", raw, 0x50)
        self._load_fat()

    def _load_fat(self):
        per = self.cluster_size // 4
        needed = -(-self.n_alloc // per)
        pointers = []
        for ifc in self.ifc_list:
            if ifc >= self.clusters_per_card:
                continue
            for v in struct.unpack("<%dI" % per, self.read_cluster(ifc)):
                if v < self.clusters_per_card:
                    pointers.append(v)
            if len(pointers) >= needed:
                break
        pointers = pointers[:needed]
        if len(pointers) < needed:
            raise CardError("Corrupt FAT (missing indirect FAT entries)")
        self.fat_clusters = pointers
        fat = []
        for c in pointers:
            fat.extend(struct.unpack("<%dI" % per, self.read_cluster(c)))
        self.fat = fat

    # ---- raw cluster access --------------------------------------------------
    def read_cluster(self, c: int) -> bytes:
        if not 0 <= c < self.clusters_per_card:
            raise CardError(f"Cluster {c} out of range")
        out = bytearray()
        for p in (c * 2, c * 2 + 1):
            off = p * self.stride
            out += self.raw[off:off + 512]
        return bytes(out)

    def write_cluster(self, c: int, data: bytes):
        if not 0 <= c < self.clusters_per_card:
            raise CardError(f"Cluster {c} out of range")
        _write_cluster(self.raw, self.stride, c, data)

    def _read_rel(self, c: int) -> bytes:
        return self.read_cluster(self.alloc_offset + c)

    def _write_rel(self, c: int, data: bytes):
        self.write_cluster(self.alloc_offset + c, data)

    def _flush_fat(self):
        per = self.cluster_size // 4
        for i, c in enumerate(self.fat_clusters):
            self.write_cluster(c, struct.pack("<%dI" % per, *self.fat[i * per:(i + 1) * per]))

    # ---- FAT chains ----------------------------------------------------------
    def chain(self, first: int):
        c, steps = first, 0
        while c != END:
            if c >= self.n_alloc:
                raise CardError("Corrupt FAT chain (cluster out of range)")
            yield c
            e = self.fat[c]
            if not e & ALLOCATED:
                raise CardError("Corrupt FAT chain (free cluster inside a chain)")
            c = END if e == END else e & 0x7FFFFFFF
            steps += 1
            if steps > self.n_alloc:
                raise CardError("Corrupt FAT chain (loop)")

    def free_clusters(self) -> int:
        return sum(1 for e in self.fat[:self.n_alloc] if not e & ALLOCATED)

    def _alloc_chain(self, n: int) -> List[int]:
        free = [i for i in range(self.n_alloc) if not self.fat[i] & ALLOCATED][:n]
        if len(free) < n:
            raise CardError("Not enough free space on the card")
        for a, b in zip(free, free[1:]):
            self.fat[a] = ALLOCATED | b
        self.fat[free[-1]] = END
        return free

    def _free_chain(self, first: int):
        for c in list(self.chain(first)):
            self.fat[c] = FREE

    def _read_chain_data(self, chain) -> bytes:
        return b"".join(self._read_rel(c) for c in chain)

    def _write_chain(self, chain, data: bytes):
        for i, c in enumerate(chain):
            self._write_rel(c, data[i * 1024:(i + 1) * 1024])

    def read_file(self, first: int, length: int) -> bytes:
        if length == 0 or first == END:
            return b""
        data = self._read_chain_data(self.chain(first))
        if len(data) < length:
            raise CardError("File is shorter than its recorded length")
        return data[:length]

    # ---- directories -----------------------------------------------------------
    def _read_dir(self, first: int, count: int) -> List[DirEntry]:
        data = self._read_chain_data(self.chain(first))
        if count * ENTRY_SIZE > len(data):
            raise CardError("Directory is larger than its cluster chain")
        return [parse_entry(data[i * ENTRY_SIZE:(i + 1) * ENTRY_SIZE]) for i in range(count)]

    def _root_len(self) -> int:
        return parse_entry(self._read_rel(0)[:ENTRY_SIZE]).length

    def _root(self):
        chain = list(self.chain(0))
        n = self._root_len()
        return chain, n, self._read_dir(0, n)

    def _write_root_entry(self, chain, slot: int, entry: DirEntry):
        cl = chain[slot // 2]
        data = bytearray(self._read_rel(cl))
        off = (slot % 2) * ENTRY_SIZE
        data[off:off + ENTRY_SIZE] = pack_entry(entry)
        self._write_rel(cl, bytes(data))

    def _find_save(self, dirname: str):
        _chain, n, entries = self._root()
        for i in range(2, n):
            e = entries[i]
            if e.mode & DF_EXISTS and e.mode & DF_DIR and e.name == dirname:
                return i, e
        return None

    # ---- listing -----------------------------------------------------------------
    def list_saves(self) -> List[SaveInfo]:
        _chain, n, entries = self._root()
        out = []
        for e in entries[2:n]:
            if not (e.mode & DF_EXISTS and e.mode & DF_DIR):
                continue
            title, files, clusters = "", 0, -(-e.length // 2)
            try:
                for s in self._read_dir(e.cluster, e.length)[2:]:
                    if s.mode & DF_EXISTS and s.mode & DF_FILE:
                        files += 1
                        clusters += -(-s.length // 1024)
                        if s.name.lower() == "icon.sys" and not title:
                            title = title_from_icon_sys(self.read_file(s.cluster, s.length))
            except CardError:
                title = "(damaged save)"
            out.append(SaveInfo(e.name, title, clusters, files, stamp_to_dt(e.modified),
                                bool(e.mode & DF_PROTECTED), bool(e.mode & DF_PSX),
                                bool(e.mode & DF_HIDDEN)))
        return out

    def get_save(self, dirname: str) -> PSUSave:
        found = self._find_save(dirname)
        if not found:
            raise CardError(f"Save '{dirname}' not found")
        _slot, e = found
        sub = self._read_dir(e.cluster, e.length)
        files = [PSUFile(s.name, s.mode, s.created, s.modified, s.attr,
                         self.read_file(s.cluster, s.length))
                 for s in sub[2:] if s.mode & DF_EXISTS and s.mode & DF_FILE]
        return PSUSave(e.name, e.mode, e.created, e.modified, e.attr, files,
                       sub[0] if sub else None, sub[1] if len(sub) > 1 else None)

    # ---- modification --------------------------------------------------------------
    def delete_save(self, dirname: str):
        found = self._find_save(dirname)
        if not found:
            raise CardError(f"Save '{dirname}' not found")
        slot, e = found
        for s in self._read_dir(e.cluster, e.length)[2:]:
            if s.mode & DF_EXISTS and s.mode & DF_FILE and s.cluster != END:
                self._free_chain(s.cluster)
        self._free_chain(e.cluster)
        e.mode &= ~DF_EXISTS
        chain = list(self.chain(0))
        self._write_root_entry(chain, slot, e)

    def import_save(self, save: PSUSave, overwrite: bool = False):
        check_name(save.dirname)
        names = [f.name for f in save.files]
        for n in names:
            check_name(n)
        if len(set(names)) != len(names):
            raise CardError("The save contains duplicate file names")
        if self._find_save(save.dirname):
            if not overwrite:
                raise CardError(f"'{save.dirname}' already exists on this card")
            self.delete_save(save.dirname)

        cs = self.cluster_size
        n_entries = len(save.files) + 2
        dir_clusters = -(-n_entries // 2)
        file_clusters = [-(-len(f.data) // cs) for f in save.files]
        root_chain, root_len, entries = self._root()
        slot = next((i for i in range(2, root_len) if not entries[i].mode & DF_EXISTS), None)
        extra = 0
        if slot is None:
            slot = root_len
            if slot >= len(root_chain) * 2:
                extra = 1
        need = dir_clusters + sum(file_clusters) + extra
        free = self.free_clusters()
        if need > free:
            raise CardError(f"Not enough space: '{save.dirname}' needs {need} KB, "
                            f"only {free} KB free")

        if extra:
            new = self._alloc_chain(1)[0]
            self.fat[root_chain[-1]] = ALLOCATED | new
            self._write_rel(new, bytes(cs))
            root_chain.append(new)
        dir_chain = self._alloc_chain(dir_clusters)
        file_chains = [self._alloc_chain(n) if n else [] for n in file_clusters]

        dot, dd = save.dot, save.dotdot
        ents = [
            DirEntry((dot.mode if dot else MODE_DIR) | DF_EXISTS | DF_DIR, 0,
                     dot.created if dot else save.created, 0, slot,
                     dot.modified if dot else save.modified, 0, "."),
            DirEntry((dd.mode if dd else MODE_DOTDOT) | DF_EXISTS | DF_DIR, 0,
                     dd.created if dd else save.created, 0, 0,
                     dd.modified if dd else save.modified, 0, ".."),
        ]
        for f, ch in zip(save.files, file_chains):
            ents.append(DirEntry(f.mode | DF_EXISTS | DF_FILE, len(f.data), f.created,
                                 ch[0] if ch else END, 0, f.modified, f.attr, f.name))
        blob = b"".join(pack_entry(e) for e in ents)
        self._write_chain(dir_chain, blob.ljust(dir_clusters * cs, b"\0"))
        for f, ch in zip(save.files, file_chains):
            if ch:
                self._write_chain(ch, f.data.ljust(len(ch) * cs, b"\0"))

        self._write_root_entry(root_chain, slot, DirEntry(
            save.mode | DF_EXISTS | DF_DIR, n_entries, save.created, dir_chain[0], 0,
            save.modified, save.attr, save.dirname))
        if slot == root_len:
            first = bytearray(self._read_rel(0))
            struct.pack_into("<I", first, 4, root_len + 1)
            self._write_rel(0, bytes(first))

    # ---- saving ----------------------------------------------------------------------
    def to_bytes(self) -> bytes:
        self._flush_fat()
        return bytes(self.raw)

    def save(self, path):
        data = self.to_bytes()
        path = os.fspath(path)
        folder = os.path.dirname(os.path.abspath(path))
        fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            if os.path.exists(path):
                shutil.copymode(path, tmp)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
