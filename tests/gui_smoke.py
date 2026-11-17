import os, sys, shutil, tkinter as tk
from pathlib import Path
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))); sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import ui_ps2cards as U
from ps2mc import *
from helpers_ps2 import mksave

root = Path("/tmp/guitest"); shutil.rmtree(root, ignore_errors=True)
cards = root / "memcards"; cards.mkdir(parents=True); bk = root / "backups"
games = ["Final Fantasy X", "Kingdom Hearts II", "Shadow of the Colossus", "Okami", "Persona 4",
         "God of War II", "Metal Gear Solid 3", "Ratchet & Clank", "Jak II", "Burnout 3"]
a = PS2Card.create_blank()
for i, g in enumerate(games[:7]):
    a.import_save(mksave(f"BASLUS-2{i*137:04d}", g, 40 + i*90))
a.save(cards / "Mcd001.ps2")
b = PS2Card.create_blank()
for i, g in enumerate(games[7:]):
    b.import_save(mksave(f"BESCES-5{i*11:04d}", g, 120))
b.import_save(mksave("BASLUS-20000", "Final Fantasy X (older)", 40))
b.save(cards / "Mcd002.ps2")

class FakeApp(tk.Tk):
    backup_dir = bk
    def resolve(self, emu): return {"Memory Cards": cards}
    def open_folder(self, p): print("open", p)

answers = {"askyesnocancel": True}
for n in ("askokcancel", "askyesno"):
    setattr(U.messagebox, n, lambda *a, **k: True)
U.messagebox.askyesnocancel = lambda *a, **k: answers["askyesnocancel"]
U.messagebox.showerror = lambda t, m, **k: print("ERROR:", t, m)
U.messagebox.showinfo = lambda t, m, **k: print("INFO:", t, m)
U.simpledialog.askstring = lambda *a, **k: k.get("initialvalue")

app = FakeApp(); app.withdraw()
d = U.PS2CardDialog(app)
d.update()
L, R = d.left, d.right
print("left", L.path.name, list(L.rows)); print("right", R.path.name, list(R.rows))

# copy 2 saves L->R, one conflicts (replace)
L.tree.selection_set(["BASLUS-20000", "BASLUS-20137"]); d.update()
d.transfer(L, R, False); d.update(); print(d.status_bar.cget("text"))
assert {"BASLUS-20000", "BASLUS-20137"} <= set(R.rows) and "BASLUS-20000" in L.rows
assert PS2Card.load(cards/"Mcd002.ps2").get_save("BASLUS-20000").title() == "Final Fantasy X"
# move 2 saves R->L
R.tree.selection_set(["BESCES-50000", "BESCES-50011"]); d.update()
d.transfer(R, L, True); d.update(); print(d.status_bar.cget("text"))
assert "BESCES-50000" in L.rows and "BESCES-50000" not in R.rows
# conflict skip
answers["askyesnocancel"] = False
L.tree.selection_set(["BASLUS-20000"]); d.transfer(L, R, True); d.update(); print(d.status_bar.cget("text"))
assert "BASLUS-20000" in L.rows
# same card both sides
R.path = L.path; R.reload(); d.update_buttons(); print("arrows:", d.arrows["copy_r"].state())
d.transfer(L, R, False)
R.path = cards/"Mcd002.ps2"; d.refresh_card_lists()
# new card, delete saves, duplicate, rename
d.set_active(R); d.new_card(); d.update(); print("new:", R.path.name, len(R.rows))
L.tree.selection_set(list(L.rows)[:6]); d.update(); d.transfer(L, R, True); d.update(); print(d.status_bar.cget("text"))
d.set_active(L); L.tree.selection_set([list(L.rows)[0]]); d.delete_selected(); d.update(); print(d.status_bar.cget("text"))
print("safety copies:", sorted(p.name for p in (bk/"_ps2_card_safety").iterdir()))
for p in sorted(cards.iterdir()):
    c = PS2Card.load(p); print(p.name, len(c.list_saves()), c.free_clusters())
# final state for screenshot
d.set_active(L)
L.path = cards/"Mcd001.ps2"; R.path = cards/"Mcd002.ps2"; d.refresh_card_lists()
L.tree.selection_set(list(L.rows)[1:3]); L.tree.focus_set(); d.update()
d.status("Copied 2 save(s): Mcd001.ps2 → Mcd002.ps2  ·  replaced 1")
d.tk.call("wm","transient",d._w,""); d.deiconify(); d.geometry("1200x660+0+0")
import time
for _ in range(20): d.update(); time.sleep(0.05)
os.system("import -window root -crop 1200x700+0+0 /tmp/guitest/shot.png")
print("SMOKE OK")
