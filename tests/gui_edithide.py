"""ROM Library: editing a game's information, and hiding games (run with xvfb-run -a).
  * Edit information: the title, description etc. are saved to the library for every machine;
    search finds the new title; a later look-up or a match picked by hand doesn't replace what
    was edited; emptying a field removes it; the game's file isn't renamed.
  * Hide: hidden games leave every view and the system counts, Show: Hidden lists them, the
    flag is in the library's shared library.json (every machine), and they can be shown again.
  * Both are in the game's menu (right-click / controller)."""
import faulthandler, json, os, shutil, sys, time
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/edithide"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
sys.path.insert(0, ROOT)
faulthandler.dump_traceback_later(150, exit=True)
import ui_library as U, ui_covers as C, libmeta as M, libpad

checks = 0


def ok(cond, what):
    global checks
    assert cond, what
    checks += 1


lib, local = T / "NUC/Games/roms", T / "Emulation/roms"
for n in ("Alpha", "Bravo", "Charlie"):
    p = lib / f"snes/{n} (USA).sfc"; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(os.urandom(200))
p = lib / "gba/Delta (USA).gba"; p.parent.mkdir(parents=True); p.write_bytes(os.urandom(200))
local.mkdir(parents=True)
app = U.LibraryApp(cfg={"library": str(lib), "local_roms": str(local), "view": "list"})


def pump(sec=0.3, until=None):
    end = time.time() + sec
    while time.time() < end:
        app.update(); time.sleep(0.03)
        if until and until():
            return True
    return bool(until and until())


assert pump(30, lambda: app.lib and len(app.lib.items) == 4 and not app._indexing)
A, B, Cc, D = "snes/Alpha (USA).sfc", "snes/Bravo (USA).sfc", "snes/Charlie (USA).sfc", "gba/Delta (USA).gba"
app.meta.put(A, {"title": "Alpha", "desc": "Scraped description.", "genre": "Action", "year": "1994"})
pump(0.2)

# 1. edit through the form
app.edit_info(A); pump(0.3)
dlg = [w for w in app.winfo_children() if isinstance(w, C.EditInfoDialog)][0]
ok(dlg.vars["title"].get() == "Alpha" and dlg.vars["released"].get() == "1994", "the form shows what's there")
dlg.vars["title"].set("Alpha Prime: Director's Cut")
dlg.vars["genre"].set("")                                        # emptied: removed
dlg.desc.delete("1.0", "end"); dlg.desc.insert("1.0", "My own description.")
dlg.save()
ok(pump(15, lambda: app.status.cget("text").startswith("Information saved")), app.status.cget("text"))
rec = app.meta.get(A)
ok(rec["title"] == "Alpha Prime: Director's Cut" and rec["desc"] == "My own description." and "genre" not in rec, rec)
ok(sorted(rec["edited"]) == ["desc", "genre", "title"], rec.get("edited"))
ok(rec["year"] == "1994", "untouched fields stay")
shared = json.loads((lib / ".library/meta/snes.json").read_text())
srec = shared.get(A) or shared.get("items", {}).get(A) or {}
ok("Director's Cut" in json.dumps(shared), "saved to the library for every machine")
ok((lib / "snes/Alpha (USA).sfc").is_file(), "the game's file isn't renamed")
ok(app.display_title(A) == "Alpha Prime: Director's Cut", app.display_title(A))
app.v_search.set("director"); app.fill_games(); pump(0.2)
ok(list(app.tree.get_children()) == [A], "search finds the new title")
app.v_search.set(""); app.fill_games(); pump(0.2)

# 2. a look-up and a by-hand match don't replace edited fields
app.meta.put(A, {"title": "Scraper Title", "desc": "Scraper text.", "genre": "Platform", "publisher": "Pub"})
app.meta.put(A, {"title": "Match Title", "desc": "Match text."}, replace=True)
rec = app.meta.get(A)
ok(rec["title"] == "Alpha Prime: Director's Cut" and rec["desc"] == "My own description.", rec)
ok("genre" not in rec, "an emptied field isn't filled back in")
ok(rec.get("publisher") == "Pub", "fields that weren't edited still fill in")
print("1-2. edited information is saved, searchable, and kept by look-ups and matches: ok")

# 3. hide two games
app.tree.selection_set([B, D]); app._update_selection(); pump(0.2)
labels = [t for t, _f in libpad.game_items(app, [B, D])]
ok("Hide" in labels, labels)
app.toggle_hidden()
ok(pump(15, lambda: app.lib.is_hidden(B) and app.lib.is_hidden(D) and "hidden" in app.status.cget("text")),
   app.status.cget("text"))
pump(0.3)
ok(sorted(app.tree.get_children()) == sorted([A, Cc]), sorted(app.tree.get_children()))
ok("gba" not in app.sys_tree.get_children(), "a system with only hidden games leaves the list")
ok(app.sys_tree.item("snes", "values")[1] == "2", app.sys_tree.item("snes", "values"))
for f in ("On this machine", "Not on this machine", "Favourites", "Missing cover"):
    app.v_filter.set(f); app.fill_games(); pump(0.1)
    ok(B not in app.tree.get_children(), f"hidden under {f}")
app.v_filter.set("Hidden"); app.fill_games(); pump(0.2)
ok(sorted(app.tree.get_children()) == sorted([B, D]), "Show: Hidden lists them")
shared = json.loads((lib / ".library/library.json").read_text())
ok(shared["items"][B].get("hidden") is True and shared["items"][D].get("hidden") is True, shared)
print("3. hidden games leave every view and the counts, Show: Hidden lists them, shared via the library: ok")

# 4. show one again
app.tree.selection_set([B]); app._update_selection(); pump(0.2)
labels = [t for t, _f in libpad.game_items(app, [B])]
ok("Show again (unhide)" in labels and "Edit information…" in labels, labels)
app.toggle_hidden()
ok(pump(15, lambda: not app.lib.is_hidden(B)), "unhidden")
pump(0.3)
ok(list(app.tree.get_children()) == [D], "only Delta is still hidden")
app.v_filter.set("All games"); app.fill_games(); pump(0.2)
ok(sorted(app.tree.get_children()) == sorted([A, B, Cc]), sorted(app.tree.get_children()))
shared = json.loads((lib / ".library/library.json").read_text())
ok(B not in shared["items"] or not shared["items"][B].get("hidden"), "cleared in the library too")
print("4. shown again: ok")
app.destroy()
print(f"gui_edithide: {checks} checks passed OK")
