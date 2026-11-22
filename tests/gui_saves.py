"""The grouped save list (run with xvfb-run; restarts itself on a 1280x800 screen like the Deck):
Dolphin's GameCube saves, card image, Wii saves and states show game names, descriptions and
icons; names for states arrive from titledb in the background; the details strip; picking a save
backs up all its files; 'Group by game' off gives the flat list. ESM_SHOTS=<dir> saves screenshots."""
import json, os, shutil, subprocess, sys, time, zipfile
from pathlib import Path
if os.environ.get("ESM_DECK_SCREEN") != "1" and shutil.which("xvfb-run"):
    r = subprocess.run(["xvfb-run", "-a", "-s", "-screen 0 1280x800x24", sys.executable, __file__],
                       env=dict(os.environ, ESM_DECK_SCREEN="1"))
    sys.exit(r.returncode)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/savesgui"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
SHOTS = os.environ.get("ESM_SHOTS")
import savefixtures as F

# Dolphin folders, plus an "install" with GameTDB's list for the state's name
gc, wii, st = T / "dolphin/GC", T / "dolphin/Wii", T / "dolphin/StateSaves"
F.gci_file(gc / "USA/Card A/01-GZLE-gczelda2.gci")
F.gci_file(gc / "USA/Card A/01-GZLE-gczelda1.gci", fname=b"gczelda1", detail="File 1")
F.gci_file(gc / "USA/Card A/8P-GPVE-pikmin.gci", code=b"GPVE", maker=b"8P", fname=b"pikmin",
           title="Pikmin 2", detail="Day 12", icon="ci8shared", banner=False)
F.gc_card(gc / "MemoryCardB.USA.raw", [F.gci(code=b"GM4E", title="Mario Kart", detail="Double Dash!!")])
F.wii_banner(wii / "title/00010000/524d4745/data/banner.bin")
(wii / "title/00010000/524d4745/data/GameData.bin").write_bytes(b"x" * 3000)
st.mkdir(parents=True)
for n in ("GZLE01.s01", "GXXE01.s02"):
    (st / n).write_bytes(b"s" * 500)
(gc / "USA/IPL.bin").write_bytes(b"bios")                 # not a save: listed as unrecognised
(T / "home/.emu_save_manager").mkdir(parents=True)
(T / "home/.emu_save_manager/config.json").write_text(json.dumps({"overrides": {
    "Dolphin::GameCube Saves": str(gc), "Dolphin::Wii Saves": str(wii), "Dolphin::Save States": str(st)}}))
inst = T / "dolphin-install/Sys"; inst.mkdir(parents=True)
(inst / "wiitdb-en.txt").write_text("TITLES = x\nGXXE01 = Fake Cube Racer\n")

import titledb
_orig_roots = titledb.install_roots
titledb.install_roots = lambda extra=(): _orig_roots(extra) + [T / "dolphin-install"]
titledb.rom_roots = lambda: []
import emu_save_manager as esm
app = esm.App()
KEEP = [app]


def pump(sec, until=None):
    end = time.time() + sec
    while time.time() < end:
        app.update(); time.sleep(0.03)
        if until and until():
            return True
    return bool(until and until())


def shot(name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        app.update(); time.sleep(0.3); app.update()
        os.system(f"import -window root {SHOTS}/{name}.png 2>/dev/null")


names = list(app.emu_list.get(0, "end"))
app.emu_list.selection_clear(0, "end"); app.emu_list.selection_set(names.index("Dolphin"))
app.on_select_emulator()
pump(0.3)
tree, sl = app.save_tree, app.save_list
tops = {tree.item(i, "text"): i for i in tree.get_children()}
assert "The Legend of Zelda" in tops and "Pikmin 2" in tops and "Super Mario Galaxy" in tops, tops
assert "Memory card B (USA)" in tops and "IPL.bin" in tops, tops
zeldas = [i for i in tree.get_children() if tree.item(i, "text") == "The Legend of Zelda"]
assert sorted(tree.set(i, "info") for i in zeldas) == ["File 1", "File 2", "Save state, slot 1"]
assert all(tree.item(i, "image") for i in zeldas if tree.set(i, "info").startswith("File"))
assert tree.item(tops["Pikmin 2"], "image")
card = tops["Memory card B (USA)"]
kids = tree.get_children(card)
assert [tree.item(k, "text") for k in kids] == ["Memory card image", "Mario Kart"]
assert tree.set(kids[1], "info").startswith("Double Dash!!")
assert "IPL.bin" in tops and "unknown" in tree.item(tops["IPL.bin"], "tags")
print("1. Dolphin's saves are grouped by game with names, descriptions and icons; the card lists its saves: ok")
shot("saves_grouped")

# the state named only by ID gets its name once titledb has loaded
assert pump(10, lambda: any(tree.item(i, "text") == "Fake Cube Racer" for i in tree.get_children())), \
    [tree.item(i, "text") for i in tree.get_children()]
print("2. names from the emulator's own list arrive in the background (the GXXE01 state): ok")

# details strip
tops = {tree.item(i, "text") + "|" + tree.set(i, "info"): i for i in tree.get_children()}
mg = tops["Super Mario Galaxy|Mario: 3 stars"]
tree.selection_set(mg); pump(0.2)
assert sl.d_title.cget("text") == "Super Mario Galaxy — Mario: 3 stars"
assert "Wii" in sl.d_info.cget("text") and "Game ID RMGE" in sl.d_info.cget("text") and "2 files" in sl.d_info.cget("text")
assert sl.d_pic.cget("image")
tree.item(mg, open=True)
kid = tree.get_children(mg)[0]
tree.selection_set(kid); pump(0.2)
assert "This file:" in sl.d_info.cget("text")
print("3. the details strip shows the selected save's banner, name and details: ok")
tree.selection_set(mg); pump(0.2)
shot("saves_details")

# backing up a whole save by picking its row
z2 = tops["The Legend of Zelda|File 2"]
tree.selection_set((z2, mg)); pump(0.1)
picked = sorted(e.rel.as_posix() for e in sl.selected_entries())
assert picked == ["USA/Card A/01-GZLE-gczelda2.gci", "title/00010000/524d4745/data/GameData.bin",
                  "title/00010000/524d4745/data/banner.bin"], picked
esm.simpledialog.askstring = lambda *a, **k: "before boss"
app.backup_selected()
assert pump(10, lambda: list((T / "home/.emu_save_manager/backups/Dolphin").glob("*.zip")))
pump(0.5)
z = next((T / "home/.emu_save_manager/backups/Dolphin").glob("*.zip"))
names_in = sorted(n for n in zipfile.ZipFile(z).namelist() if n != "manifest.json")
assert len(names_in) == 3 and any(n.endswith("gczelda2.gci") for n in names_in), names_in
print("4. selecting saves backs up every file that belongs to them: ok")

# flat list
sl.grouped.set(False); app._toggle_grouping(); pump(0.2)
flat = tree.get_children()
assert len(flat) == len(app.entries) == 9 and not any(tree.get_children(i) for i in flat)
assert any(tree.item(i, "text") == "Super Mario Galaxy" and tree.set(i, "info").endswith("banner.bin") for i in flat)
assert json.loads((T / "home/.emu_save_manager/config.json").read_text())["group_saves"] is False
print("5. 'Group by game' off shows the plain file list (game name beside each file), and is remembered: ok")
shot("saves_flat")
sl.grouped.set(True); app._toggle_grouping(); pump(0.2)

# the list fits the Deck's screen: the buttons and summary stay visible
app.update()
assert app.saves_summary.winfo_ismapped() and app.saves_summary.winfo_rooty() + 10 < app.winfo_rooty() + app.winfo_height()
print("6. on a 1280x800 screen the list, details strip and buttons all fit: ok")
app.destroy()
print("gui_saves OK")
