"""The ROM Library keeps its use of the share's connections small (Windows 10/11 accept only 20)."""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
T = Path(tempfile.mkdtemp(prefix="smblim-"))
os.environ["HOME"] = str(T / "home")
os.environ["USERPROFILE"] = str(T / "home")

import cloudsync
import romlibrary as L

RCLONE = shutil.which("rclone")

# 1. the runner adds extra flags to every command, last, so they win over the command's own flags
r = cloudsync.RcloneRunner("rclone", "/x/rclone.conf", lambda m: None)
assert r._cmd(["lsf", "a:"]) == ["rclone", "--config", "/x/rclone.conf", "lsf", "a:"]
L.limit_runner(r); L.limit_runner(r)                      # idempotent
assert r.extra == L.LIBRARY_FLAGS, r.extra
assert r._cmd(["lsf", "a:"]) == ["rclone", "--config", "/x/rclone.conf", "lsf", "a:"] + L.LIBRARY_FLAGS
cmd = r._cmd(["copy", "a:", "b", "--transfers", "4"])       # the pictures' own --transfers 4 ...
assert cmd[-len(L.LIBRARY_FLAGS):] == L.LIBRARY_FLAGS and cmd.index("4") < cmd.index("2")   # ... loses
assert r.gate is L.SHARE_GATE
c = cloudsync.RcloneRunner("rclone", "", lambda m: None)   # cloud sync is unchanged
assert c._cmd(["sync", "a", "b"]) == ["rclone", "sync", "a", "b"]
print("1. the library's runner limits checkers, transfers and multi-thread streams; cloud sync's doesn't: ok")

# 2. an existing [romlibrary] SMB remote gets idle_timeout; nothing else changes
conf = T / "rclone.conf"
before = ("[other]\ntype = local\n\n[romlibrary]\ntype = smb\nhost = nuc\nuser = u\npass = OBSCURED\n\n"
          "[after]\ntype = smb\nhost = h\n")
conf.write_text(before)
assert L.ensure_share_options(conf)
after = conf.read_text()
assert "[romlibrary]\ntype = smb\nhost = nuc\nuser = u\npass = OBSCURED\nidle_timeout = 15s\n\n[after]" in after, after
assert after.replace("idle_timeout = 15s\n", "") == before
assert not L.ensure_share_options(conf) and conf.read_text() == after      # only once
conf.write_text("[romlibrary]\ntype = smb\nhost = nuc")                    # last section, no newline
assert L.ensure_share_options(conf) and conf.read_text() == "[romlibrary]\ntype = smb\nhost = nuc\nidle_timeout = 15s\n"
conf.write_text("[romlibrary]\ntype = local\n")                             # not SMB: left alone
assert not L.ensure_share_options(conf)
conf.write_text("[x]\ntype = smb\n")                                        # someone else's remote
assert not L.ensure_share_options(conf)
assert not L.ensure_share_options(T / "missing.conf")
print("2. an existing share remote gets idle_timeout once; other remotes and settings are untouched: ok")

# 3. a new share remote is written with it (rclone obscure needs the real rclone)
if RCLONE:
    L.OWN_RCLONE_CONF = T / "own" / "rclone.conf"
    run = cloudsync.RcloneRunner(RCLONE, str(L.OWN_RCLONE_CONF), lambda m: None)
    loc = L.setup_share(run, "nuc", "BigBoy", "Games", "user", "pw")
    text = L.OWN_RCLONE_CONF.read_text()
    assert "type = smb" in text and "idle_timeout = 15s" in text and "pw\n" not in text
    # rclone itself accepts the setting and the flags
    out = subprocess.run([RCLONE, "--config", str(L.OWN_RCLONE_CONF), "config", "show", "romlibrary"],
                         capture_output=True, text=True)
    assert out.returncode == 0 and "idle_timeout = 15s" in out.stdout, out.stderr
    d = T / "lib"; (d / "PS2").mkdir(parents=True); (d / "PS2" / "a.iso").write_bytes(b"x" * 10)
    lib_runner = L.limit_runner(cloudsync.RcloneRunner(RCLONE, "", lambda m: None))
    rc, js = lib_runner.capture(["lsjson", "-R", "--files-only", str(d)], merge=False)
    assert rc == 0 and "a.iso" in js, js
    # 4. a Library made with the app's own conf upgrades an older share remote
    L.OWN_RCLONE_CONF.write_text("[romlibrary]\ntype = smb\nhost = nuc\n")
    lib = L.Library(cloudsync.RcloneRunner(RCLONE, str(L.OWN_RCLONE_CONF), lambda m: None),
                    str(d), str(T / "roms"))
    assert "idle_timeout = 15s" in L.OWN_RCLONE_CONF.read_text()
    assert lib.runner.extra == L.LIBRARY_FLAGS
    print("3-4. new and existing share remotes get idle_timeout; rclone accepts the flags and setting: ok")
else:
    print("3-4. skipped (no rclone)")

# 5. check-out/check-in copies use the same small limits and wait for a free slot
src = Path(ROOT, "libqueue.py").read_text()
assert '"--checkers", "2", "--multi-thread-streams", "1"' in src and 'getattr(r, "extra"' in src
assert "gate.acquire(timeout=0.5)" in src
print("5. copies use 2 transfers, 2 checkers, one stream per file, and share the slots: ok")

# 6. real rclone: with a repeated flag, the last one wins (that's why the limits go last)
if RCLONE:
    import json as _j
    o = subprocess.run([RCLONE, "rc", "--loopback", "options/get", "--transfers", "4", "--transfers", "2"],
                       capture_output=True, text=True)
    if o.returncode == 0:
        assert _j.loads(o.stdout)["main"]["Transfers"] == 2, o.stdout[:200]
        print("6. rclone takes the last --transfers: ok")
    else:
        print("6. skipped (this rclone has no rc --loopback)")

# 7. at most SHARE_COMMANDS library commands run at once (the rest wait their turn)
import threading, time
peak, now, lock = 0, 0, threading.Lock()
class Slow(cloudsync.RcloneRunner):
    def _capture(self, args, timeout, merge):
        global peak, now
        with lock:
            now += 1; peak = max(peak, now)
        time.sleep(0.3)
        with lock:
            now -= 1
        return 0, ""
slow = L.limit_runner(Slow("rclone", "", lambda m: None))
ths = [threading.Thread(target=slow.capture, args=(["lsf", "a:"],)) for _ in range(10)]
[t.start() for t in ths]; [t.join() for t in ths]
assert peak == L.SHARE_COMMANDS == 3, peak
free = cloudsync.RcloneRunner("rclone", "", lambda m: None)
assert free.gate is None, "cloud sync isn't limited"
print(f"7. 10 commands at once: at most {peak} ran together; cloud sync isn't limited: ok")
# 8. the error text: no JSON listing in it, and "maximum number of connections" explained
shot = ('{"Path":"psp/cps1psp/Roms/willow.zip","Name":"willow.zip","Size":1773820,"IsDir":false},\n'
        '{"Path":"psp/cps1psp/Roms/wof.zip","Name":"wof.zip","Size":3927125,"IsDir":false}'
        '2026/11/26 21:58:15 ERROR : .library: error listing: couldn\'t initialize SMB: No more connections '
        'can be made to this remote computer at this time because the computer has already accepted the '
        'maximum number of connections.\n2026/11/26 21:58:35 NOTICE: Failed to lsjson with 2 errors')
msg = L._last_lines(shot)
assert "Path" not in msg and "Set-SmbShare" in msg and "Limit the number of simultaneous users" in msg, msg
other = '[\n{"Path":"a","Size":1},\n2026/11/26 ERROR : x: directory not found\n'
assert L._last_lines(other) == "2026/11/26 ERROR : x: directory not found", L._last_lines(other)
print("8. errors: JSON listing dropped; 'maximum number of connections' points at the share's user limit: ok")

# 9. a full share: the listing waits and tries again
class Busy:
    calls = 0
    def capture(self, args, **kw):
        Busy.calls += 1
        if Busy.calls < 3:
            return 1, "ERROR : couldn't initialize SMB: ... accepted the maximum number of connections."
        return 0, '[{"Path":"snes/a.sfc","Size":5,"ModTime":"2024-01-01T00:00:00Z","IsDir":false}]'
L.time.sleep = lambda s: None
got = L.list_library(Busy(), "romlibrary:Games")
assert Busy.calls == 3 and len(got) == 1, (Busy.calls, got)
class Down:
    calls = 0
    def capture(self, args, **kw):
        Down.calls += 1
        return 1, "ERROR : couldn't connect SMB: connection refused"
try:
    L.list_library(Down(), "romlibrary:Games"); assert False
except RuntimeError as ex:
    assert Down.calls == 1 and "connection refused" in str(ex), (Down.calls, ex)
print("9. a full share is retried twice; other errors fail at once: ok")
shutil.rmtree(T, ignore_errors=True)
print("test_smblimits OK")
