"""libcheck.py - the ROM Library check-up: a report of what in the library isn't quite right.

It only reads the library's index (romlibrary.Item objects) and never changes, moves or hides
anything. It finds:
  * entries that aren't games (pictures, text, saves, plugin source, emulator programs);
  * duplicates (the same game more than once in one system);
  * games the emulator can't load as they are (archives in disc systems, extracted Xbox games);
  * games that could take less space (PS2/PSP/PS1 discs as CHD, GameCube/Wii as RVZ);
  * Wii U games that aren't a single .wua yet (the library's chosen Wii U format).
"""

import os
import re
from collections import defaultdict
from dataclasses import dataclass, field

import romlibrary as rl

# ---------------------------------------------------------------------------
# Report entries
# ---------------------------------------------------------------------------
CATEGORIES = [
    ("notgame", "Not games",
     "These are listed in the library as games but don't look like games. They can be moved out of "
     "the system folder (or into a 'media' folder, which the library skips)."),
    ("duplicate", "Duplicates",
     "The same game is in the library more than once. Keeping one copy frees the space of the others."),
    ("convert", "Needs converting",
     "The emulator can't load these as they are."),
    ("wiiu", "Wii U: not a .wua yet",
     "Wii U games are best kept as one .wua file each, with the game's update and DLC inside it. "
     "Cemu reads .wua directly and they're much smaller than unpacked folders."),
    ("space", "Could take less space",
     "These work as they are, but a compressed format the emulator also reads would use less space."),
]
CATEGORY_TITLE = {c: t for c, t, _ in CATEGORIES}
CATEGORY_HELP = {c: h for c, _, h in CATEGORIES}


@dataclass
class Finding:
    category: str
    system: str                     # canonical system folder name ('ps2')
    title: str                      # what the person sees in the list
    detail: str                     # why it's listed and what to do
    keys: list = field(default_factory=list)   # library item keys involved
    size: int = 0                   # bytes involved (for duplicates: what removing the extras frees)

    def system_name(self):
        return rl.SYSTEMS.get(self.system, self.system)


@dataclass
class Report:
    findings: list = field(default_factory=list)
    checked: int = 0                # items looked at
    systems: int = 0

    def by_category(self):
        out = defaultdict(list)
        for f in self.findings:
            out[f.category].append(f)
        return out

    def count(self, category=None):
        return sum(1 for f in self.findings if category in (None, f.category))

    def text(self) -> str:
        """The whole report as plain text (for 'Save report…')."""
        lines = [f"ROM Library check-up: {self.checked} entries in {self.systems} systems, "
                 f"{len(self.findings)} thing(s) to look at.", ""]
        cats = self.by_category()
        for cat, title, help_ in CATEGORIES:
            items = cats.get(cat) or []
            if not items:
                continue
            lines += [f"== {title} ({len(items)}) ==", help_, ""]
            for f in sorted(items, key=lambda f: (f.system_name().lower(), f.title.lower())):
                size = f"  [{rl.human(f.size)}]" if f.size else ""
                lines.append(f"- {f.system_name()}: {f.title}{size}")
                lines.append(f"    {f.detail}")
                for k in f.keys:
                    lines.append(f"    {k}")
            lines.append("")
        if not self.findings:
            lines.append("Nothing to report: everything looks like a game in a format its emulator reads.")
        return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# What files are
# ---------------------------------------------------------------------------
PICTURE = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".ico", ".svg"}
TEXT = {".txt", ".nfo", ".md", ".pdf", ".html", ".htm", ".rtf", ".doc", ".docx", ".diz", ".url",
        ".lnk", ".log", ".csv", ".xml", ".json", ".yml", ".yaml", ".ini", ".cfg", ".sfv", ".md5",
        ".sha1", ".torrent", ".lily", ".dat"}
SAVE = {".srm", ".sav", ".state", ".mcr", ".mcd", ".mcs", ".gme", ".vmp", ".ps2", ".gci", ".raw",
        ".st0", ".st1", ".st2", ".st3", ".oops", ".dsv", ".eep", ".fla", ".sra", ".mpk", ".rtc"}
SOURCE = {".c", ".h", ".cpp", ".hpp", ".cc", ".s", ".asm", ".py", ".sh", ".bat", ".mk", ".o",
          ".a", ".ld", ".patch", ".diff", ".js", ".css"}
PROGRAM = {".exe", ".dll", ".so", ".dylib", ".msi", ".appimage", ".pdb"}
ARCHIVE = {".zip", ".7z", ".rar"}
SOURCE_NAMES = {"makefile", "readme", "license", "licence", "changelog", "copying", "authors"}
WIIU_NUS = {".app", ".h3", ".tmd", ".tik", ".cert"}
# Systems where a game is a disc image (archives aren't loaded by their usual emulators)
DISC_SYSTEMS = {"psx", "ps2", "ps3", "psp", "gc", "wii", "wiiu", "xbox", "xbox360", "dreamcast",
                "saturn", "segacd", "megacd", "n3ds", "switch", "pcfx", "neogeocd", "3do",
                "atarijaguarcd"}
# Systems whose games are pictures (PICO-8 carts are .p8.png) or are mostly loose files
PICTURE_GAMES = {"pico8"}
LOOSE_SYSTEMS = {"dos", "scummvm", "amiga", "c64", "msx", "ps4", "psvita"}
EMULATOR_NAMES = {"cemu", "dolphin", "pcsx2", "pcsx2-qt", "duckstation", "duckstation-qt", "ppsspp",
                  "ppssppwindows64", "rpcs3", "xemu", "xenia", "xenia_canary", "retroarch", "yuzu",
                  "ryujinx", "citra", "azahar", "melonds", "primehack", "vita3k", "flycast", "redream",
                  "mame", "mednafen", "shadps4"}

TITLE_ID = re.compile(r"\b(0005000[0ceECE][0-9a-fA-F]{8})\b")
TAGS = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")


def _ext(name: str) -> str:
    return os.path.splitext(name)[1].lower()


def _leaf(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def file_kind(name: str, system: str = "") -> str:
    """'picture', 'text', 'save', 'source', 'program', 'archive', 'nus' or 'game' for one file name."""
    leaf = _leaf(name).lower()
    ext = _ext(leaf)
    if system in PICTURE_GAMES and leaf.endswith(".p8.png"):
        return "game"
    if ext in PICTURE:
        return "picture"
    stem = os.path.splitext(leaf)[0]
    if ext in SOURCE or leaf == "makefile":
        return "source"
    if leaf in SOURCE_NAMES or (stem in SOURCE_NAMES and ext in (".txt", ".md")):
        return "text"
    if ext in TEXT:
        return "text"
    if ext in PROGRAM:
        return "program"
    if ext in SAVE:
        return "save"
    if ext in ARCHIVE:
        return "archive"
    if ext in WIIU_NUS and system == "wiiu":
        return "nus"
    return "game"


def _kinds(item, system):
    counts = defaultdict(int)
    for path in item.files:
        counts[file_kind(path, system)] += 1
    return counts


def _norm_title(title: str) -> str:
    """For finding duplicates: case, punctuation and '(Disc 1)'-style differences don't count,
    but region and version tags do (they tell copies apart)."""
    t = title.lower().replace("&", "and")
    t = re.sub(r"[\s_\-:,.'!]+", " ", t)
    return t.strip()


def _base_title(title: str) -> str:
    """Wii U: the game's name without its tags ('(UPDATE DATA)', '[0005000010101C00]', '(USA)')."""
    t = TAGS.sub("", title)
    t = re.sub(r"\b(update data|update|dlc|unpacked|v\d+)\b", "", t, flags=re.I)
    return _norm_title(t)


def _wiiu_ids(item):
    ids = set(TITLE_ID.findall(item.path))
    for path in list(item.files)[:200]:
        ids.update(TITLE_ID.findall(path))
    return {i.upper() for i in ids}


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------
def _single(item):
    """The one file of an entry ('ps2/Game/Game.iso' as a folder holding one game), or ''."""
    if not item.is_dir:
        return item.path
    return next(iter(item.files)) if len(item.files) == 1 else ""


def _source_folders(items, system):
    """Folders (inside a system folder) that are mostly program source: {folder path: share}."""
    per = defaultdict(lambda: [0, 0])
    for it in items:
        parent = it.path.rsplit("/", 1)[0] if "/" in it.path else ""
        if not parent:
            continue
        for p in it.files:
            per[parent][1] += 1
            per[parent][0] += file_kind(p, system) == "source"
    return {d for d, (src, n) in per.items() if n >= 3 and src * 2 >= n}


def title_of(item) -> str:
    """The entry's name, or for a folder holding one file, that file's name (the folder may just be
    called 'USA' or 'Spare')."""
    one = _single(item)
    if item.is_dir and one:
        return rl.clean_title(_leaf(one))
    return item.title


def _not_game(item, system):
    """A reason this entry isn't a game, or ''."""
    leaf = _leaf(item.path)
    low = leaf.lower()
    kinds = _kinds(item, system)
    total = sum(kinds.values())
    if not total:
        return ""
    names = {os.path.splitext(_leaf(p))[0].lower() for p in item.files if file_kind(p, system) == "program"}
    if kinds.get("program") and (names & EMULATOR_NAMES or low in EMULATOR_NAMES
                                 or any(part.lower() in EMULATOR_NAMES or part.lower() == "emulators"
                                        for part in item.path.split("/"))):
        return "A copy of an emulator program, not a game."
    if kinds.get("source") and kinds.get("game", 0) <= kinds["source"]:
        return "Program source code (a plugin or tool), not a game."
    games = kinds.get("game", 0) + kinds.get("archive", 0) + kinds.get("nus", 0)
    if system in LOOSE_SYSTEMS or games:
        if games and kinds.get("game", 0) == total and item.size == 0:
            return "Empty: every file in it is 0 bytes."
        return ""
    if kinds.get("program") and system not in ("xbox", "xbox360", "ps3", "wiiu", "switch"):
        return "A program, not a game for this system."
    what = max(("picture", "text", "save", "program"), key=lambda k: kinds.get(k, 0))
    return {"picture": "Only pictures (covers or screenshots), no game files.",
            "text": "Only text files (notes, lists or databases), no game files.",
            "save": "Only save files, which belong with the emulator's saves, not with the games.",
            "program": "Only program files, no game files."}[what]


def _convert(item, system):
    """(category, reason) for a format problem, or None."""
    kinds = _kinds(item, system)
    exts = {_ext(p) for p in item.files}
    leaf_ext = _ext(_single(item))
    if system in DISC_SYSTEMS and kinds.get("archive") and not kinds.get("game"):
        which = "/".join(sorted(e for e in exts if e in ARCHIVE))
        return ("convert", f"A {which} archive: {rl.system_name(system)} emulators load disc images, not "
                           "archives. Extract it (the game inside is what goes in the library).")
    if system == "xbox" and item.is_dir and any(_leaf(p).lower() == "default.xbe" for p in item.files):
        return ("convert", "An extracted Xbox game folder. xemu only loads disc images: make an XISO "
                           "from the folder (extract-xiso -c) and keep the .iso instead.")
    if system == "xbox360" and item.is_dir and any(_leaf(p).lower() == "default.xex" for p in item.files):
        return None                                 # Xenia loads extracted games
    if system == "ps2" and leaf_ext == ".iso":
        return ("space", "A PS2 .iso. PCSX2 also reads .chd, which is usually much smaller "
                         "(chdman createdvd).")
    if system == "psp" and leaf_ext == ".iso":
        return ("space", "A PSP .iso. PPSSPP also reads .chd and .cso, which are smaller.")
    if system in ("gc", "wii") and leaf_ext in (".iso", ".gcm", ".wbfs"):
        return ("space", f"A {leaf_ext} image. Dolphin also reads .rvz, which is much smaller "
                         "(Dolphin: right-click the game > Convert File).")
    if system in ("psx", "dreamcast", "segacd", "megacd", "saturn", "pcengine", "neogeocd") \
            and leaf_ext in (".cue", ".gdi", ".ccd", ".toc"):
        return ("space", f"A {leaf_ext} disc with separate track files. A single .chd is smaller and "
                         "tidier (chdman createcd), and the emulators read it.")
    return None


def _wiiu(items):
    """Wii U findings: anything that isn't a .wua, grouped by game. Returns [Finding]."""
    out = []
    games = defaultdict(list)                       # base title or title-id low half -> [(item, kind)]
    for it in items:
        kinds = _kinds(it, "wiiu")
        low = it.path.lower()
        one = _ext(_single(it))
        if one == ".wua":
            kind = "wua"
        elif one in (".wux", ".wud"):
            kind = "disc"
        elif one == ".rpx":
            kind = "rpx"
        elif kinds.get("nus") and kinds["nus"] >= max(1, kinds.get("game", 0) // 2):
            kind = "nus"
        elif it.is_dir and any(seg in ("code", "content", "meta")
                               for f in it.files for seg in f[len(it.path) + 1:].lower().split("/")[:-1]):
            kind = "folder"
        else:
            continue
        ids = _wiiu_ids(it)
        part = "base"
        if any(i[7] in "eE" for i in ids) or "update" in low:
            part = "update"
        elif any(i[7] in "cC" for i in ids) or "(dlc" in low or " dlc" in low:
            part = "dlc"
        ident = sorted(i[8:] for i in ids)[0] if ids else _base_title(title_of(it))
        games[ident].append((it, kind, part))
    for ident, entries in games.items():
        kinds = {k for _it, k, _p in entries}
        parts = sorted({p for _it, _k, p in entries})
        if kinds == {"wua"}:
            continue                                # all good (two .wua copies: the duplicate check)
        name = next((title_of(it) for it, _k, p in entries if p == "base"), title_of(entries[0][0]))
        name = TAGS.sub("", name).strip() or name
        size = sum(it.size for it, _k, _p in entries)
        bits = []
        if "nus" in kinds:
            bits.append("encrypted installer files (.app/.tmd/.h3): Cemu can't run these as they are; "
                        "install them in Cemu (File > Install game title, update or DLC)")
        if "folder" in kinds:
            bits.append("unpacked folders (code/content/meta)")
        if "disc" in kinds:
            bits.append("a disc image (.wux/.wud)")
        if "rpx" in kinds:
            bits.append("a loose .rpx")
        if "wua" in kinds:
            bits.append("a .wua as well")
        extra = [p for p in parts if p != "base"]
        sep = f" Separate {' and '.join(extra)} {'entries' if len(extra) > 1 else 'entry'} could go " \
              f"inside the .wua." if extra else ""
        if "wua" in kinds:
            what = (f"Has {', '.join(bits)}. If the .wua already has this content, the other copies "
                    f"aren't needed.{sep}")
        else:
            what = (f"Kept as {', '.join(bits)}. Once it's in Cemu, its game list can make one .wua "
                    f"(right-click > Convert to compressed Wii U archive (.wua)) with the update and DLC "
                    f"included.{sep}")
        out.append(Finding("wiiu", "wiiu", name, what, [it.key for it, _k, _p in entries], size))
    return out


def _duplicates(items, system):
    out = []
    groups = defaultdict(list)
    for it in items:
        if it.size <= 0:
            continue
        groups[_norm_title(title_of(it))].append(it)
    for _t, same in groups.items():
        if len(same) < 2:
            continue
        if system == "wiiu" and any(_ext(_single(it)) != ".wua" for it in same):
            continue                                # the Wii U check covers mixed layouts
        same = sorted(same, key=lambda it: (it.size, it.key))
        keep = same[0]
        extra = sum(it.size for it in same[1:])
        fmts = sorted({(_ext(it.path) or "folder") for it in same})
        why = (f"{len(same)} copies ({', '.join(fmts)}). Keeping the smallest "
               f"({keep.path}) would free {rl.human(extra)}.")
        out.append(Finding("duplicate", system, TAGS.sub("", title_of(keep)).strip() or title_of(keep), why,
                           [it.key for it in same], extra))
    # the very same file in two category folders (e.g. 'USA/' and 'Favourites/')
    seen = defaultdict(list)
    for it in items:
        one = _single(it)
        if one and it.size > 0:
            seen[(_leaf(one).lower(), it.size)].append(it)
    named = {k for f in out for k in f.keys}
    for (_leafname, size), same in seen.items():
        if len(same) > 1 and not all(it.key in named for it in same):
            out.append(Finding("duplicate", system, title_of(same[0]),
                               f"The same file ({rl.human(size)}) is in {len(same)} folders.",
                               [it.key for it in same], size * (len(same) - 1)))
    return out


def check(items) -> Report:
    """items: romlibrary Items (e.g. Library.items.values()). Returns a Report."""
    rep = Report()
    by_sys = defaultdict(list)
    for it in items:
        by_sys[it.system].append(it)
    rep.systems = len(by_sys)
    for system, lst in sorted(by_sys.items()):
        rep.checked += len(lst)
        games = []
        source = _source_folders(lst, system)
        for it in lst:
            parent = it.path.rsplit("/", 1)[0] if "/" in it.path else ""
            why = _not_game(it, system) or ("Part of a folder of program source code (a plugin or tool), "
                                             f"'{parent}', not a game." if parent in source else "")
            if why:
                rep.findings.append(Finding("notgame", system, title_of(it), why, [it.key], it.size))
                continue
            games.append(it)
            if system == "wiiu":
                continue
            conv = _convert(it, system)
            if conv:
                cat, why = conv
                rep.findings.append(Finding(cat, system, title_of(it), why, [it.key], it.size))
        if system == "wiiu":
            rep.findings += _wiiu(games)
        rep.findings += _duplicates(games, system)
    return rep


SHORT = {"notgame": "not games", "duplicate": "duplicates", "convert": "to convert",
         "wiiu": "Wii U not yet .wua", "space": "could be smaller"}


def summary(rep: Report) -> str:
    cats = rep.by_category()
    parts = [f"{len(cats[c])} {SHORT[c]}" for c, _t, _h in CATEGORIES if cats.get(c)]
    return (f"Checked {rep.checked} entries in {rep.systems} systems: " + (", ".join(parts) if parts
            else "nothing to report") + ".")
