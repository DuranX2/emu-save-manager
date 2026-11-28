"""
syncplan.py - decides what each sync copies, using what both sides looked like at the last sync.

"Newest file wins" can't tell *which side changed*: a save edited on the Deck but dated older than
the PC's copy loses, and when both devices played the same game one of the two sessions silently
disappears.  So after every sync the agreed state of each item (size and date on both sides) is
stored per synced folder in ~/.emu_save_manager/sync_state/.  The next sync compares against it:

  * changed only on this device  -> upload        * changed only in the cloud -> download
  * changed on both, now different -> CONFLICT: nothing is copied either way; the item is listed
    until the person picks a side in Cloud Sync ("Keep this device's" / "Keep the cloud's")
  * never synced before (no state yet) -> the newer copy wins, as before

Items: a file, except inside a PCSX2 folder memory card ("<name>.ps2/" folder), where each game's
save folder is one item that is always copied whole, so a save never ends up half from each device.
A card that is a file on one side and a folder on the other is reported once and skipped.
Never deletes anything; replaced files still go to the backup folders (rclone --backup-dir), and an
empty file never replaces one with content: the copy with content is sent the other way instead, so an
empty copy (e.g. left in the cloud by the 2026-11-17 upload problem) is repaired, not kept forever.
"""

import fnmatch
import hashlib
import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path

import cloudsync as cs

APP_DIR = Path.home() / ".emu_save_manager"
STATE_DIR = APP_DIR / "sync_state"
STATE_VERSION = 1
TOLERANCE = 1.0                      # seconds: OneDrive keeps whole seconds
SKIP = [p.lower() for p in cs.EXCLUDES] + ["*.partial", "*.esmh_tmp"]


# ---- listings ------------------------------------------------------------------------------
def _skip(rel: str) -> bool:
    return any(fnmatch.fnmatchcase(part.lower(), p) for part in rel.split("/") for p in SKIP)


def local_listing(root) -> dict:
    """{relative path: (size, mtime)} for every file under root."""
    root = Path(root)
    out = {}
    if not root.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _skip(d)]
        for f in filenames:
            p = Path(dirpath) / f
            rel = p.relative_to(root).as_posix()
            if _skip(rel):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            out[rel] = (st.st_size, st.st_mtime)
    return out


def _parse_time(s: str) -> float:
    s = s.strip().replace("Z", "+00:00")
    if "." in s:                                   # rclone gives up to 9 fraction digits
        head, rest = s.split(".", 1)
        digits = "".join(ch for ch in rest if ch.isdigit())
        tz = rest[len(digits):]
        s = f"{head}.{digits[:6].ljust(6, '0')}{tz}"
    return datetime.fromisoformat(s).timestamp()


def remote_listing(runner, remote: str):
    """{relative path: (size, mtime)}, {} if the folder doesn't exist yet, None if listing failed."""
    rc, out = runner.capture(["lsjson", remote, "-R", "--files-only", "--no-mimetype",
                              "--log-level", "ERROR"])
    if rc == 3 or (rc != 0 and "directory not found" in out.lower()):
        return {}
    if rc != 0:
        return None
    try:
        items = json.loads(out[out.index("["): out.rindex("]") + 1])
    except ValueError:
        return None
    res = {}
    for i in items:
        rel = i.get("Path", "")
        if i.get("IsDir") or _skip(rel):
            continue
        try:
            res[rel] = (int(i.get("Size", 0)), _parse_time(i.get("ModTime", "")))
        except (ValueError, TypeError):
            res[rel] = (int(i.get("Size", 0) or 0), 0.0)
    return res


# ---- items ---------------------------------------------------------------------------------
def folder_cards(*listings) -> set:
    """Top-level '<name>.ps2' folders (PCSX2 folder memory cards) on either side."""
    out = set()
    for lst in listings:
        for rel in lst:
            parts = rel.split("/")
            if len(parts) >= 2 and parts[0].lower().endswith(".ps2"):
                out.add(parts[0])
    return out


def item_of(rel: str, cards: set) -> str:
    parts = rel.split("/")
    if parts[0] in cards and len(parts) >= 3:
        return f"{parts[0]}/{parts[1]}"            # one game's save folder
    return rel


def group(listing: dict, cards: set) -> dict:
    out = {}
    for rel, v in listing.items():
        out.setdefault(item_of(rel, cards), {})[rel] = v
    return out


def type_mismatches(local: dict, remote: dict) -> set:
    """Names that are a file on one side and a folder on the other (top level or anywhere)."""
    def dirs(lst):
        d = set()
        for rel in lst:
            parts = rel.split("/")
            for i in range(1, len(parts)):
                d.add("/".join(parts[:i]))
        return d
    return (set(local) & dirs(remote)) | (set(remote) & dirs(local))


def _same(a, b) -> bool:
    return a is not None and b is not None and a[0] == b[0] and abs(a[1] - b[1]) <= TOLERANCE


def _changed(now: dict, before: dict, count_removed=False) -> bool:
    """New or modified files count.  A file that disappeared counts only inside a folder-card save
    (where the game itself removed it); elsewhere nothing is ever deleted."""
    if any(not _same(v, tuple(before.get(rel) or ())) if before.get(rel) else True for rel, v in now.items()):
        return True
    return count_removed and bool(set(before) - set(now))


def _newest(files: dict) -> float:
    return max((v[1] for v in files.values()), default=0.0)


# ---- state ---------------------------------------------------------------------------------
def state_file(local, rpath) -> Path:
    key = hashlib.sha1(f"{Path(local)}|{rpath}".encode("utf-8")).hexdigest()[:16]
    return STATE_DIR / f"{key}.json"


def load_state(local, rpath) -> dict:
    try:
        st = json.loads(state_file(local, rpath).read_text(encoding="utf-8"))
        if st.get("version") == STATE_VERSION:
            return st
    except (OSError, ValueError):
        pass
    return {"version": STATE_VERSION, "items": {}, "conflicts": {}, "resolve": {}}


def save_state(local, rpath, st: dict):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    p = state_file(local, rpath)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, p)


def _snap(files: dict) -> dict:
    return {rel: [v[0], round(v[1], 3)] for rel, v in files.items()}


# ---- the plan ------------------------------------------------------------------------------
def plan(local: dict, remote: dict, st: dict, mode: str = "both") -> dict:
    """What to copy.  Returns {'down': [rel], 'up': [rel], 'conflicts': {item: info},
    'protected': [rel], 'mismatch': [name], 'agreed': {item: (L, R)}, 'moved': {item: dir}}."""
    mism = type_mismatches(local, remote)
    def blocked(rel):
        return any(rel == m or rel.startswith(m + "/") for m in mism)
    local = {r: v for r, v in local.items() if not blocked(r)}
    remote = {r: v for r, v in remote.items() if not blocked(r)}
    cards = folder_cards(local, remote)
    L, R = group(local, cards), group(remote, cards)
    out = {"down": [], "up": [], "conflicts": {}, "protected": [], "mismatch": sorted(mism),
           "agreed": {}, "moved": {}, "extra_local": [], "extra_remote": []}
    items = st.get("items", {})
    for it in sorted(set(L) | set(R)):
        lf, rf = L.get(it, {}), R.get(it, {})
        if set(lf) == set(rf) and all(_same(lf[k], rf[k]) for k in lf):
            out["agreed"][it] = (lf, rf)
            continue
        prev = items.get(it)
        whole = "/" in it and it.split("/", 1)[0] in cards     # a folder-card save: copied as a unit
        force = st.get("resolve", {}).get(it)
        if force in ("local", "cloud"):
            winner = "up" if force == "local" else "down"
        elif not rf:
            winner = "up"
        elif not lf:
            winner = "down"
        elif prev is None:                               # never synced: newer wins, as before
            nl, nr = _newest(lf), _newest(rf)
            if abs(nl - nr) <= TOLERANCE:
                out["conflicts"][it] = _conflict_info(it, lf, rf, "same date, different content")
                continue
            winner = "up" if nl > nr else "down"
        else:
            cl = _changed(lf, prev.get("l", {}), whole)
            cr = _changed(rf, prev.get("r", {}), whole)
            el, er = _empty(lf), _empty(rf)
            if cl and cr and el != er:                   # one side became empty: the side with data wins
                cl, cr = er, el
            if cl and cr:
                out["conflicts"][it] = _conflict_info(it, lf, rf, "changed on both since the last sync")
                continue
            if cl or cr:
                winner = "up" if cl else "down"
            else:                                        # e.g. an earlier copy failed: newer wins
                winner = "up" if _newest(lf) > _newest(rf) else "down"
        src, dst = (lf, rf) if winner == "up" else (rf, lf)
        sent = []
        for rel, v in src.items():
            if _same(v, dst.get(rel)):
                continue
            if v[0] == 0 and (dst.get(rel) or (0,))[0] > 0:
                out["protected"].append(rel)             # an empty file never replaces a real one;
                out["down" if winner == "up" else "up"].append(rel)   # the real one repairs the empty side
                continue
            sent.append(rel)
        out[winner].extend(sent)
        out["moved"][it] = winner
        if whole:                                        # leftovers of the losing copy go to the backup
            extra = sorted(set(dst) - set(src))
            out["extra_remote" if winner == "up" else "extra_local"].extend(extra)
    if mode == "download":
        out["up"], out["extra_remote"] = [], []
    elif mode == "upload":
        out["down"], out["extra_local"] = [], []
    return out


def _empty(files) -> bool:
    """Every file of an item is empty (0 bytes): there is nothing in it to keep."""
    return bool(files) and all(v[0] == 0 for v in files.values())


def _conflict_info(item, lf, rf, why):
    return {"local_time": _newest(lf), "cloud_time": _newest(rf),
            "local_size": sum(v[0] for v in lf.values()), "cloud_size": sum(v[0] for v in rf.values()),
            "files": len(set(lf) | set(rf)), "why": why, "found": time.time()}


# ---- running it ------------------------------------------------------------------------------
def _copy(runner, src, dst, rels, dry, backup_dir):
    fd, lst = tempfile.mkstemp(prefix="esm_files_", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(rels) + "\n")
    args = ["copy", src, dst, "--files-from-raw", lst, "-v", "--stats", "0",
            "--transfers", str(cs.TRANSFERS), "--checkers", str(cs.CHECKERS), "--no-traverse"]
    if dry:
        args.append("--dry-run")
    if backup_dir:
        args += ["--backup-dir", str(backup_dir)]
    try:
        return runner.run(args)
    finally:
        try:
            os.unlink(lst)
        except OSError:
            pass


def _set_aside(runner, local_dir, rpath, p, dry, local_backup, remote_backup, log) -> bool:
    """Move files that only the losing copy of a folder-card save has into the backup folder,
    so the save ends up exactly like the winning copy."""
    import shutil
    ok = True
    if p["extra_local"]:
        log(f"Moving {len(p['extra_local'])} leftover file(s) of replaced saves to the backup folder.")
        for rel in p["extra_local"]:
            if dry or not local_backup:
                continue
            dst = Path(local_backup) / rel
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(local_dir / rel), str(dst))
            except OSError as ex:
                log(f"Couldn't move {rel} aside: {ex}")
                ok = False
    if p["extra_remote"]:
        log(f"Moving {len(p['extra_remote'])} leftover file(s) of replaced saves to the cloud backup folder.")
        for rel in p["extra_remote"]:
            if dry or not remote_backup:
                continue
            if runner.run(["moveto", f"{rpath}/{rel}", f"{remote_backup}/{rel}"]) != 0:
                ok = False
    return ok


def _namer(job, local_dir, local):
    """Game names for this folder's files (for the log), or None if they can't be worked out."""
    try:
        import savenames
        return savenames.Namer(job.get("emu", ""), job.get("label", ""), local_dir, list(local),
                               savenames.cached_titles())
    except Exception:  # noqa - the log line just goes without a name
        return None


def sync_job(runner, job: dict, rpath: str, mode: str, dry: bool, local_backup, remote_backup, log) -> dict:
    """Sync one folder.  job: {'emu', 'label', 'local'}.  Returns {'ok': bool, 'nothing_in_cloud',
    'uploaded': bool, 'conflicts': n, 'down': n, 'up': n}."""
    local_dir = Path(job["local"])
    res = {"ok": False, "nothing_in_cloud": False, "uploaded": False, "conflicts": 0, "down": 0, "up": 0}
    remote = remote_listing(runner, rpath)
    if remote is None:
        log("Couldn't list the cloud folder - skipped this time.")
        return res
    local = local_listing(local_dir)
    skip = {d.lower() for d in job.get("exclude") or ()}
    if skip:                                       # sub-folders that aren't saves (e.g. Xenia DLC)
        def keep(rel):
            return not any(part.lower() in skip for part in rel.split("/")[:-1])
        local = {r: v for r, v in local.items() if keep(r)}
        remote = {r: v for r, v in remote.items() if keep(r)}
    if not remote and mode == "download":
        res.update(ok=True, nothing_in_cloud=True)
        return res
    st = load_state(local_dir, rpath)
    p = plan(local, remote, st, mode)
    for name in p["mismatch"]:
        here = "a folder" if any(r.startswith(name + "/") for r in local) else "a file"
        there = "a file" if here == "a folder" else "a folder"
        log(f"Skipped {name}: it's {here} on this device but {there} in the cloud (for a PCSX2 card: "
            f"a folder card on one side, a single-file card on the other). Move one of them aside to sync it.")
    if p["protected"]:
        log(f"Protected {len(p['protected'])} file(s): the incoming copy is empty, so the real one is kept "
            f"and sent to replace the empty copy (which goes to the backup folder). First: {p['protected'][0]}")
    names = _namer(job, local_dir, local) if p["conflicts"] else None
    for it, c in p["conflicts"].items():
        game = names.name(it) if names else ""
        log(f"CONFLICT {it}{f' ({game})' if game else ''}: {c['why']} (this device {_when(c['local_time'])}, cloud {_when(c['cloud_time'])})"
            f" - not copied either way. Choose a side under Conflicts in Cloud Sync.")
    log(f"Plan: {len(p['down'])} file(s) to download, {len(p['up'])} to upload, "
        f"{len(p['conflicts'])} conflict(s).")
    ok = True
    ok &= _set_aside(runner, local_dir, rpath, p, dry, local_backup, remote_backup, log)
    if p["down"]:
        ok &= _copy(runner, rpath, str(local_dir), p["down"], dry, local_backup) == 0
    if p["up"] and not runner.cancelled:
        rc = _copy(runner, str(local_dir), rpath, p["up"], dry, remote_backup)
        ok &= rc == 0
        res["uploaded"] = rc == 0 and not dry
    if runner.cancelled:
        ok = False
    res.update(ok=ok, conflicts=len(p["conflicts"]), down=len(p["down"]), up=len(p["up"]),
               mismatch=len(p["mismatch"]))
    if not dry:
        _record(local_dir, rpath, st, p, local, remote, ok, mode, job)
    return res


def _record(local_dir, rpath, st, p, local, remote, ok, mode, job):
    """Remember the agreed state of every item that is now the same on both sides."""
    cards = folder_cards(local, remote)
    L, R = group(local, cards), group(remote, cards)
    items = st.setdefault("items", {})
    for it, (lf, rf) in p["agreed"].items():
        items[it] = {"l": _snap(lf), "r": _snap(rf)}
        st["conflicts"].pop(it, None)
        st["resolve"].pop(it, None)
    if ok:
        prot = set(p["protected"])
        for it, winner in p["moved"].items():
            if (winner == "up" and mode == "download") or (winner == "down" and mode == "upload"):
                continue
            src = dict(L.get(it, {}) if winner == "up" else R.get(it, {}))
            dst = dict(R.get(it, {}) if winner == "up" else L.get(it, {}))
            back = "down" if winner == "up" else "up"
            if not ((back == "up" and mode == "download") or (back == "down" and mode == "upload")):
                for rel in prot & set(src):              # repaired: the real copy is now on both sides
                    if rel in dst:
                        src[rel] = dst[rel]
            whole = "/" in it and it.split("/", 1)[0] in cards
            after = ({rel: v for rel, v in dst.items() if rel in prot} if whole   # becomes the winner's
                     else dict(dst))
            for rel, v in src.items():
                if rel not in prot:
                    after[rel] = v                       # the copy gave dst the source's size and date
            lf, rf = (src, after) if winner == "up" else (after, src)
            items[it] = {"l": _snap(lf), "r": _snap(rf)}
            st["conflicts"].pop(it, None)
            st["resolve"].pop(it, None)
    for it, c in p["conflicts"].items():
        st["conflicts"][it] = c
    st.update(emu=job.get("emu", ""), label=job.get("label", ""), local=str(local_dir), remote=rpath,
              updated=time.time())
    save_state(local_dir, rpath, st)


def _when(t: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "?"


# ---- conflicts for the window ------------------------------------------------------------------
def all_conflicts():
    """[(state file, state, item, info)] across every synced folder on this device."""
    out = []
    if not STATE_DIR.is_dir():
        return out
    for f in sorted(STATE_DIR.glob("*.json")):
        try:
            st = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for it, info in sorted(st.get("conflicts", {}).items()):
            out.append((f, st, it, info))
    return out


def choose(state_path: Path, item: str, side: str):
    """Record the person's choice ('local' or 'cloud'); the next sync applies it."""
    st = json.loads(Path(state_path).read_text(encoding="utf-8"))
    st.setdefault("resolve", {})[item] = side
    tmp = Path(state_path).with_suffix(".tmp")
    tmp.write_text(json.dumps(st, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, state_path)


def conflict_count() -> int:
    return sum(1 for _f, st, it, _i in all_conflicts() if it not in st.get("resolve", {}))
