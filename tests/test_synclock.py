"""SyncLock: locks left by dead processes or a previous boot are cleared at once, and a manual sync
can stop a running background sync (it stops cleanly; one that ignores the request is ended)."""
import json, os, shutil, subprocess, sys, textwrap, time
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/synclock"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
sys.path.insert(0, ROOT)
import autosync

L = autosync.LOCK_FILE
assert str(L).startswith(str(T)), L

# 1. normal: second acquire fails while held, works after release; the file says who holds it
a, b = autosync.SyncLock(kind="window"), autosync.SyncLock()
assert a.acquire() and not b.acquire()
info = a.owner(); assert info["pid"] == os.getpid() and info["kind"] == "window" and info["boot"]
a.release(); assert b.acquire(); b.release()
print("1. one sync at a time; the lock names its holder: ok")

# 2. a lock from a process that no longer exists is cleared at once (was: up to 3 hours)
p = subprocess.Popen([sys.executable, "-c", "pass"]); p.wait()
L.write_text(json.dumps({"pid": p.pid, "started": time.time(), "kind": "background", "boot": autosync._boot_id()}))
assert autosync.SyncLock().busy_with() is None and autosync.SyncLock().acquire()
L.unlink()
# old one-line format from v1.2.0 with a dead pid
L.write_text(f"{p.pid} {time.time()}\n"); assert autosync.SyncLock().acquire(); L.unlink()
# a live pid, but from before a restart
L.write_text(json.dumps({"pid": os.getppid(), "started": time.time(), "kind": "background", "boot": "old-boot"}))
assert autosync.SyncLock().acquire(); L.unlink()
print("2. locks from dead processes, the old format and a previous boot are cleared: ok")

# 3. a background sync that listens (watch_for_stop) stops cleanly when asked
helper = T / "emu_save_manager_bg.py"      # named like the app: only the app is ever ended
helper.write_text(textwrap.dedent(f"""
    import os, sys, time
    os.environ["HOME"] = {str(T / 'home')!r}
    sys.path.insert(0, {ROOT!r})
    import autosync
    class R:
        cancelled = False
        def cancel(self): self.cancelled = True
    r = R(); lock = autosync.SyncLock()
    assert lock.acquire()
    stop = autosync.watch_for_stop(r, lambda m: print(m, flush=True), interval=0.2)
    listen = sys.argv[1] == "listen"
    for _ in range(600):
        if listen and r.cancelled: break
        time.sleep(0.1)
    stop(); lock.release(); print("released", flush=True)
"""))
bg = subprocess.Popen([sys.executable, str(helper), "listen"], stdout=subprocess.PIPE, text=True)
for _ in range(50):
    if autosync.SyncLock().busy_with(): break
    time.sleep(0.1)
assert autosync.SyncLock().busy_with()["pid"] == bg.pid
t = time.time(); w = autosync.SyncLock(kind="window")
assert w.request_stop(timeout=10) and w.acquire()
out = bg.communicate(timeout=10)[0]
assert "taking over" in out and "released" in out and time.time() - t < 5, out
assert not autosync.STOP_FILE.exists()
w.release()
print(f"3. background sync stopped cleanly for the manual sync in {time.time() - t:.1f}s: ok")

# 4. one that ignores the request is ended after the timeout (it's this app's process)
bg = subprocess.Popen([sys.executable, str(helper), "ignore"], stdout=subprocess.PIPE, text=True)
for _ in range(50):
    if autosync.SyncLock().busy_with(): break
    time.sleep(0.1)
msgs = []
w = autosync.SyncLock(kind="window")
assert w.request_stop(timeout=1.5, log=msgs.append), msgs
assert bg.wait(timeout=10) != 0 and any("ending it" in m for m in msgs)
assert w.acquire(); w.release()
print("4. a background sync that doesn't respond is ended: ok")

# 5. never ends a process that isn't this app
sleeper = subprocess.Popen(["sleep", "30"])
L.write_text(json.dumps({"pid": sleeper.pid, "started": time.time(), "kind": "background", "boot": autosync._boot_id()}))
assert not autosync.SyncLock().request_stop(timeout=1) and sleeper.poll() is None
sleeper.kill(); sleeper.wait(); L.unlink()
print("5. an unrelated process holding the pid is left alone: ok")
print("SYNCLOCK OK")
