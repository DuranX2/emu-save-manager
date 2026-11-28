"""Smart sync (syncplan): the side that changed wins (even with an older date), folder-card saves
move as a whole, both-changed is a conflict until a side is chosen, a card that's a file on one
side and a folder on the other is skipped with one message, empty files never replace real ones (the real copy repairs them)."""
import json, os, shutil, sys, time
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/syncplan"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
sys.path.insert(0, ROOT)
import cloudsync as cs, syncplan as sp
if not shutil.which("rclone"):
    print("rclone not on PATH: skipped"); print("SYNCPLAN OK (skipped)"); sys.exit(0)

cloud = T / "OneDrive"; cloud.mkdir()
conf = T / "rclone.conf"; conf.write_text(f"[onedrive]\ntype = alias\nremote = {cloud}\n")
logs = []
runner = cs.RcloneRunner(shutil.which("rclone"), str(conf), logs.append)
RP = "onedrive:EmuDeck/saves/pcsx2/saves"
C = cloud / "EmuDeck/saves/pcsx2/saves"
pc, deck = T / "pc", T / "deck"
stamp = [0]
def sync(dev, mode="both"):
    stamp[0] += 1; logs.clear()
    r = sp.sync_job(runner, {"emu": "PCSX2", "label": "Memory Cards", "local": dev}, RP, mode, False,
                    T / f"bk_{dev.name}" / str(stamp[0]), f"onedrive:EmuDeck/EmuSaveManager-overwritten/{dev.name}/{stamp[0]}",
                    logs.append)
    assert r["ok"], logs
    return r
def put(p, data, age=0.0):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
    t = time.time() - age; os.utime(p, (t, t))
def read(p): return p.read_bytes()

MF = "Mem Folder 1.ps2"
put(pc / MF / "_pcsx2_superblock", b"SB", 999)
put(pc / MF / "BASCUS-97399GodOfWar/icon.sys", b"PS2D gow", 900)
put(pc / MF / "BASCUS-97399GodOfWar/data0.bin", b"gow ch1", 900)
put(pc / MF / "BASLUS-20845ColdWntr/save", b"cold v1", 800)
put(pc / "Mcd001.ps2", b"card v1", 700)
sync(pc); sync(deck)
assert read(deck / MF / "BASCUS-97399GodOfWar/data0.bin") == b"gow ch1"
print("1. first sync PC -> cloud -> Deck: ok")

# 2. the Deck plays God of War but its clock/date is OLDER than the PC's copy: the changed side wins
put(deck / MF / "BASCUS-97399GodOfWar/data0.bin", b"gow ch2 (deck)", 2000)     # dated older
put(deck / MF / "BASCUS-97399GodOfWar/data1.bin", b"gow slot2", 2000)
sync(deck)
assert read(C / MF / "BASCUS-97399GodOfWar/data0.bin") == b"gow ch2 (deck)"
sync(pc)
assert read(pc / MF / "BASCUS-97399GodOfWar/data0.bin") == b"gow ch2 (deck)", "older-dated change lost"
assert read(pc / MF / "BASCUS-97399GodOfWar/data1.bin") == b"gow slot2"
print("2. a change wins even when its date is older than the other copy: ok")

# 3. both devices played Cold Winter since the last sync: conflict, nothing copied either way
put(pc / MF / "BASLUS-20845ColdWntr/save", b"cold pc", 0)
put(pc / MF / "BASLUS-20845ColdWntr/pc_only.dat", b"pc extra", 0)
put(deck / MF / "BASLUS-20845ColdWntr/save", b"cold deck", 0)
sync(deck)                                                     # only the deck changed vs cloud: uploads
r = sync(pc)
assert r["conflicts"] == 1 and any(f"CONFLICT {MF}/BASLUS-20845ColdWntr" in l for l in logs), logs
assert read(pc / MF / "BASLUS-20845ColdWntr/save") == b"cold pc"
assert read(C / MF / "BASLUS-20845ColdWntr/save") == b"cold deck"
assert not (C / MF / "BASLUS-20845ColdWntr/pc_only.dat").exists()   # nothing of the save moved
conf_list = sp.all_conflicts()
assert len(conf_list) == 1 and conf_list[0][2] == f"{MF}/BASLUS-20845ColdWntr" and sp.conflict_count() == 1
print("3. both played the same game: conflict, the whole save left alone on both sides: ok")
# still a conflict on the next sync until someone chooses
assert sync(pc)["conflicts"] == 1

# 4. choose "keep the cloud's": the PC's save becomes exactly the cloud's; its version is backed up
f, st, it, info = conf_list[0]
sp.choose(f, it, "cloud")
assert sp.conflict_count() == 0
sync(pc)
assert read(pc / MF / "BASLUS-20845ColdWntr/save") == b"cold deck"
assert not (pc / MF / "BASLUS-20845ColdWntr/pc_only.dat").exists(), "leftover of the losing copy stayed"
bk = {p.name: p.read_bytes() for p in (T / "bk_pc").rglob("*") if p.is_file() and "ColdWntr" in str(p)}
assert bk.get("save") == b"cold pc" and bk.get("pc_only.dat") == b"pc extra", bk
assert not sp.all_conflicts()
print("4. 'keep the cloud's': save replaced whole, PC's copy (incl. its extra file) in the backup: ok")

# 5. the game deleted a file inside its save on the Deck: the PC's copy follows (file goes to backup)
os.remove(deck / MF / "BASCUS-97399GodOfWar/data1.bin")
put(deck / MF / "BASCUS-97399GodOfWar/data0.bin", b"gow ch3", 0)
sync(deck); sync(pc)
assert read(pc / MF / "BASCUS-97399GodOfWar/data0.bin") == b"gow ch3"
assert not (pc / MF / "BASCUS-97399GodOfWar/data1.bin").exists()
print("5. a file the game removed inside a save is removed on the other device too (kept in backup): ok")

# 6. outside folder cards nothing is ever deleted
os.remove(deck / "Mcd001.ps2"); sync(deck); sync(pc)
assert (pc / "Mcd001.ps2").exists() and (deck / "Mcd001.ps2").exists()      # came back to the Deck
print("6. a missing plain file is restored, never deleted elsewhere: ok")

# 7. this morning's problem: the card is a FILE on the Deck but a folder in the cloud
shutil.rmtree(deck / MF); put(deck / MF, b"\0" * 1024, 0)
put(pc / "Mcd002.ps2", b"card2", 0); sync(pc)
r = sync(deck)
assert r["mismatch"] == 1 and sum("Skipped Mem Folder 1.ps2" in l for l in logs) == 1, logs
assert not any("not a directory" in l for l in logs) and read(deck / "Mcd002.ps2") == b"card2"
assert read(deck / MF) == b"\0" * 1024
print("7. file card vs folder card: one clear message, skipped, everything else synced: ok")
os.remove(deck / MF); sync(deck)
assert read(deck / MF / "BASCUS-97399GodOfWar/data0.bin") == b"gow ch3"

# 8. empty files never replace real ones; the real copy repairs the empty side
put(deck / "Mcd002.ps2", b"", 0); sync(deck)
assert read(C / "Mcd002.ps2") == b"card2" and any("Protected 1 file" in l for l in logs)
assert read(deck / "Mcd002.ps2") == b"card2", "the empty copy on this device was repaired from the cloud"
put(C / "Mcd002.ps2", b"", 0); sync(deck)                 # the cloud copy emptied (newer): EmuDeck's old rclone
assert read(deck / "Mcd002.ps2") == b"card2" and read(C / "Mcd002.ps2") == b"card2", "the cloud copy was repaired"
kept = list((cloud / "EmuDeck/EmuSaveManager-overwritten/deck").rglob("Mcd002.ps2"))
assert kept and all(k.stat().st_size == 0 for k in kept), "the empty cloud copy went to the backup"
sync(deck); assert any("0 file(s) to download, 0 to upload" in l for l in logs), logs
put(deck / "Mcd002.ps2", b"card2 v2", 0); put(C / "Mcd002.ps2", b"", 0); sync(deck)   # both changed, cloud empty
assert read(C / "Mcd002.ps2") == b"card2 v2" and not any("CONFLICT" in l for l in logs), logs
print("8. an empty file never replaces a real one, and the real copy repairs the empty side: ok")

# 9. download-only and upload-only modes
put(pc / "Mcd003.ps2", b"pc only", 0); put(C / "Mcd004.ps2", b"cloud only", 0)
sync(pc, "download"); assert (pc / "Mcd004.ps2").exists() and not (C / "Mcd003.ps2").exists()
sync(pc, "upload"); assert (C / "Mcd003.ps2").exists()
print("9. download-only / upload-only: ok")
print("SYNCPLAN OK")
