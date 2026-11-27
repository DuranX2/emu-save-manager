"""ROM Library transfer queue: check games out (library -> this machine), check them in (verify the
library has them, then remove the local copy) and add games to the library (this machine -> library).

Jobs run one at a time in a background thread, with progress from rclone's JSON log.  The queue is
saved to disk, so transfers that were waiting or running when the app closed carry on next time;
files that already arrived are skipped by rclone.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

import romlibrary as L

QUEUE_FILE = L.APP_DIR / "queue.json"
SPACE_MARGIN = 2 * 1024 ** 3          # keep 2 GB free after a checkout
KINDS = {"checkout": "Check out", "checkin": "Check in", "add": "Add to library", "remove": "Remove copy"}


class Job:
    FIELDS = ("id", "kind", "key", "title", "size", "state", "done", "speed", "eta", "error", "note",
              "finished")

    def __init__(self, kind, key, title, size, **kw):
        self.id = kw.get("id") or uuid.uuid4().hex[:10]
        self.kind, self.key, self.title, self.size = kind, key, title, size
        self.state = kw.get("state", "queued")   # queued, running, done, failed, cancelled
        self.done = kw.get("done", 0)
        self.speed = 0.0
        self.eta = None
        self.error = kw.get("error", "")
        self.note = kw.get("note", "")
        self.finished = kw.get("finished", 0)

    def to_json(self):
        return {f: getattr(self, f) for f in self.FIELDS}

    @property
    def percent(self):
        if self.state == "done":
            return 100
        return int(100 * self.done / self.size) if self.size else 0


class Cancelled(Exception):
    pass


class TransferQueue:
    def __init__(self, library: L.Library, log=print, persist: Path = QUEUE_FILE, autostart=True):
        self.lib = library
        self.log = log
        self.persist = Path(persist)
        self.jobs = []
        self.lock = threading.RLock()
        self._wake = threading.Event()
        self._proc = None
        self._cancel_current = False
        self._thread = None
        self._stopping = False
        self.changed = 0                 # bumped on every change, so a UI can poll cheaply
        self._load()
        if autostart:
            self.start()

    # -- persistence ----------------------------------------------------------
    def _load(self):
        d = L._read_json(self.persist, {})
        if d.get("location") != self.lib.location:
            return
        for j in d.get("jobs", []):
            if j.get("state") in ("queued", "running"):
                j["state"] = "queued"
                j["note"] = "resumed"
                self.jobs.append(Job(**{k: j.get(k) for k in Job.FIELDS if k in j}))

    def _save(self):
        with self.lock:
            keep = [j.to_json() for j in self.jobs if j.state in ("queued", "running")]
        try:
            L._write_json(self.persist, {"location": self.lib.location, "jobs": keep})
        except OSError:
            pass

    def _bump(self, save=False):
        self.changed += 1
        if save:
            self._save()

    # -- public ---------------------------------------------------------------
    def add(self, kind, keys):
        """Queue jobs; games already waiting or running for the same action are skipped."""
        added = []
        with self.lock:
            busy = {(j.kind, j.key) for j in self.jobs if j.state in ("queued", "running")}
            for k in keys:
                it = self.lib.get(k)
                if not it or (kind, k) in busy:
                    continue
                size = (self.lib.items.get(k) or it).size if kind == "checkout" else it.size
                job = Job(kind, k, it.title, size)
                self.jobs.append(job)
                added.append(job)
        if added:
            self._bump(save=True)
            self._wake.set()
        return added

    def snapshot(self):
        with self.lock:
            return list(self.jobs)

    def pending_checkout_bytes(self):
        with self.lock:
            return sum(max(0, j.size - j.done) for j in self.jobs
                       if j.kind == "checkout" and j.state in ("queued", "running"))

    def cancel(self, job_id):
        with self.lock:
            for j in self.jobs:
                if j.id == job_id:
                    if j.state == "queued":
                        j.state = "cancelled"
                        j.finished = time.time()
                    elif j.state == "running":
                        self._cancel_current = True
                        self._kill()
        self._bump(save=True)

    def cancel_all(self):
        with self.lock:
            for j in self.jobs:
                if j.state == "queued":
                    j.state = "cancelled"
                    j.finished = time.time()
                elif j.state == "running":
                    self._cancel_current = True
                    self._kill()
        self._bump(save=True)

    def clear_finished(self):
        with self.lock:
            self.jobs = [j for j in self.jobs if j.state in ("queued", "running")]
        self._bump()

    def busy(self):
        with self.lock:
            return any(j.state in ("queued", "running") for j in self.jobs)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stopping = False
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
        self._wake.set()

    def stop(self, wait=5):
        """Stop after (cancelling) the current job; queued jobs stay saved for next time."""
        self._stopping = True
        with self.lock:
            running = [j for j in self.jobs if j.state == "running"]
        if running:
            self._kill()
        self._wake.set()
        if self._thread:
            self._thread.join(wait)
        with self.lock:
            for j in self.jobs:
                if j.state == "running":
                    j.state = "queued"
        self._save()

    def wait_idle(self, timeout=60):
        end = time.time() + timeout
        while time.time() < end:
            if not self.busy():
                return True
            time.sleep(0.05)
        return False

    # -- worker -----------------------------------------------------------------
    def _next(self):
        with self.lock:
            for j in self.jobs:
                if j.state == "queued":
                    j.state = "running"
                    j.error = ""
                    return j
        return None

    def _worker(self):
        while not self._stopping:
            job = self._next()
            if not job:
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            self._cancel_current = False
            self._bump(save=True)
            try:
                getattr(self, "_do_" + job.kind)(job)
                job.state = "done"
                job.done = job.size
                self.log(f"{KINDS[job.kind]}: {job.title} - done")
            except Cancelled:
                if self._stopping:
                    job.state = "queued"
                else:
                    job.state = "cancelled"
                    self.log(f"{KINDS[job.kind]}: {job.title} - cancelled")
            except Exception as ex:  # noqa - shown on the job
                job.state = "failed"
                job.error = str(ex)
                self.log(f"{KINDS[job.kind]}: {job.title} - FAILED: {ex}")
            job.finished = time.time()
            job.speed, job.eta = 0.0, None
            self._bump(save=True)

    def _kill(self):
        p = self._proc
        if p:
            try:
                p.terminate()
            except OSError:
                pass

    def _check_cancel(self):
        if self._cancel_current or self._stopping:
            raise Cancelled()

    # -- rclone -------------------------------------------------------------------
    def _copy(self, job, src_dir: str, dst_dir: str, files, base_done=0):
        """Copy files (paths relative to src_dir) with progress; raises on failure."""
        fd, listfile = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(files) + "\n")
        r = self.lib.runner
        cmd = [r.exe] + (["--config", r.conf] if r.conf else []) + list(getattr(r, "extra", []) or []) + [
            "copy", src_dir, dst_dir, "--files-from-raw", listfile, "--no-traverse",
            "--transfers", "2", "--checkers", "2", "--multi-thread-streams", "1",
            "--retries", "3", "--low-level-retries", "10",
            "--use-json-log", "--stats", "1s", "--stats-one-line", "-v"]
        kw = {"creationflags": 0x08000000} if L.IS_WIN else {}
        errors = []
        try:
            self._check_cancel()
            self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                          stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                                          errors="replace", **kw)
            for line in self._proc.stderr:
                try:
                    e = json.loads(line)
                except ValueError:
                    if line.strip():
                        errors.append(line.strip())
                    continue
                st = e.get("stats")
                if st:
                    job.done = min(job.size, base_done + int(st.get("bytes") or 0))
                    job.speed = float(st.get("speed") or 0) or sum(
                        float(t.get("speed") or 0) for t in (st.get("transferring") or []))
                    job.eta = st.get("eta")
                    self._bump()
                elif e.get("level") in ("error", "critical"):
                    errors.append(e.get("msg", "").strip())
            rc = self._proc.wait()
        finally:
            self._proc = None
            try:
                os.unlink(listfile)
            except OSError:
                pass
        self._check_cancel()
        if rc != 0:
            raise RuntimeError(errors[-1] if errors else f"rclone exited with code {rc}")

    def _remote_sizes(self, sys_location: str, files):
        """{relative path: size} of these files in the library (only the ones that exist)."""
        fd, listfile = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(files) + "\n")
        try:
            rc, out = self.lib.runner.capture(["lsjson", "-R", "--files-only", "--no-mimetype",
                                               "--files-from-raw", listfile, "--no-traverse", sys_location],
                                              timeout=600, merge=False)
        finally:
            os.unlink(listfile)
        if rc != 0:
            raise RuntimeError("could not read the library: " + L._last_lines(out))
        try:
            data = json.loads(out[out.index("["):])
        except ValueError:
            data = []
        return {e["Path"]: e.get("Size", 0) for e in data}

    # -- jobs -----------------------------------------------------------------------
    def _local_root(self) -> Path:
        if not self.lib.local_roms:
            raise RuntimeError("Set this machine's ROM folder in Settings first.")
        return Path(self.lib.local_roms)

    def _do_checkout(self, job):
        lib = self.lib
        item = lib.items.get(job.key)
        if not item:
            raise RuntimeError("This game is no longer in the library (refresh the library).")
        dst = self._local_root() / lib.local_dir_for(item.system)
        if item.system in lib.offline or (dst.is_symlink() and not dst.exists()):
            target = lib.offline.get(item.system, ("", os.readlink(dst) if dst.is_symlink() else ""))[1]
            raise RuntimeError(f"{L.system_name(item.system)}'s folder ({dst.name}) is a link to {target}, "
                               f"which isn't there. Insert that SD card, then try again.")
        have = {}
        for rel in item.files:
            try:
                have[rel] = (dst / rel).stat().st_size
            except OSError:
                pass
        already = sum(s for rel, (s, _) in item.files.items() if have.get(rel) == s)
        need = item.size - already
        space = L.disk_space(str(dst))
        if space and need + SPACE_MARGIN > space[2]:
            raise RuntimeError(f"Not enough space: needs {L.human(need)}, {L.human(space[2])} free "
                               f"(and {L.human(SPACE_MARGIN)} is kept free).")
        mine = lib.manifest()
        mine[job.key] = {"size": item.size, "at": int(time.time()), "incomplete": True}
        lib._save_manifest(mine)
        job.done = already
        todo = [rel for rel, (s, _) in item.files.items() if have.get(rel) != s]
        if todo:
            self._copy(job, L.join(lib.location, lib.lib_dir_for(item.system)), str(dst), todo, already)
        bad = []
        for rel, (s, _) in item.files.items():
            try:
                if (dst / rel).stat().st_size != s:
                    bad.append(rel)
            except OSError:
                bad.append(rel)
        if bad:
            raise RuntimeError(f"{len(bad)} file(s) did not arrive complete: {bad[0]}")
        with lib.lock:
            lib.local_sysdirs.setdefault(item.system, dst.name)
            lib.local[job.key] = L.Item(dst.name, item.path, item.title,
                                        {rel: (s, time.time()) for rel, (s, _) in item.files.items()},
                                        item.is_dir)
        lib.record(job.key, True)

    def _do_checkin(self, job):
        lib = self.lib
        item, loc = lib.items.get(job.key), lib.local.get(job.key)
        if not loc:
            lib.record(job.key, False)
            return                                 # already gone from this machine
        if not item:
            raise RuntimeError("This game isn't in the library, so removing it here would lose it. "
                               "Use Add to library first.")
        incomplete = lib.manifest().get(job.key, {}).get("incomplete")
        lib_sizes = self._remote_sizes(L.join(lib.location, lib.lib_dir_for(item.system)), list(loc.files))
        for rel, (s, _) in loc.files.items():
            ls = lib_sizes.get(rel)
            if ls is None:
                raise RuntimeError(f"The library doesn't have {rel}; use Add to library first.")
            if ls != s and not (incomplete and s < ls):
                a, b = (L.human(s), L.human(ls)) if L.human(s) != L.human(ls) else (f"{s:,} bytes", f"{ls:,}")
                raise RuntimeError(f"{rel} differs from the library's copy ({a} here, {b} in the "
                                   f"library); nothing was removed.")
        self._check_cancel()
        self._delete_local(loc)
        with lib.lock:
            lib.local.pop(job.key, None)
        lib.record(job.key, False)

    def _do_remove(self, job):
        """Remove an unfinished checkout (the library has the whole game)."""
        self._do_checkin(job)

    def _delete_local(self, loc):
        root = self._local_root() / loc.sysdir
        for rel in loc.files:
            p = root / rel
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        # tidy up folders the game used (never the system folder itself)
        dirs = sorted({(root / rel).parent for rel in loc.files}, key=lambda p: len(p.parts), reverse=True)
        for d in dirs:
            while d != root and root in d.parents:
                try:
                    d.rmdir()
                except OSError:
                    break
                d = d.parent

    def _do_add(self, job):
        lib = self.lib
        loc = lib.local.get(job.key)
        if not loc:
            raise RuntimeError("This game is no longer on this machine.")
        libdir = lib.lib_dir_for(loc.system)
        dst = L.join(lib.location, libdir)
        self._copy(job, str(self._local_root() / loc.sysdir), dst, list(loc.files))
        sizes = self._remote_sizes(dst, list(loc.files))
        bad = [rel for rel, (s, _) in loc.files.items() if sizes.get(rel) != s]
        if bad:
            raise RuntimeError(f"{len(bad)} file(s) did not arrive complete in the library: {bad[0]}")
        with lib.lock:
            lib.lib_sysdirs.setdefault(loc.system, libdir)
            lib.items[job.key] = L.Item(libdir, loc.path, loc.title, dict(loc.files), loc.is_dir)
        lib.record(job.key, True)
