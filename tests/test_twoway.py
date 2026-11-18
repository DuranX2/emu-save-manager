import json, os, shutil, subprocess, sys, time
from pathlib import Path
APP = __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))), "emu_save_manager.py")
T = Path("/tmp/twoway"); shutil.rmtree(T, ignore_errors=True)
cloud = T / "OneDrive"; cloud.mkdir(parents=True)
conf = T / "rclone.conf"; conf.write_text(f"[onedrive]\ntype = alias\nremote = {cloud}\n")

def device(name):
    home = T / name; mem = home / "memcards"; mem.mkdir(parents=True)
    st = home / "sstates"; st.mkdir()
    cfg = {"backup_dir": str(home / "backups"),
           "overrides": {"PCSX2::Memory Cards": str(mem), "PCSX2::Save States": str(st)},
           "cloud": {"rclone": shutil.which("rclone"), "config": str(conf), "remote": "onedrive",
                     "base": "EmuDeck/saves", "pairs": {}}}
    (home / ".emu_save_manager").mkdir()
    (home / ".emu_save_manager" / "config.json").write_text(json.dumps(cfg))
    return home, mem

def sync(home, *extra):
    env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"))
    r = subprocess.run([sys.executable, APP, "--sync", *extra], env=env, capture_output=True, text=True)
    return r.returncode, (home / ".emu_save_manager/logs/autosync.log").read_text() if (home / ".emu_save_manager/logs/autosync.log").exists() else ""

pc, pc_mem = device("pc"); deck, deck_mem = device("deck")
c = cloud / "EmuDeck/saves/pcsx2/saves"
# 1. PC has saves -> upload; Deck gets them
(pc_mem / "Mcd001.ps2").write_bytes(b"pc v1")
fc = pc_mem / "Folder.ps2" / "BASLUS-20001"; fc.mkdir(parents=True); (fc / "save.dat").write_bytes(b"game1 v1")
(pc_mem / "Folder.ps2" / "_pcsx2_superblock").write_bytes(b"sb")
rc, _ = sync(pc); assert rc == 0, rc
assert (c / "Mcd001.ps2").read_bytes() == b"pc v1" and (c / "Folder.ps2/BASLUS-20001/save.dat").exists()
rc, _ = sync(deck); assert rc == 0
assert (deck_mem / "Folder.ps2/BASLUS-20001/save.dat").read_bytes() == b"game1 v1"
print("1. PC -> cloud -> Deck: ok")
# 2. Deck plays: newer save + a new game; PC gets both, its old copy is backed up
time.sleep(1.2)
(deck_mem / "Folder.ps2/BASLUS-20001/save.dat").write_bytes(b"game1 v2 (deck)")
g2 = deck_mem / "Folder.ps2/BASLUS-20002"; g2.mkdir(); (g2 / "save.dat").write_bytes(b"game2 deck")
assert sync(deck)[0] == 0 and sync(pc)[0] == 0
assert (pc_mem / "Folder.ps2/BASLUS-20001/save.dat").read_bytes() == b"game1 v2 (deck)"
assert (pc_mem / "Folder.ps2/BASLUS-20002/save.dat").exists()
bk = list((pc / "backups/cloud_overwritten").rglob("save.dat"))
assert bk and bk[0].read_bytes() == b"game1 v1", bk
print("2. Deck -> cloud -> PC: ok; PC's older copy kept in", bk[0].relative_to(pc))
# 3. both sides edit the same file between syncs: a CONFLICT - nothing is overwritten either way
#    until a side is chosen; then the chosen copy wins and the other is kept in the backups
time.sleep(1.2); (deck_mem / "Mcd001.ps2").write_bytes(b"deck edit")
time.sleep(1.2); (pc_mem / "Mcd001.ps2").write_bytes(b"pc edit")
assert sync(deck)[0] == 0                      # only the Deck changed vs. the cloud: it uploads
assert (c / "Mcd001.ps2").read_bytes() == b"deck edit"
rc, log = sync(pc)                             # the PC changed too: conflict, nothing copied
assert rc == 0 and "CONFLICT Mcd001.ps2" in log and "conflict(s) need your choice" in log, log[-800:]
assert (c / "Mcd001.ps2").read_bytes() == b"deck edit" and (pc_mem / "Mcd001.ps2").read_bytes() == b"pc edit"
st_file = [f for f in (pc / ".emu_save_manager/sync_state").glob("*.json")
           if "Mcd001.ps2" in json.loads(f.read_text()).get("conflicts", {})][0]
status = json.loads((pc / ".emu_save_manager/autosync_status.json").read_text())
assert status["conflicts"] == 1 and "conflict" in status["message"], status
st = json.loads(st_file.read_text()); st["resolve"]["Mcd001.ps2"] = "local"; st_file.write_text(json.dumps(st))
assert sync(pc)[0] == 0                        # "keep this device's"
assert (c / "Mcd001.ps2").read_bytes() == b"pc edit"
kept = list((cloud / "EmuDeck/EmuSaveManager-overwritten").rglob("Mcd001.ps2"))
assert any(k.read_bytes() == b"deck edit" for k in kept), kept
assert not json.loads(st_file.read_text())["conflicts"]
assert sync(deck)[0] == 0 and (deck_mem / "Mcd001.ps2").read_bytes() == b"pc edit"
assert any(b.read_bytes() == b"deck edit" for b in (deck / "backups/cloud_overwritten").rglob("Mcd001.ps2"))
print("3. both edited: conflict, nothing overwritten; 'keep this device's' applied, other copy kept:",
      kept[0].relative_to(cloud))
# 4. emulator running -> skipped (exit 3), nothing touched
fake = T / "pcsx2-qt"; shutil.copy("/bin/sleep", fake)
p = subprocess.Popen([str(fake), "30"])
try:
    time.sleep(0.3)
    rc, log = sync(pc); assert rc == 3, rc
    print("4. skipped while emulator runs:", log.strip().splitlines()[-1].split("  ", 1)[1])
finally:
    p.kill()
# 5. another sync holding the lock -> skipped; stale lock is cleared
lock = pc / ".emu_save_manager/autosync.lock"; lock.write_text("1 0")
assert sync(pc)[0] == 3
os.utime(lock, (time.time() - 4 * 3600,) * 2); assert sync(pc)[0] == 0 and not lock.exists()
print("5. lock: ok")
# 6. not set up -> exit 2; dry run changes nothing
st = json.loads((pc / ".emu_save_manager/autosync_status.json").read_text()); print("   status file:", st)
(pc_mem / "new.ps2").write_bytes(b"x"); rc, _ = sync(pc, "--dry-run"); assert rc == 0 and not (c / "new.ps2").exists()
print("6. dry run: ok")
print("ALL TWO-WAY TESTS OK")
