import os, sys, shutil, subprocess, threading, time
from pathlib import Path
T = Path("/tmp/ewtest"); shutil.rmtree(T, ignore_errors=True); T.mkdir()
os.environ["HOME"] = str(T)
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import autosync, exitwatch as ew
E = lambda *n: set(n)
# 1. decision logic with a fake clock
on = {"emulators": True, "frontend": True}
w = ew.Watcher({"emulators": E("pcsx2-qt"), "frontends": E("es-de")}, delay=10)
assert not w.step({"emulators": E(), "frontends": E("es-de")}, on, 0)      # PCSX2 closed -> wait
assert not w.step({"emulators": E(), "frontends": E("es-de")}, on, 5)
assert w.step({"emulators": E(), "frontends": E("es-de")}, on, 10) and w.reason == ["PCSX2"]
w.reason = []
assert not w.step({"emulators": E(), "frontends": E("es-de")}, on, 30)     # nothing new closed
# another game started before the delay ran out -> no sync until it closes too
w = ew.Watcher({"emulators": E("pcsx2-qt"), "frontends": E()}, delay=10)
w.step({"emulators": E(), "frontends": E()}, on, 0)
assert not w.step({"emulators": E("duckstation-qt"), "frontends": E()}, on, 11)
assert not w.step({"emulators": E("duckstation-qt"), "frontends": E()}, on, 40)
assert not w.step({"emulators": E(), "frontends": E()}, on, 41)
assert w.step({"emulators": E(), "frontends": E()}, on, 51) and w.reason == ["PCSX2", "DuckStation"]
# ES-DE only: an emulator closing inside ES-DE is ignored; quitting ES-DE syncs
fe = {"emulators": False, "frontend": True}
w = ew.Watcher({"emulators": E("retroarch"), "frontends": E("es-de")}, delay=10)
assert not w.step({"emulators": E(), "frontends": E("es-de")}, fe, 0) and w.due is None
w.step({"emulators": E(), "frontends": E()}, fe, 100)
assert w.step({"emulators": E(), "frontends": E()}, fe, 110) and w.reason == ["ES-DE"]
# ES-DE only, an emulator still running at due time -> retried later, not dropped
w = ew.Watcher({"emulators": E("xemu"), "frontends": E("es-de")}, delay=10)
w.step({"emulators": E("xemu"), "frontends": E()}, fe, 0)
assert not w.step({"emulators": E("xemu"), "frontends": E()}, fe, 10) and w.due == 20
assert w.step({"emulators": E(), "frontends": E()}, fe, 20)
print("logic ok")
# 2. systemd unit
u = ew.systemd_unit(["/home/deck/.local/share/EmuSaveManager/EmuSaveManager", "--watch"])
assert "ExecStart=/home/deck/.local/share/EmuSaveManager/EmuSaveManager --watch" in u and "WantedBy=default.target" in u and "Restart=on-failure" in u
assert ew.watch_command()[-1] == "--watch"
# 3. the real loop against real processes named like PCSX2 and ES-DE
es = subprocess.Popen(["/tmp/ewbin/es-de", "300"]); emu = subprocess.Popen(["/tmp/ewbin/pcsx2-qt", "300"])
time.sleep(0.3)
snap = ew.snapshot(); print("snapshot:", snap)
assert snap == {"emulators": {"pcsx2-qt"}, "frontends": {"es-de"}}
cfg = {"cloud": {"exit_sync": {"emulators": True, "frontend": True}}}
synced = []
autosync.load_app = lambda: (cfg, {}, lambda e: {})
autosync.sync_from_config = lambda c, p, r, log: (synced.append(time.time()), log("(sync ran)"), 0)[2]
lines = []
th = threading.Thread(target=lambda: ew.watch(log=lines.append, poll=0.2, delay=0.6), daemon=True); th.start()
time.sleep(0.6)
assert ew.watcher_running() and ew.watch(log=lines.append) == 0      # a second copy exits at once
emu.terminate(); emu.wait(); time.sleep(1.6)
assert len(synced) == 1, (synced, lines)
es.terminate(); es.wait(); time.sleep(1.6)
assert len(synced) == 2, lines
cfg["cloud"]["exit_sync"] = {"emulators": False, "frontend": False}
th.join(3); assert not th.is_alive()
print("\n".join(lines))
assert lines[0].startswith("Sync-on-exit watcher started") and "== Sync after PCSX2 closed ==" in lines and "== Sync after ES-DE closed ==" in lines and lines[-1].startswith("Sync-on-exit watcher stopped")
assert not ew.watcher_running()
print("EXITWATCH OK")
