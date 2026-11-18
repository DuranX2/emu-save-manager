"""Conflicts in the Cloud Sync window: a save changed on both sides shows an orange banner and the
Conflicts (1) button; the dialog lists it by game name; 'Keep this device's' + apply wins it."""
import os, shutil, sys, time, tkinter as tk
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/conflictsgui"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
sys.path.insert(0, ROOT)
import syncplan, ui_cloud as U

mem = T / "pc/memcards"; save = mem / "Mem Folder 1.ps2/BASCUS-97399GodOfWar"; save.mkdir(parents=True)
(mem / "Mem Folder 1.ps2/_pcsx2_superblock").write_bytes(b"SB")
icon = bytearray(964); icon[:4] = b"PS2D"; icon[0xC0:0xC0 + 22] = "ＧＯＤ　ＯＦ　ＷＡＲ".encode("shift_jis")
(save / "icon.sys").write_bytes(bytes(icon)); (save / "data0.bin").write_bytes(b"ch1")
states = T / "pc/states"; states.mkdir()
cloud = T / "OneDrive"; conf = T / "rclone.conf"; conf.write_text(f"[onedrive]\ntype = alias\nremote = {cloud}\n")

class FakeApp(tk.Tk):
    backup_dir = T / "backups"
    cfg = {"cloud": {"rclone": shutil.which("rclone"), "config": str(conf), "remote": "onedrive",
                     "base": "EmuDeck/saves"}}
    profiles = {"PCSX2": {"locations": [{"label": "Memory Cards"}, {"label": "Save States"}]}}
    def resolve(self, emu): return {"Memory Cards": mem, "Save States": states}
    def save_cfg(self): pass
    def refresh_all(self): pass
    def open_folder(self, p): pass

U.messagebox.askokcancel = lambda *a, **k: True
app = FakeApp(); app.withdraw()
d = U.CloudSyncDialog(app)
def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        d.update(); time.sleep(0.05)
def run(mode="both"):
    d.start(mode)
    for _ in range(150):
        pump(0.1)
        if not d.busy: return
pump(0.5); run()
assert d.banner_kind == "ok" and str(d.conflicts_btn.cget("state")) == "disabled"
# the Deck played God of War (uploaded), and the PC played it too: conflict
cs_ = cloud / "EmuDeck/saves/pcsx2/saves/Mem Folder 1.ps2/BASCUS-97399GodOfWar/data0.bin"
time.sleep(1.2); cs_.write_bytes(b"ch2 deck")
time.sleep(1.2); (save / "data0.bin").write_bytes(b"ch2 pc")
run()
assert d.banner_kind == "warn" and "1 conflict(s)" in d.banner.cget("text"), d.banner.cget("text")
assert d.conflicts_btn.cget("text") == "Conflicts (1)"
assert "CONFLICT Mem Folder 1.ps2/BASCUS-97399GodOfWar" in d.log.get("1.0", "end")
assert cs_.read_bytes() == b"ch2 deck" and (save / "data0.bin").read_bytes() == b"ch2 pc"
print("1. conflict: orange banner ->", d.banner.cget("text"))

d.show_conflicts(); pump(0.5)
dlg = [w for w in d.winfo_children() if isinstance(w, U.ConflictsDialog)][0]
rows = dlg.tree.get_children(); assert len(rows) == 1
vals = dlg.tree.item(rows[0], "values")
assert vals[0] == "PCSX2 / Memory Cards" and "GOD OF WAR" in vals[1] and vals[4] == "?", vals
print("2. Conflicts window lists:", vals[1], "|", vals[2], "|", vals[3])
dlg.tree.selection_set(rows[0]); dlg._choose("local"); pump(0.2)
assert dlg.tree.item(dlg.tree.get_children()[0], "values")[4] == "this device's"
assert d.conflicts_btn.cget("text") == "Conflicts"            # nothing left undecided
dlg.tk.call("wm", "transient", dlg._w, ""); dlg.deiconify(); dlg.geometry("900x300+0+0"); pump(0.8)
os.system("import -window root -crop 900x300+0+0 /tmp/conflicts_dialog.png 2>/dev/null")
dlg._apply()
for _ in range(150):
    pump(0.1)
    if not d.busy: break
assert cs_.read_bytes() == b"ch2 pc", "the chosen copy didn't win"
assert d.banner_kind == "ok", d.banner.cget("text")
assert not syncplan.all_conflicts()
kept = list((cloud / "EmuDeck/EmuSaveManager-overwritten").rglob("data0.bin"))
assert any(k.read_bytes() == b"ch2 deck" for k in kept)
print("3. 'Keep this device's' + apply: PC copy in the cloud, the Deck's kept in the cloud backup: ok")
print("GUI CONFLICTS OK")
