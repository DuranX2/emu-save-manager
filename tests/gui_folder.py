import os, sys, shutil, time, tkinter as tk
from pathlib import Path
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))); sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import ui_ps2cards as U
from ps2mc import *
from ps2folder import *
from helpers_ps2 import mksave

root = Path("/tmp/guifolder"); shutil.rmtree(root, ignore_errors=True)
cards = root / "memcards"; cards.mkdir(parents=True); bk = root / "backups"
a = PS2Card.create_blank()
for i, g in enumerate(["Final Fantasy X", "Kingdom Hearts II", "Okami", "Persona 4"]):
    a.import_save(mksave(f"BASLUS-2{i*137:04d}", g, 40 + i*90))
a.save(cards / "Mcd001.ps2")
f = create_folder_card(cards / "Mcd002.ps2")
for i, g in enumerate(["Ratchet & Clank", "Jak II", "Burnout 3", "Shadow of the Colossus", "God of War II"]):
    f.import_save(mksave(f"BESCES-5{i*11:04d}", g, 120))
f.import_save(mksave("BASLUS-20000", "Final Fantasy X (older)", 40))
f.save(trash_dir=bk / "t")
(cards / "NotACard.ps2").mkdir()                      # folder without superblock: ignored

class FakeApp(tk.Tk):
    backup_dir = bk
    def resolve(self, emu): return {"Memory Cards": cards}
    def open_folder(self, p): print("open", p)
for n in ("askokcancel", "askyesno"): setattr(U.messagebox, n, lambda *a, **k: True)
U.messagebox.askyesnocancel = lambda *a, **k: True
U.messagebox.showerror = lambda t, m, **k: print("ERROR:", t, m)
U.messagebox.showinfo = lambda t, m, **k: print("INFO:", t, m)
U.simpledialog.askstring = lambda *a, **k: k.get("initialvalue")

app = FakeApp(); app.withdraw()
d = U.PS2CardDialog(app); d.update()
L, R = d.left, d.right
print("cards:", list(L.cards)); print("L", L.path.name, len(L.rows), "| R", R.path.name, len(R.rows))
print("R info:", R.info.cget("text"))
# file -> folder copy with replace
L.tree.selection_set(["BASLUS-20000", "BASLUS-20137"]); d.transfer(L, R, False); d.update(); print(d.status_bar.cget("text"))
assert open_card(cards/"Mcd002.ps2").get_save("BASLUS-20000").title() == "Final Fantasy X"
# folder -> file move
R.tree.selection_set(["BESCES-50000", "BESCES-50011"]); d.transfer(R, L, True); d.update(); print(d.status_bar.cget("text"))
assert "BESCES-50000" in L.rows and "BESCES-50000" not in R.rows and not (cards/"Mcd002.ps2"/"BESCES-50000").exists()
# delete on folder card
d.set_active(R); R.tree.selection_set(["BESCES-50022"]); d.delete_selected(); d.update(); print(d.status_bar.cget("text"))
# new folder card, move into it, duplicate, rename, delete
d.set_active(R); d.new_folder_card(); d.update(); print("new:", R.path.name, R.info.cget("text")[:40])
L.tree.selection_set(list(L.rows)); d.transfer(L, R, True); d.update(); print(d.status_bar.cget("text"))
d.set_active(R); d.duplicate_card(); d.update(); print("dup on left:", L.path.name, len(L.rows))
d.set_active(L); d.rename_card(); d.update(); print("renamed:", L.path.name)
d.delete_card(); d.update(); print(d.status_bar.cget("text"))
print("backup tree:"); os.system(f"cd {bk} && find . -maxdepth 3 -type d | sort")
for p in sorted(cards.iterdir()):
    try: c = open_card(p); print(p.name, type(c).__name__, sorted(c.save_names()))
    except CardError as e: print(p.name, "->", e)
# screenshot: file card left, folder card right
L.path = cards/"Mcd001.ps2"; R.path = cards/"Mcd002.ps2"; d.set_active(R); d.refresh_card_lists()
R.tree.selection_set(list(R.rows)[:2]); R.tree.focus_set()
d.status("Copied 2 save(s): Mcd001.ps2 → Mcd002.ps2  ·  replaced 1")
d.tk.call("wm","transient",d._w,""); d.deiconify(); d.geometry("1200x660+0+0")
for _ in range(20): d.update(); time.sleep(0.05)
os.system("import -window root -crop 1200x700+0+0 /tmp/ps2_card_manager_folders.png")
print("GUI FOLDER OK")
