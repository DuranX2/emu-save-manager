"""
biosdb.py - identify emulator BIOS files by their contents (never by their name).

  * PS2      : the BIOS's own ROMDIR is parsed and its ROMVER entry read - version,
               region, build date - exactly as PCSX2 does (pcsx2/ps2/BiosTools.cpp).
               A ROMDIR that runs past the end of the file means a truncated dump.
  * Xbox     : MCPX boot ROM by MD5, from xemu's documentation (good and known-bad dump).
  * everything RetroArch knows (PS1, Saturn, Dreamcast, GBA, DS, ...): MD5 against
               libretro's System.dat (biosdb_data.py), which also gives the file names
               the cores expect.
No GUI, no network.  Nothing here downloads or contains BIOS files.
"""

import hashlib
import os
import re
import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from biosdb_data import DB, SOURCE_VERSION  # noqa: F401  (SOURCE_VERSION shown in the UI)

# Bump when identification rules change, so cached results are recomputed.
RULES_VERSION = 2

PS1 = "Sony - PlayStation"
PS2 = "Sony - PlayStation 2"
XBOX = "Microsoft - Xbox"
GC = "Nintendo - GameCube"
UNKNOWN = "Unidentified"

# xemu documentation: https://xemu.app/docs/required-files
XBOX_MCPX = {
    "d49c52a4102f6df7bcf8d0617ac475ed": ("MCPX 1.0 boot ROM", "verified"),
    "196a5f59a13382c185636e691d6c323d": ("MCPX boot ROM, bad dump (a few bytes off)", "damaged"),
}
PS2_ZONES = {"J": "Japan", "A": "USA", "E": "Europe", "H": "Asia", "C": "China",
             "T": "Test (T10K)", "X": "Test", "P": "Free"}
# Sony's model numbers end in the region: SCPH-xxx0 Japan, xxx1 USA/Canada, xxx2 Europe, xxx3 Asia
PS1_REGION_BY_DIGIT = {"0": "Japan", "1": "USA", "2": "Europe", "3": "Asia"}
# GameCube IPL dumps as libretro names them -> Dolphin's region folder
GC_IPL_REGION = {"gc-pal-10.bin": "EUR", "gc-pal-11.bin": "EUR", "gc-pal-12.bin": "EUR",
                 "gc-ntsc-10.bin": "JAP", "gc-ntsc-11.bin": "USA", "gc-ntsc-12.bin": "USA"}
# Files PCSX2 keeps beside a BIOS with the same base name (settings, DVD player ROMs)
PS2_SIDE_EXTS = (".nvm", ".mec", ".erom", ".rom1", ".rom2")
PS2_DVD_ROMS = ("rom1.bin", "rom2.bin", "erom.bin")
NDS = "Nintendo - Nintendo DS"


@dataclass
class Identity:
    system: str                     # libretro-style system name, or UNKNOWN
    label: str                      # plain-language description
    status: str                     # verified | identified | unknown | damaged | side-file
    md5: str
    size: int
    names: Tuple[str, ...] = ()     # file names libretro cores expect (may include sub-folders)
    region: str = ""                # Japan / USA / Europe ... where known
    extra: dict = field(default_factory=dict)

    @property
    def installable(self) -> bool:
        return self.status in ("verified", "identified", "side-file")


def md5_of(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


# ---------------------------------------------------------------------------
# PS2
# ---------------------------------------------------------------------------
def ps2_romver(data: bytes) -> Optional[dict]:
    """Parse the PS2 BIOS ROMDIR.  Returns {version, region, date, kind, serial, truncated}
    or None if this is not a PS2 BIOS."""
    if len(data) < 0x3000:
        return None
    start = 0
    while True:
        i = data.find(b"RESET\x00\x00\x00\x00\x00", start)
        if i < 0:
            return None
        if i % 16 == 0:
            break
        start = i + 1
    off, romver, serial, p = 0, None, "", i
    while p + 16 <= len(data):
        name = data[p:p + 10].split(b"\0")[0]
        if not name:
            break
        size = struct.unpack_from("<I", data, p + 12)[0]
        if name == b"ROMVER" and off + 14 <= len(data):
            romver = data[off:off + 14].decode("ascii", "replace")
        elif name == b"EXTINFO" and off + 0x1F <= len(data):
            serial = data[off + 0x10:off + 0x1F].split(b"\0")[0].decode("ascii", "replace")
        off += size if size % 16 == 0 else (size + 0x10) & ~0xF
        p += 16
        if p > i + 16 * 512:            # a sane ROMDIR has far fewer entries
            break
    if not romver or not romver[:4].isdigit():
        return None
    return {
        "version": f"{int(romver[0:2])}.{romver[2:4]}",
        "region": PS2_ZONES.get(romver[4], romver[4]),
        "kind": {"C": "Console", "D": "Devkit"}.get(romver[5], ""),
        "date": f"{romver[6:10]}-{romver[10:12]}-{romver[12:14]}",
        "serial": serial,
        "truncated": len(data) < off,
        "percent": min(100, len(data) * 100 // max(off, 1)),
    }


def _ps1_region(names) -> str:
    for n in names:
        m = re.search(r"scph-?(\d{3,4})", n.lower())
        if m:
            return PS1_REGION_BY_DIGIT.get(m.group(1)[-1], "")
    return ""


# ---------------------------------------------------------------------------
# The one entry point
# ---------------------------------------------------------------------------
def identify(data: bytes, filename: str = "", hint_system: str = "") -> Identity:
    """Identify a file.  hint_system = the vault folder it sits in (used only for files
    no fingerprint can place, such as an Xbox flash BIOS or a folder-set member)."""
    h = md5_of(data)
    size = len(data)
    low = os.path.basename(filename).lower()

    if low.endswith(PS2_SIDE_EXTS) and size <= 0x1000000:
        return Identity(PS2, f"PCSX2 file kept beside a PS2 BIOS ({low.rsplit('.', 1)[-1].upper()})",
                        "side-file", h, size)
    if low in PS2_DVD_ROMS and hint_system == PS2 and size <= 0x1000000:
        return Identity(PS2, "PS2 DVD player ROM", "side-file", h, size)

    if size in (0x200000, 0x400000, 0x800000) or low.endswith((".bin", ".rom", ".rom0")):
        info = ps2_romver(data)
        if info:
            kind = f" {info['kind']}" if info["kind"] else ""
            label = f"PS2 {info['region']} v{info['version']} ({info['date']}){kind}"
            if info["truncated"]:
                return Identity(PS2, label + f" - truncated, {info['percent']}% of the file",
                                "damaged", h, size, region=info["region"], extra=info)
            known = DB.get(h)
            return Identity(PS2, label, "verified" if known else "identified", h, size,
                            known[1] if known else (), info["region"], info)

    if h in XBOX_MCPX:
        label, status = XBOX_MCPX[h]
        return Identity(XBOX, label, status, h, size, ("mcpx_1.0.bin",) if status == "verified" else ())

    known = DB.get(h)
    if known:
        system, names, _sz = known
        base = os.path.basename(names[0])
        label = base
        region = ""
        if system == PS1:
            region = _ps1_region(names)
            models = ", ".join(sorted({re.sub(r"\.bin$", "", os.path.basename(n), flags=re.I).upper()
                                       for n in names}))
            label = f"PS1 BIOS {models}" + (f" ({region})" if region else "")
        elif system == GC:
            region = next((GC_IPL_REGION[os.path.basename(n)] for n in names
                           if os.path.basename(n) in GC_IPL_REGION), "")
            label = f"GameCube IPL {base}" + (f" ({region})" if region else "")
        return Identity(system, label, "verified", h, size, tuple(names), region)

    # No fingerprint: a few safe structural guesses, otherwise the vault folder decides
    if size == 524288 and b"Sony Computer Entertainment" in data[:0x10000]:
        m = re.search(r"scph-?(\d{3,4})", low)
        region = PS1_REGION_BY_DIGIT.get(m.group(1)[-1], "") if m else ""
        return Identity(PS1, "PS1 BIOS (a version libretro does not list)" + (f" ({region})" if region else ""),
                        "identified", h, size, (), region)
    if hint_system == NDS and size in (131072, 262144, 524288) and "firmware" in low:
        return Identity(NDS, "DS firmware (holds the console's own settings, so it never matches a list)",
                        "identified", h, size, ("firmware.bin",))
    if size == 512 and low.startswith("mcpx"):
        return Identity(XBOX, "MCPX boot ROM, not a known good dump", "damaged", h, size)
    if hint_system == XBOX or re.search(r"4627|complex|cromwell|evox|xbox", low):
        if size in (262144, 1048576):
            return Identity(XBOX, f"Xbox flash BIOS ({size // 1024} KB)", "identified", h, size)
        if size == 256 and "eeprom" in low:
            return Identity(XBOX, "Xbox EEPROM (console settings)", "identified", h, size)
    if hint_system == GC and low == "ipl.bin" and size == 2097152:
        return Identity(GC, "GameCube IPL (not on libretro's list)", "identified", h, size)
    if re.search(r"\[b\d*\]", low):
        return Identity(hint_system or UNKNOWN, "named as a bad dump [b]", "damaged", h, size)
    return Identity(hint_system or UNKNOWN, "not on any fingerprint list", "unknown", h, size)
