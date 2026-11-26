"""savenames.py - game names for save files, for screens that list files (Save History, Cloud Sync).

The readable save list (saveinfo.identify + titledb) already knows which game each file belongs to.
This module reuses that for one save folder and answers "what game is this file / item?".
Nothing here changes any file, and a parsing problem only means a file has no name.
"""

from pathlib import Path, PurePosixPath

import saveinfo


class Namer:
    """Names for the files of one save folder.

    emu / label: the emulator profile and location label ("PCSX2", "Memory Cards").
    folder: the folder on this device (may be None or missing: then names come from paths only).
    rels: posix paths relative to the folder. Files that no longer exist are fine.
    titles: a titledb.TitleDB (or anything with .lookup); None for names from the saves alone.
    """

    def __init__(self, emu: str, label: str, folder, rels, titles=None):
        self.by_rel = {}                    # rel -> "Game · detail"
        rels = sorted({r for r in rels if r})
        if not rels:
            return
        base = Path(folder) if folder else Path("/nonexistent-save-folder")
        try:
            groups, _left = saveinfo.identify(emu, [(label, base, rels)])
            saveinfo.apply_titles(groups, titles)
        except Exception as ex:  # noqa - names are a nicety; never break the screen over them
            print("Save names failed:", ex)
            return
        for g in groups:
            name = describe(g)
            if not name:
                continue
            for _lab, rel, _role in g.files:
                self.by_rel.setdefault(rel, name)

    def name(self, rel: str) -> str:
        """The game for a file, or for a folder item (e.g. a PCSX2 folder card): '' if unknown."""
        if not rel:
            return ""
        if rel in self.by_rel:
            return self.by_rel[rel]
        prefix = rel.rstrip("/") + "/"
        seen = []
        for r, n in sorted(self.by_rel.items()):
            if r.startswith(prefix) and n not in seen:
                seen.append(n)
        if not seen:
            return ""
        if len(seen) == 1:
            return seen[0]
        return f"{seen[0]} + {len(seen) - 1} more"

    def label(self, rel: str) -> str:
        """'Game  (file)' when the game is known, else the path itself."""
        n = self.name(rel)
        leaf = PurePosixPath(rel).name
        return f"{n}  ({leaf})" if n else rel


def describe(g) -> str:
    """A short name for one SaveGroup: the game, plus what the save is when that adds something."""
    if g.kind == "card":
        names = [i.label() for i in g.inner if i.label()]
        if names:
            more = f" + {len(names) - 1} more" if len(names) > 1 else ""
            return f"Memory card: {names[0]}{more}"
        return ""
    if not (g.title or g.game_id):
        return ""
    base = g.label()
    extra = " ".join(g.detail.split()) if g.detail else ""
    if len(extra) > 48:
        extra = extra[:47].rstrip() + "…"
    if g.kind == "state" and "state" not in extra.lower():
        extra = f"save state {extra}".strip()
    return f"{base} · {extra}" if extra and extra.lower() not in base.lower() else base


def for_location(app, key: str, folder, rels) -> Namer:
    """A Namer for a Save History location key ('emu::label'), using the app's game names."""
    emu, _, label = key.partition("::")
    return Namer(emu, label, folder, rels, getattr(app, "titles", None))


_shared_titles = None


def cached_titles():
    """Game names without loading every database: just what earlier runs learned (cheap, headless)."""
    global _shared_titles
    if _shared_titles is None:
        try:
            import titledb
            _shared_titles = titledb.TitleDB()
        except Exception:  # noqa
            _shared_titles = False
    return _shared_titles or None
