"""ROM Library transfer queue: check out, cancel and resume, check in (only when the library has the
game), add to library, space check, and the queue surviving a restart."""
import os, shutil, sys, time
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/romlib_queue"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
sys.path.insert(0, ROOT)
import cloudsync, romlibrary as L, libqueue as Q

def mk(path, size=10, data=None):
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if data is not None else os.urandom(size)); return p

lib, local = T / "library", T / "Emulation/roms"
mk(lib / "ps2/Okami (USA).iso", 3_000_000)
mk(lib / "psx/FF7 (USA).m3u", 30); mk(lib / "psx/FF7 (USA) (Disc 1).chd", 400_000); mk(lib / "psx/FF7 (USA) (Disc 2).chd", 400_000)
mk(lib / "ps3/Demon's Souls [BLUS30443]/PS3_GAME/USRDIR/EBOOT.BIN", 200_000)
mk(lib / "ps3/Demon's Souls [BLUS30443]/PS3_GAME/PARAM.SFO", 1000)
big = mk(lib / "ps2/Big Game (USA).iso", 40_000_000)
mk(lib / "neo geo/mslug.zip", 5000)
local.mkdir(parents=True)
mk(local / "ps2/My Homebrew.iso", 7000)

runner = cloudsync.RcloneRunner(shutil.which("rclone"), "", print)
lb = L.Library(runner, str(lib), str(local), machine="deck1", label="Steam Deck"); lb.refresh()
logs = []
q = Q.TransferQueue(lb, log=logs.append)

# 1. check out three games, including a folder game and a multi-disc game
jobs = q.add("checkout", ["ps2/Okami (USA).iso", "psx/FF7 (USA).m3u", "ps3/Demon's Souls [BLUS30443]", "neogeo/mslug.zip"])
assert len(jobs) == 4 and q.add("checkout", ["ps2/Okami (USA).iso"]) == []      # no duplicates
assert q.wait_idle(60), [(j.title, j.state) for j in q.jobs]
assert all(j.state == "done" for j in jobs), [(j.title, j.state, j.error) for j in jobs]
assert (local / "ps2/Okami (USA).iso").read_bytes() == (lib / "ps2/Okami (USA).iso").read_bytes()
assert (local / "psx/FF7 (USA) (Disc 2).chd").stat().st_size == 400_000
assert (local / "ps3/Demon's Souls [BLUS30443]/PS3_GAME/USRDIR/EBOOT.BIN").is_file()
assert (local / "neogeo/mslug.zip").is_file(), "new system folders use EmuDeck's name"
for k in ("ps2/Okami (USA).iso", "psx/FF7 (USA).m3u", "ps3/Demon's Souls [BLUS30443]"):
    assert lb.status(k)[0] == "here", (k, lb.status(k))
assert set(lb.manifest()) >= {"ps2/Okami (USA).iso", "psx/FF7 (USA).m3u"}
assert not any(v.get("incomplete") for v in lb.manifest().values())
print("1. checked out a disc image, a multi-disc game, a PS3 folder and a renamed system: ok")

# 2. cancel a big checkout part-way, then resume it after a 'restart'
r2 = cloudsync.RcloneRunner(shutil.which("rclone"), "", print)
q.lib.runner = r2
orig = Q.TransferQueue._copy
def slow_copy(self, job, src, dst, files, base_done=0):        # throttle so there is time to cancel
    self.lib.runner.exe_args = None
    import subprocess as sp
    real_popen = sp.Popen
    def popen(cmd, **kw):
        return real_popen(cmd + ["--bwlimit", "8M"], **kw)
    Q.subprocess.Popen = popen
    try:
        return orig(self, job, src, dst, files, base_done)
    finally:
        Q.subprocess.Popen = real_popen
Q.TransferQueue._copy = slow_copy
[jb] = q.add("checkout", ["ps2/Big Game (USA).iso"])
for _ in range(200):
    time.sleep(0.05)
    if jb.state == "running" and jb.done > 0: break
assert jb.done > 0 and jb.speed > 0, (jb.state, jb.done, jb.speed)
print(f"   progress while copying: {jb.percent}% at {L.human(jb.speed)}/s")
q.stop()                                                          # app closes mid-copy
assert jb.state == "queued"
lb.refresh_local()
assert lb.status("ps2/Big Game (USA).iso")[0] in ("partial", "not_here")
Q.TransferQueue._copy = orig
q2 = Q.TransferQueue(lb, log=logs.append)                         # app starts again
assert any(j.key == "ps2/Big Game (USA).iso" and j.note == "resumed" for j in q2.jobs)
assert q2.wait_idle(60)
[jb2] = [j for j in q2.jobs if j.key == "ps2/Big Game (USA).iso"]
assert jb2.state == "done", (jb2.state, jb2.error)
assert (local / "ps2/Big Game (USA).iso").read_bytes() == big.read_bytes()
print("2. a checkout interrupted by closing the app resumed on the next start: ok")

# 3. cancelling a queued job
q2.stop(); q3 = Q.TransferQueue(lb, log=logs.append, autostart=False)
[jc] = q3.add("checkout", ["neogeo/mslug.zip"]); q3.cancel(jc.id)
assert jc.state == "cancelled"; q3.clear_finished(); assert not q3.jobs
print("3. cancel a waiting job, clear finished: ok")

# 4. check in: removes local copy only when the library has it identical
q3.start()
[ci] = q3.add("checkin", ["psx/FF7 (USA).m3u"]); assert q3.wait_idle(30)
assert ci.state == "done", ci.error
assert not (local / "psx/FF7 (USA) (Disc 1).chd").exists() and (local / "psx").is_dir()
assert (lib / "psx/FF7 (USA) (Disc 1).chd").exists(), "the library copy is never touched"
assert lb.status("psx/FF7 (USA).m3u")[0] == "not_here" and "psx/FF7 (USA).m3u" not in lb.manifest()
[cd] = q3.add("checkin", ["ps3/Demon's Souls [BLUS30443]"]); assert q3.wait_idle(30) and cd.state == "done"
assert not (local / "ps3/Demon's Souls [BLUS30443]").exists(), "empty game folders are tidied"
print("4. check in removed the local copies, library untouched: ok")

# 5. refusals: a game only here, and a local copy that differs from the library's
[bad1] = q3.add("checkin", ["ps2/My Homebrew.iso"]); assert q3.wait_idle(30)
assert bad1.state == "failed" and "Add to library" in bad1.error and (local / "ps2/My Homebrew.iso").exists()
mk(local / "ps2/Okami (USA).iso", 3_000_001); lb.refresh_local()       # someone patched it
[bad2] = q3.add("checkin", ["ps2/Okami (USA).iso"]); assert q3.wait_idle(30)
assert bad2.state == "failed" and "differs" in bad2.error and (local / "ps2/Okami (USA).iso").exists()
print("5. refused to remove a game the library lacks, and one that differs:", bad2.error[:60], "…")

# 6. add to library, then it can be checked in
[ad] = q3.add("add", ["ps2/My Homebrew.iso"]); assert q3.wait_idle(30)
assert ad.state == "done", ad.error
assert (lib / "ps2/My Homebrew.iso").read_bytes() == (local / "ps2/My Homebrew.iso").read_bytes()
assert lb.status("ps2/My Homebrew.iso")[0] == "here"
[ci2] = q3.add("checkin", ["ps2/My Homebrew.iso"]); assert q3.wait_idle(30) and ci2.state == "done"
print("6. add to library, then check in: ok")

# 7. not enough space
real_space = L.disk_space
L.disk_space = lambda p: (100 * 1024**3, 99 * 1024**3, 1 * 1024**3)
[sp] = q3.add("checkout", ["psx/FF7 (USA).m3u"]); assert q3.wait_idle(30)
L.disk_space = real_space
assert sp.state == "failed" and "Not enough space" in sp.error, sp.error
print("7. space check:", sp.error)

# 8. an unfinished checkout can be removed even though its files are smaller than the library's
mk(local / "psx/FF7 (USA).m3u", 30, (lib / "psx/FF7 (USA).m3u").read_bytes())
mk(local / "psx/FF7 (USA) (Disc 1).chd", 1000)
m = lb.manifest(); m["psx/FF7 (USA).m3u"] = {"size": 800030, "incomplete": True}; lb._save_manifest(m)
lb.refresh_local(); assert lb.status("psx/FF7 (USA).m3u")[0] == "partial"
[rm] = q3.add("remove", ["psx/FF7 (USA).m3u"]); assert q3.wait_idle(30) and rm.state == "done", rm.error
assert not (local / "psx/FF7 (USA) (Disc 1).chd").exists()
q3.stop()
print("8. an unfinished checkout can be removed: ok")
print("ALL OK")
