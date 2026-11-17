"""Save History window: lists files and versions, restores a version and a whole folder,
takes a manual snapshot, saves settings, and the main window opens it."""
import os, shutil, sys, time, tkinter as tk
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
T = Path("/tmp/histgui"); shutil.rmtree(T, ignore_errors=True); T.mkdir()
os.environ["HOME"] = str(T / "home")                     # SyncLock and config live under HOME
import autosync, savehistory as sh, ui_history as U
autosync.LOCK_FILE = T / "home/.emu_save_manager/autosync.lock"

mem = T / "memcards"; (mem / "Folder.ps2/SLUS-1").mkdir(parents=True)
(mem / "Mcd001.ps2").write_bytes(b"card v1"); (mem / "Folder.ps2/SLUS-1/save.dat").write_bytes(b"g v1")
states = T / "states"; states.mkdir()

class FakeApp(tk.Tk):
    backup_dir = T / "backups"
    cfg = {"history": {}}
    profiles = {"PCSX2": {"locations": [{"label": "Memory Cards"}, {"label": "Save States"}]},
                "DuckStation": {"locations": [{"label": "Memory Cards"}]}}
    saved = 0
    def resolve(self, emu):
        return {"Memory Cards": mem, "Save States": states} if emu == "PCSX2" else {"Memory Cards": None}
    def save_cfg(self): FakeApp.saved += 1
    def refresh_all(self): pass

asked = []
U.messagebox.askyesno = lambda t, m, **k: asked.append(m) or True
U.messagebox.showinfo = lambda t, m, **k: print("INFO:", m)
U.messagebox.showwarning = lambda t, m, **k: print("WARN:", m)
U.messagebox.showerror = lambda t, m, **k: (_ for _ in ()).throw(AssertionError(m))
U.autosync.running_emulators = lambda: []

# history as two syncs would have left it
h = sh.SaveHistory(sh.history_dir(FakeApp.backup_dir))
K = "PCSX2::Memory Cards"; t0 = time.time() - 3600
h.snapshot(K, mem, "local", t0)
time.sleep(0.02)
(mem / "Mcd001.ps2").write_bytes(b"card v2 from deck"); (mem / "Folder.ps2/SLUS-1/save.dat").write_bytes(b"g v2")
h.snapshot(K, mem, "cloud", t0 + 600); h.save()

app = FakeApp(); app.withdraw()
d = U.SaveHistoryDialog(app, "PCSX2")
def pump(s=0.3):
    end = time.time() + s
    while time.time() < end:
        d.update(); time.sleep(0.03)
pump()
assert d.v_loc.get() == "PCSX2 / Memory Cards", d.combo["values"]
assert "DuckStation / Memory Cards" not in d.combo["values"]          # no folder, no history
rows = {d.files.set(i, "file"): i for i in d.files.get_children()}
assert set(rows) == {"Mcd001.ps2", "Folder.ps2/SLUS-1/save.dat"}, rows
d.files.selection_set(rows["Mcd001.ps2"]); pump()
vers = d.vers.get_children()
assert len(vers) == 2 and "= the file now" in d.vers.set(vers[0], "what")
assert "this device" in d.vers.set(vers[1], "what")
print("1. files and versions listed; current version marked: ok")

# restore the older version of one file
d.vers.selection_set(vers[1]); d.restore_version(); pump()
assert (mem / "Mcd001.ps2").read_bytes() == b"card v1" and "Mcd001.ps2" in asked[-1]
d.files.selection_set(rows["Mcd001.ps2"]); pump()
top = d.vers.get_children()[0]
assert "Restored" in d.vers.set(top, "what") and "= the file now" in d.vers.set(top, "what")
print("2. restore one version: ok ->", d.status.cget("text"))

# whole folder back to the first state
assert len(d.point_rows) >= 2
d.points.current(d.point_rows.index(t0)); d.restore_folder(); pump()
assert (mem / "Folder.ps2/SLUS-1/save.dat").read_bytes() == b"g v1"
print("3. restore the whole folder to a point in time: ok")

# refuses while an emulator runs or a sync holds the lock
U.autosync.running_emulators = lambda: ["pcsx2-qt"]
(mem / "Folder.ps2/SLUS-1/save.dat").write_bytes(b"g v3"); d.restore_folder()
assert (mem / "Folder.ps2/SLUS-1/save.dat").read_bytes() == b"g v3"
U.autosync.running_emulators = lambda: []
lock = autosync.SyncLock(); assert lock.acquire(); d.restore_folder(); lock.release()
assert (mem / "Folder.ps2/SLUS-1/save.dat").read_bytes() == b"g v3"
print("4. refuses while an emulator runs or a sync is running: ok")

# manual snapshot picks up g v3
d.snapshot_now(); pump()
assert "1 changed" in d.status.cget("text"), d.status.cget("text")
print("5. take snapshot now: ok")

# settings
d.v_keep.set("5"); d.v_max.set("1.5"); d.save_settings()
assert app.cfg["history"] == {"enabled": True, "keep": 5, "max_mb": 1536} and FakeApp.saved
print("6. settings saved:", app.cfg["history"])

d.tk.call("wm", "transient", d._w, ""); d.deiconify(); d.geometry("1040x700+0+0")
d.files.selection_set(d.files.get_children()[1]); pump(1)
os.system("import -window root -crop 1040x700+0+0 /tmp/save_history.png 2>/dev/null")
d.destroy()

# main window has the button and opens the window for the current emulator
import emu_save_manager as esm
esm.CONFIG_DIR = T / "home/.emu_save_manager"; esm.CONFIG_FILE = esm.CONFIG_DIR / "config.json"
main = esm.App(); main.withdraw()
btn = [w for w in main.winfo_children()[0].winfo_children() if isinstance(w, esm.ttk.Button)
       and w.cget("text") == "Save History…"]
assert btn
btn[0].invoke(); main.update()
wins = [w for w in main.winfo_children() if isinstance(w, U.SaveHistoryDialog)]
assert len(wins) == 1
print("7. main window button opens Save History: ok")
print("GUI HISTORY OK")
