"""
raconvert.py - moving in-game saves between RetroArch cores and standalone emulators.

  * PlayStation: Beetle PSX / PCSX ReARMed / SwanStation keep memory card 1 as "<game>.srm", a plain
    128 KB card image, the same bytes as a DuckStation ".mcd". DuckStation's default is one card per
    game named "<game title>_1.mcd" in its memcards folder.
  * Game Boy, Game Boy Color, Game Boy Advance, Nintendo DS: the ".srm" (or melonDS's ".sav") is the
    cartridge's save memory as is, which standalone mGBA, SameBoy, melonDS and others read as
    "<game>.sav". DeSmuME's ".dsv" is the same plus a footer, which is left off when exporting.
  * Nintendo 64: Mupen64Plus-Next and ParaLLEl N64 keep everything in one ".srm": EEPROM (2 KB),
    four Controller Paks (32 KB each), SRAM (32 KB) and FlashRAM (128 KB), in that order. Standalone
    Mupen64Plus (and RMG, which uses it) keeps them as separate .eep/.mpk/.sra/.fla files in the same
    byte order; Project64 stores SRAM and FlashRAM with every 4 bytes reversed.

Every write is verified and anything replaced goes to the RetroArch safety folder.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import ps1card
import rasaves
import safecopy

PS1_SYSTEMS = {"psx"}
RAW_SYSTEMS = {"gb", "gbc", "gba", "nds"}
N64_SYSTEMS = {"n64"}
DSV_MARK = b"|<--Snip above here to create a raw sav by excluding this DeSmuME savedata footer:"

EEP, MPK, PAK, SRA, FLA = 0x800, 0x8000 * 4, 0x8000, 0x8000, 0x20000
N64_SIZE = EEP + MPK + SRA + FLA
N64_PARTS = (("eep", 0, EEP), ("mpk", EEP, MPK), ("sra", EEP + MPK, SRA), ("fla", EEP + MPK + SRA, FLA))


class ConvertError(Exception):
    pass


def kind_for(g: rasaves.RAGame) -> str:
    """'ps1' | 'raw' | 'n64' | '' for what can be converted for this game."""
    s = g.system or ""
    hints = g.hint_systems or set()
    if s in PS1_SYSTEMS or (not s and hints & PS1_SYSTEMS):
        return "ps1"
    if s in N64_SYSTEMS or (not s and hints & N64_SYSTEMS):
        return "n64"
    if s in RAW_SYSTEMS or (not s and hints and hints <= RAW_SYSTEMS):
        return "raw"
    return ""


def main_save(g: rasaves.RAGame):
    """The game's in-game save file (newest if there are copies in several places)."""
    prefer = {".srm": 0, ".sav": 1, ".dsv": 2}
    files = [f for f in g.srams if f.suffix.lower() in prefer]
    files.sort(key=lambda f: (prefer[f.suffix.lower()], -f.mtime))
    return files[0] if files else None


def _sanitize(name: str) -> str:
    """Like DuckStation's Path::SanitizeFileName."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip()


# ---------------------------------------------------------------------------
# PlayStation
# ---------------------------------------------------------------------------
def ps1_card(g: rasaves.RAGame):
    f = main_save(g)
    if f is None or f.suffix.lower() != ".srm":
        return None, None
    data = f.path.read_bytes()
    if len(data) != ps1card.CARD_SIZE:
        raise ConvertError(f"{f.name} isn't a 128 KB memory card ({len(data)} bytes)")
    try:
        card = ps1card.Card.from_bytes(data, ".srm")
    except ps1card.PS1Error as ex:
        raise ConvertError(f"{f.name} isn't a memory card: {ex}") from None
    return f, card


def _words(s: str):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", s)
    return set(re.findall(r"[a-z0-9]+", s)) - {"the", "a", "of", "and", "disc", "1"}


def duckstation_names(g: rasaves.RAGame, card, ds_dir: Path | None, titles=None) -> list:
    """Card names to offer, best first: an existing DuckStation card for this game, then the title
    from DuckStation's game list (via a save's product code), then the game's own name."""
    out = []
    want = _words(g.title) | _words(g.stem)
    if ds_dir and Path(ds_dir).is_dir() and want:
        best = []
        for p in Path(ds_dir).glob("*.mcd"):
            name = re.sub(r"_\d+$", "", p.stem)
            w = _words(name)
            if w and len(w & want) / max(len(w), 1) >= 0.75:
                best.append((len(w & want), p.name))
        out += [n for _s, n in sorted(best, reverse=True)]
    if titles is not None and card is not None:
        for s in card.saves():
            t = titles.lookup("ps1", s.product) if s.product else ""
            if t:
                out.append(_sanitize(t) + "_1.mcd")
                break
    plain = re.sub(r"\s*\((Disc|Disk|CD)\s*\d+[^)]*\)", "", g.title, flags=re.I).strip()
    out.append(_sanitize(plain) + "_1.mcd")
    return list(dict.fromkeys(out))


def export_ps1(g: rasaves.RAGame, ds_dir: Path, name: str, backup_dir) -> Path:
    f, card = ps1_card(g)
    if f is None:
        raise ConvertError("This game has no memory card (.srm) yet")
    dest = Path(ds_dir) / name
    safecopy.write_file_verified(f.path.read_bytes(), dest, safecopy.Safety(backup_dir, rasaves.SAFETY_NAME),
                                 "replaced in DuckStation")
    return dest


def import_ps1(src: Path, g: rasaves.RAGame, dest: Path, backup_dir) -> Path:
    """Put a DuckStation (or any) PS1 card into the game's .srm at dest."""
    try:
        card = ps1card.Card.load(src)
    except (OSError, ps1card.PS1Error) as ex:
        raise ConvertError(f"{Path(src).name} isn't a memory card RetroArch can use: {ex}") from None
    safecopy.write_file_verified(bytes(card.img), dest,
                                 safecopy.Safety(backup_dir, rasaves.SAFETY_NAME), "replaced by an import")
    return dest


# ---------------------------------------------------------------------------
# GB / GBC / GBA / DS
# ---------------------------------------------------------------------------
def raw_bytes(f: rasaves.RAFile) -> bytes:
    data = f.path.read_bytes()
    if f.suffix.lower() == ".dsv":
        i = data.find(DSV_MARK)
        if i >= 0:
            data = data[:i]
    return data


def export_raw(g: rasaves.RAGame, dest_dir: Path, backup_dir) -> Path:
    f = main_save(g)
    if f is None:
        raise ConvertError("This game has no in-game save yet")
    dest = Path(dest_dir) / (g.stem + ".sav")
    safecopy.write_file_verified(raw_bytes(f), dest, safecopy.Safety(backup_dir, rasaves.SAFETY_NAME),
                                 "replaced by an export")
    return dest


def import_raw(src: Path, g: rasaves.RAGame, backup_dir) -> Path:
    """Replace the game's existing in-game save with a standalone emulator's .sav (same name kept)."""
    f = main_save(g)
    if f is None:
        raise ConvertError("Start this game once in RetroArch and save in-game first, so it's clear "
                           "which file the core uses; then import again")
    data = Path(src).read_bytes()
    if not data:
        raise ConvertError(f"{Path(src).name} is empty")
    if f.suffix.lower() == ".dsv":
        raise ConvertError("This game uses DeSmuME's .dsv format; import into a melonDS save instead")
    safecopy.write_file_verified(data, f.path, safecopy.Safety(backup_dir, rasaves.SAFETY_NAME),
                                 "replaced by an import")
    return f.path


# ---------------------------------------------------------------------------
# Nintendo 64
# ---------------------------------------------------------------------------
def swap32(data: bytes) -> bytes:
    b = bytearray(data)
    for i in range(0, len(b) - 3, 4):
        b[i:i + 4] = b[i:i + 4][::-1]
    return bytes(b)


def _uniform(data: bytes) -> bool:
    return not data or data.count(data[0]) == len(data)


def pak_used(pak: bytes) -> bool:
    """A Controller Pak with any notes in its note table (pages 3-4)."""
    return any(pak[0x300:0x500])


def n64_split(data: bytes, flavor="mupen") -> dict:
    """{'eep': bytes, …} for the parts of a combined .srm that hold anything."""
    if len(data) != N64_SIZE:
        raise ConvertError(f"This isn't a Mupen64Plus-Next save ({len(data)} bytes, expected {N64_SIZE})")
    out = {}
    for ext, off, size in N64_PARTS:
        part = data[off:off + size]
        if ext == "mpk":
            if any(pak_used(part[i:i + PAK]) for i in range(0, MPK, PAK)):
                out[ext] = part
        elif not _uniform(part):
            out[ext] = swap32(part) if (flavor == "pj64" and ext in ("sra", "fla")) else part
    return out


def n64_join(base: bytes | None, parts: dict, flavor="mupen") -> bytes:
    """A combined .srm from separate parts laid over base (or a blank save)."""
    out = bytearray(base if base and len(base) == N64_SIZE else
                    b"\xff" * EEP + b"\x00" * MPK + b"\x00" * SRA + b"\xff" * FLA)
    for ext, off, size in N64_PARTS:
        if ext not in parts:
            continue
        d = parts[ext]
        if flavor == "pj64" and ext in ("sra", "fla"):
            d = swap32(d)
        if ext == "mpk" and len(d) <= MPK and len(d) % PAK == 0:
            pass
        elif len(d) > size:
            raise ConvertError(f"The .{ext} file is larger than a {ext.upper()} save ({len(d)} bytes)")
        out[off:off + len(d)] = d
    return bytes(out)


def export_n64(g: rasaves.RAGame, dest_dir: Path, flavor, backup_dir) -> list:
    f = main_save(g)
    if f is None:
        raise ConvertError("This game has no in-game save yet")
    parts = n64_split(f.path.read_bytes(), flavor)
    if not parts:
        raise ConvertError("The save is empty: there's nothing in it to export yet")
    safety = safecopy.Safety(backup_dir, rasaves.SAFETY_NAME)
    out = []
    for ext, data in parts.items():
        dest = Path(dest_dir) / f"{g.stem}.{ext}"
        safecopy.write_file_verified(data, dest, safety, "replaced by an export")
        out.append(dest)
    return out


def import_n64(files, g: rasaves.RAGame, dest: Path, flavor, backup_dir) -> Path:
    parts = {}
    for p in files:
        ext = Path(p).suffix.lower().lstrip(".")
        if ext not in ("eep", "mpk", "sra", "fla"):
            raise ConvertError(f"{Path(p).name}: expected .eep, .mpk, .sra or .fla files")
        parts[ext] = Path(p).read_bytes()
    if not parts:
        raise ConvertError("No save files chosen")
    base = dest.read_bytes() if Path(dest).is_file() else None
    data = n64_join(base, parts, flavor)
    safecopy.write_file_verified(data, dest, safecopy.Safety(backup_dir, rasaves.SAFETY_NAME),
                                 "replaced by an import")
    return Path(dest)
