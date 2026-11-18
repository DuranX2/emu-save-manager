"""
savehistory.py - keeps earlier versions of every save so any change can be undone.

Every sync (window or background) takes a snapshot of each synced folder twice:
  * before the sync  - catches what changed on this device since last time (you played)
  * after the sync   - catches what arrived from the cloud (you played on the other device)
Only files that changed are stored, and identical content is stored once.

Layout (inside the backup folder):
    history/objects/ab/abcdef...   file contents, named by their SHA-256
    history/index.json             which versions each file had, and when

Retention: the newest `keep` versions of each file, and at most `max_mb` in total
(oldest versions go first; the newest version of a file is never removed).  Files
bigger than `max_file_mb` (e.g. Xemu's hard disk image) are not kept.

Restoring writes the old content back with the current time as its date, so the next
sync treats it as the newest copy and uploads it.  The version being replaced is
stored first, so a restore can itself be undone.  Nothing outside history/ is deleted.
No GUI dependencies.
"""

import fnmatch
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

DEFAULTS = {"enabled": True, "keep": 10, "max_mb": 2048, "max_file_mb": 256}
INDEX_VERSION = 1

# What a version's "reason" means, for people.
REASONS = {
    "local": "Changed on this device",
    "cloud": "Arrived from the cloud",
    "before-restore": "Replaced by a restore",
    "restored": "Restored from history",
    "manual": "Snapshot taken by hand",
}

SKIP_PATTERNS = ["desktop.ini", "Thumbs.db", ".DS_Store", "*.esm_tmp", "*.tmp", "*.esmh_tmp",
                 ".hash", ".token", ".temp", ".last_upload", ".last_download", ".fail_upload",
                 ".fail_download", ".pending_upload", ".no_upload", "cloudsync.emudeck",
                 "dl_cloudsync.emudeck"]


def settings_from(cfg_or_section) -> dict:
    """History settings with defaults filled in.  Accepts the whole config or cfg['history']."""
    sec = cfg_or_section or {}
    if "history" in sec and isinstance(sec.get("history"), dict):
        sec = sec["history"]
    out = dict(DEFAULTS)
    for k in DEFAULTS:
        if k in sec:
            out[k] = sec[k]
    return out


def history_dir(backup_dir) -> Path:
    return Path(backup_dir) / "history"


def location_key(emu: str, label: str) -> str:
    return f"{emu}::{label}"


def _skip(name: str) -> bool:
    low = name.lower()
    return any(fnmatch.fnmatchcase(low, p.lower()) for p in SKIP_PATTERNS)


class SaveHistory:
    def __init__(self, root, settings=None):
        self.root = Path(root)
        self.objects = self.root / "objects"
        self.index_file = self.root / "index.json"
        self.settings = settings_from(settings)
        self.index = self._load()

    # ---- index ---------------------------------------------------------------------
    def _load(self) -> dict:
        try:
            data = json.loads(self.index_file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("locations"), dict):
                return data
        except (OSError, ValueError):
            pass
        return {"version": INDEX_VERSION, "locations": {}}

    def save(self):
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.index_file.with_name("index.json.esmh_tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.index, f, separators=(",", ":"))
        os.replace(tmp, self.index_file)

    def _loc(self, key: str, create=False):
        locs = self.index["locations"]
        if key not in locs and create:
            locs[key] = {"folder": "", "files": {}, "runs": []}
        return locs.get(key)

    def object_path(self, sha: str) -> Path:
        return self.objects / sha[:2] / sha

    # ---- storing -------------------------------------------------------------------
    def _store(self, path: Path):
        """Copy a file into objects/ while hashing it.  Returns its SHA-256."""
        self.objects.mkdir(parents=True, exist_ok=True)
        tmp = self.objects / f"incoming-{os.getpid()}-{time.time_ns()}.esmh_tmp"
        h = hashlib.sha256()
        try:
            with open(path, "rb") as src, open(tmp, "wb") as dst:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    h.update(chunk)
                    dst.write(chunk)
            sha = h.hexdigest()
            final = self.object_path(sha)
            if final.is_file():
                tmp.unlink()
            else:
                final.parent.mkdir(parents=True, exist_ok=True)
                os.replace(tmp, final)
            return sha
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise

    def snapshot(self, key: str, folder, reason: str, when=None, log=None, exclude_dirs=()) -> int:
        """Store every file in `folder` that changed since the last snapshot.
        Returns the number of new versions.  Does not save the index (call save())."""
        folder = Path(folder)
        if not self.settings.get("enabled", True) or not folder.is_dir():
            return 0
        when = when or time.time()
        limit = float(self.settings.get("max_file_mb", 256)) * 1024 * 1024
        loc = self._loc(key, create=True)
        loc["folder"] = str(folder)
        skip_dirs = {d.lower() for d in exclude_dirs}
        files = loc["files"]
        added = 0
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames[:] = [d for d in dirnames if not _skip(d) and d.lower() not in skip_dirs]
            for name in filenames:
                if _skip(name):
                    continue
                p = Path(dirpath) / name
                try:
                    st = p.stat()
                except OSError:
                    continue
                if st.st_size > limit:
                    continue
                rel = p.relative_to(folder).as_posix()
                entry = files.setdefault(rel, {"seen": None, "versions": []})
                seen = [st.st_size, st.st_mtime_ns]
                if entry["seen"] == seen and entry["versions"]:
                    continue
                try:
                    sha = self._store(p)
                except OSError as ex:
                    if log:
                        log(f"History: could not keep {rel}: {ex}")
                    continue
                entry["seen"] = seen
                vs = entry["versions"]
                if vs and vs[-1]["sha"] == sha:
                    continue
                vs.append({"sha": sha, "size": st.st_size, "mtime": st.st_mtime,
                           "taken": when, "reason": reason})
                added += 1
        if added:
            runs = loc.setdefault("runs", [])
            if not runs or runs[-1] != when:
                runs.append(when)
        return added

    # ---- reading -------------------------------------------------------------------
    def keys(self):
        return sorted(self.index["locations"])

    def files(self, key: str):
        """[(rel, number of versions, time of last change)] for a location."""
        loc = self._loc(key)
        if not loc:
            return []
        out = [(rel, len(e["versions"]), e["versions"][-1]["taken"])
               for rel, e in loc["files"].items() if e["versions"]]
        out.sort(key=lambda t: t[0].lower())
        return out

    def versions(self, key: str, rel: str):
        """Versions of one file, newest first."""
        loc = self._loc(key)
        if not loc or rel not in loc["files"]:
            return []
        return list(reversed(loc["files"][rel]["versions"]))

    def points(self, key: str):
        """Times at which something in this location changed, newest first."""
        loc = self._loc(key)
        return sorted(set(loc.get("runs", [])), reverse=True) if loc else []

    def state_at(self, key: str, when: float):
        """{rel: version} - how the folder looked right after the snapshot at `when`."""
        loc = self._loc(key)
        out = {}
        if not loc:
            return out
        for rel, e in loc["files"].items():
            best = None
            for v in e["versions"]:
                if v["taken"] <= when + 1e-6:
                    best = v
            if best:
                out[rel] = best
        return out

    def usage(self) -> int:
        """Bytes used by stored versions."""
        total = 0
        for sha in self._referenced():
            try:
                total += self.object_path(sha).stat().st_size
            except OSError:
                pass
        return total

    def _referenced(self):
        refs = set()
        for loc in self.index["locations"].values():
            for e in loc["files"].values():
                for v in e["versions"]:
                    refs.add(v["sha"])
        return refs

    # ---- restoring -----------------------------------------------------------------
    @staticmethod
    def _safe_target(folder: Path, rel: str) -> Path:
        target = (folder / rel)
        base = os.path.abspath(folder)
        if os.path.commonpath([base, os.path.abspath(target)]) != base:
            raise ValueError(f"Unsafe path in history: {rel}")
        return target

    def _write_back(self, folder: Path, rel: str, version: dict, now: float):
        src = self.object_path(version["sha"])
        if not src.is_file():
            raise FileNotFoundError(f"The stored copy of {rel} is missing from the history folder.")
        target = self._safe_target(folder, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".esm_tmp")
        shutil.copyfile(src, tmp)
        os.utime(tmp, (now, now))                    # newest copy -> the next sync uploads it
        os.replace(tmp, target)
        st = target.stat()
        return [st.st_size, st.st_mtime_ns]

    def restore(self, key: str, picks, folder=None, log=None) -> int:
        """Write versions back.  picks: {rel: version}.  The current files are kept in the
        history first.  Returns the number of files written."""
        loc = self._loc(key)
        if not loc:
            raise KeyError(key)
        folder = Path(folder or loc["folder"])
        now = time.time()
        self.snapshot(key, folder, "before-restore", when=now - 0.001, log=log)
        written = 0
        for rel, v in picks.items():
            entry = loc["files"][rel]
            target = self._safe_target(folder, rel)
            if target.is_file() and entry["versions"] and entry["versions"][-1]["sha"] == v["sha"]:
                continue                                # already the content on disk
            entry["seen"] = self._write_back(folder, rel, v, now)
            entry["versions"].append({"sha": v["sha"], "size": v["size"], "mtime": now,
                                      "taken": now, "reason": "restored"})
            written += 1
            if log:
                log(f"Restored {rel} ({fmt_time(v['taken'])} version)")
        if written:
            loc.setdefault("runs", []).append(now)
        self.prune()
        self.save()
        return written

    def restore_file(self, key: str, rel: str, version: dict, folder=None, log=None) -> int:
        return self.restore(key, {rel: version}, folder, log)

    def restore_folder(self, key: str, when: float, folder=None, log=None) -> int:
        """Put every file back the way it was at `when`.  Files that did not exist then
        are left alone (nothing is deleted)."""
        return self.restore(key, self.state_at(key, when), folder, log)

    def export(self, version: dict, dest):
        shutil.copyfile(self.object_path(version["sha"]), dest)

    # ---- pruning -------------------------------------------------------------------
    def prune(self) -> int:
        """Apply the retention limits and delete stored contents nothing refers to.
        Returns the number of versions dropped."""
        keep = max(1, int(self.settings.get("keep", 10)))
        dropped = 0
        for loc in self.index["locations"].values():
            for e in loc["files"].values():
                extra = len(e["versions"]) - keep
                if extra > 0:
                    del e["versions"][:extra]
                    dropped += extra
        cap = float(self.settings.get("max_mb", 2048)) * 1024 * 1024
        sizes = {}
        for sha in self._referenced():
            try:
                sizes[sha] = self.object_path(sha).stat().st_size
            except OSError:
                sizes[sha] = 0
        total = sum(sizes.values())
        if total > cap:
            # every version except each file's newest, oldest first
            cands = []
            for loc in self.index["locations"].values():
                for e in loc["files"].values():
                    for v in e["versions"][:-1]:
                        cands.append((v["taken"], id(v), e, v))
            cands.sort(key=lambda t: (t[0], t[1]))
            refcount = {}
            for loc in self.index["locations"].values():
                for e in loc["files"].values():
                    for v in e["versions"]:
                        refcount[v["sha"]] = refcount.get(v["sha"], 0) + 1
            for _t, _i, e, v in cands:
                if total <= cap:
                    break
                e["versions"].remove(v)
                dropped += 1
                refcount[v["sha"]] -= 1
                if refcount[v["sha"]] == 0:
                    total -= sizes.get(v["sha"], 0)
        self._collect_garbage()
        for loc in self.index["locations"].values():
            used = {v["taken"] for e in loc["files"].values() for v in e["versions"]}
            loc["runs"] = [r for r in loc.get("runs", []) if r in used]
        return dropped

    def _collect_garbage(self):
        refs = self._referenced()
        if not self.objects.is_dir():
            return
        cutoff = time.time() - 3600
        for sub in self.objects.iterdir():
            try:
                if sub.is_file():                     # a leftover incoming-*.esmh_tmp
                    if sub.stat().st_mtime < cutoff:
                        sub.unlink()
                    continue
                for f in sub.iterdir():
                    if f.name not in refs:
                        f.unlink()
                if not any(sub.iterdir()):
                    sub.rmdir()
            except OSError:
                pass


def fmt_time(t: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))


def snapshot_jobs(jobs, backup_dir, settings, reason, log=None, when=None) -> int:
    """Snapshot each sync job's local folder ({'emu', 'label', 'local'}).  Never raises:
    a problem with the history must not stop a sync."""
    try:
        s = settings_from(settings)
        if not s.get("enabled", True):
            return 0
        h = SaveHistory(history_dir(backup_dir), s)
        when = when or time.time()
        added = 0
        for j in jobs:
            added += h.snapshot(location_key(j["emu"], j["label"]), j["local"], reason, when, log,
                                exclude_dirs=j.get("exclude") or ())
        if added:
            h.prune()
        h.save()
        return added
    except Exception as ex:  # noqa
        if log:
            log(f"History: snapshot skipped ({ex})")
        return 0
