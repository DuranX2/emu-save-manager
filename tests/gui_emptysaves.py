"""Empty (0-byte) saves - what the 2026-11-17 sync left behind (Skies of Arcadia Legends on the Deck).
  * emptysaves.scan finds them in every save folder (not sync markers), with the copies a sync kept
    in cloud_overwritten and the versions in Save History, newest first;
  * restoring puts the copy back with today's date, keeps the empty file in empty_saves, and leaves
    a file alone that has data again;
  * the Empty saves window lists them and restores one / all;
  * the GameCube manager shows an empty .gci on the card, marked, refuses to copy or export it,
    and offers 'Put back a kept copy…'.
Run with xvfb-run -a."""
import faulthandler, os, shutil, sys, time, tkinter as tk
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/emptysaves"); shutil.rmtree(T, ignore_errors=True)
os.environ["HOME"] = str(T / "home"); (T / "home").mkdir(parents=True)
faulthandler.dump_traceback_later(120, exit=True)
import savefixtures as F
import emptysaves as es, gcfolder, savehistory as sh
import ui_emptysaves as UE, ui_gccards as UG

checks = 0


def ok(c, what):
    global checks
    assert c, what
    checks += 1


gc, bk, ps2 = T / "saves/dolphin/GC", T / "backups", T / "saves/pcsx2/saves"
card = gc / "EUR/Card A"
SKIES = "8P-GEAP-SKIES_LEGENDS.gci"
F.gci_file(card / "01-GZLP-gczelda.gci", code=b"GZLP")
(card / SKIES).write_bytes(b"")
os.utime(card / SKIES, (time.time() - 86400 * 11, time.time() - 86400 * 11))
(gc / ".hash").write_bytes(b"")                                # a sync marker, not a save
ps2.mkdir(parents=True); (ps2 / "Mcd001.ps2").write_bytes(b"")  # no copy anywhere
# what the 11-17 sync kept, and an older one
good_h, good_d = F.gci(code=b"GEAP", maker=b"8P", fname=b"SKIES_LEGENDS", title="Skies of Arcadia",
                       detail="35:12 Valua", blocks=6)
older_h, older_d = F.gci(code=b"GEAP", maker=b"8P", fname=b"SKIES_LEGENDS", title="Skies of Arcadia",
                         detail="02:10 Pirate Isle", blocks=6)
for stamp, data in (("2026-11-17_09-30-00", good_h + good_d), ("2022-05-01_12-00-00", older_h + older_d)):
    p = bk / "cloud_overwritten" / stamp / "Dolphin/GameCube Saves/EUR/Card A" / SKIES
    p.parent.mkdir(parents=True); p.write_bytes(data)
empty_stamp = bk / "cloud_overwritten/2026-11-20_10-00-00/Dolphin/GameCube Saves/EUR/Card A" / SKIES
empty_stamp.parent.mkdir(parents=True); empty_stamp.write_bytes(b"")       # empty copies don't count
locs = [("Dolphin", "GameCube Saves", gc, ()), ("PCSX2", "Memory Cards", ps2, ()),
        ("PrimeHack", "GameCube Saves", gc, ())]                              # same folder twice

# 1. scan
found = es.scan(locs, bk)
ok([f.rel for f in found] == [f"EUR/Card A/{SKIES}", "Mcd001.ps2"], [f.rel for f in found])
sk = found[0]
ok(len(sk.copies) == 2 and sk.best.kind == "overwritten" and "2026-11-17" in sk.best.describe(), sk.copies)
ok(sk.best.size == len(good_h + good_d), "the newest non-empty copy is the best")
ok(not found[1].copies, "no copy for the PS2 card")
print("1. empty saves found with their kept copies (not sync markers, each folder once): ok")

# 2. Save History copies too
hist = sh.SaveHistory(sh.history_dir(bk))
tmp = T / "histsrc/EUR/Card A"; tmp.mkdir(parents=True)
(tmp / SKIES).write_bytes(good_h + good_d)
hist.snapshot(sh.location_key("Dolphin", "GameCube Saves"), T / "histsrc", "local", when=time.time() - 3600)
hist.save()
sk = es.scan(locs, bk)[0]
ok(len(sk.copies) == 3 and sk.best.kind == "history", [c.kind for c in sk.copies])
print("2. Save History versions are offered too, newest first: ok")

# 3. restore
kept = es.restore(sk, sk.copies[1], bk)                      # the 11-17 copy
data = (card / SKIES).read_bytes()
ok(data == good_h + good_d, "the save is back")
ok(abs((card / SKIES).stat().st_mtime - time.time()) < 5, "dated now, so the next sync uploads it")
ok(kept and kept.is_file() and kept.stat().st_size == 0 and "empty_saves" in str(kept), kept)
try:
    es.restore(sk, sk.copies[2], bk); ok(False, "should refuse")
except ValueError:
    ok((card / SKIES).read_bytes() == good_h + good_d, "a file with data again is left alone")
print("3. restore puts the copy back, keeps the empty file, never overwrites data: ok")

# 4. the GameCube folder view
(card / SKIES).write_bytes(b"")
files, problems = gcfolder.list_folder(card)
emp = [f for f in files if f.empty]
ok(len(files) == 2 and len(emp) == 1 and not problems, (files, problems))
ok(emp[0].save.game_id == "GEAP8P" and emp[0].save.filename == "SKIES_LEGENDS", emp[0].save)
rep = gcfolder.transfer(emp, gc / "EUR/Card B", False, bk)
ok(not rep.done and rep.refused and "empty" in rep.refused[0][1], rep)
rep = gcfolder.export(emp, bk / "GameCube exports")
ok(not rep.done and rep.refused, "export refuses it too")
print("4. gcfolder lists the empty .gci (game ID from its name) and won't copy or export it: ok")


class FakeApp(tk.Tk):
    backup_dir = bk
    cfg = {"history": {}}
    opened = []
    def resolve(self, emu): return {"GameCube Saves": gc, "Wii Saves": None, "Save States": None}
    def open_folder(self, p): self.opened.append(Path(p))


class Titles:
    def lookup(self, kind, gid): return "Skies of Arcadia Legends" if gid.startswith("GEAP") else ""


app = FakeApp(); app.geometry("10x10+0+0"); app.titles = Titles()


def pump(sec, until=None):
    end = time.time() + sec
    while time.time() < end:
        app.update(); time.sleep(0.02)
        if until and until():
            return True
    return bool(until and until())


# 5. GameCube manager
d = UG.GCCardDialog(app)
v = d.view
ok(pump(10, lambda: bool(v.items[0])), "card loaded")
titles = [it.title for it in v.items[0]]
ok("Empty save · Skies of Arcadia Legends" in titles, titles)
v.cursor[0] = [it.title for it in v.items[0]].index("Empty save · Skies of Arcadia Legends")
v.side = 0
shown = []
v.show_overlay = lambda title, body, opts, cb: shown.append((title, body, opts, cb))
v.save_menu()
keys = [k for k, _t in shown[-1][2]]
ok(keys[0] == "empty" and "copy" not in keys[:1], keys)
v.details()
ok("empty (0 bytes)" in shown[-1][1], shown[-1][1])
shown[-2][3]("empty")
dlgs = [w for w in app.winfo_children() if isinstance(w, UE.EmptySavesDialog)]
ok(len(dlgs) == 1, "Put back a kept copy… opens Empty saves for the GameCube folder")
e = dlgs[0]
ok(pump(15, lambda: bool(e.rows)), "listed")
ok(len(e.rows) == 1, "only the GameCube folder")
f = list(e.rows.values())[0]
row = e.tree.item(list(e.rows)[0], "values")
ok(row[0].startswith("Skies of Arcadia") and "Save History" in row[4], row)
ok(len(e.copy_rows) == 3, "its three copies are listed")
print("5. the GameCube manager marks the empty save and opens Empty saves from it: ok")

# 6. restore one from the window (choose the 11-17 copy), then the card shows the real save
import tkinter.messagebox as mb
mb.askyesno = lambda *a, **k: True
mb.showinfo = mb.showwarning = lambda *a, **k: None
pick = [iid for iid, c in e.copy_rows.items() if c.kind == "overwritten" and "2026-11-17" in c.describe()][0]
e.copies.selection_set(pick)
e.restore_selected_copy()
ok((card / SKIES).read_bytes() == good_h + good_d, "restored from the window")
ok(pump(15, lambda: not e.rows and "No empty" in e.summary.cget("text")), e.summary.cget("text"))
e.destroy()
ok(pump(5, lambda: not any(it.empty for it in v.items[0])), "the card reloads with the real save")
ok(any(it.detail == "35:12 Valua" for it in v.items[0]), [it.detail for it in v.items[0]])
print("6. restoring from the window brings the save back on the card: ok")

# 7. the full window: every folder, Restore all
(card / SKIES).write_bytes(b"")
e = UE.EmptySavesDialog(app, locations=locs)
ok(pump(15, lambda: len(e.rows) == 2), "both empty files listed")
ok(str(e.b_all.cget("state")) == "normal", "Restore all is offered")
e.restore_all()
ok((card / SKIES).stat().st_size > 0 and (ps2 / "Mcd001.ps2").stat().st_size == 0, "only ones with a copy")
ok(pump(15, lambda: len(e.rows) == 1 and "No copy" in e.tree.item(e.tree.get_children()[0], "values")[4]),
   "the PS2 card stays listed, with no copy")
e.destroy()
d.destroy()
app.destroy()
print(f"gui_emptysaves: {checks} checks passed OK")
