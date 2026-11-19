"""Cloud Sync's folder list on a Steam Deck-sized screen (1280x800): with more folders than fit, it
has a scrollbar, scrolls with the wheel and by dragging a finger, and a tap on ✓ / ✗ switches a row."""
import os, shutil, subprocess, sys, time, tkinter as tk
from pathlib import Path
if os.environ.get("ESM_DECK_SCREEN") != "1" and shutil.which("xvfb-run"):
    r = subprocess.run(["xvfb-run", "-a", "-s", "-screen 0 1280x800x24", sys.executable, __file__],
                       env=dict(os.environ, ESM_DECK_SCREEN="1"))
    sys.exit(r.returncode)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/deckfolders"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
sys.path.insert(0, ROOT)
import ui_cloud as U

EMUS = ["PCSX2", "DuckStation", "Dolphin", "Yuzu", "Azahar", "melonDS", "PPSSPP", "RPCS3"]
dirs = {}
for e in EMUS:
    for lab in ("Saves", "States"):
        p = T / "deck" / e / lab; p.mkdir(parents=True); dirs[(e, lab)] = p
conf = T / "rclone.conf"; conf.write_text(f"[onedrive]\ntype = alias\nremote = {T/'cloud'}\n")

class FakeApp(tk.Tk):
    backup_dir = T / "backups"
    cfg = {"cloud": {"rclone": shutil.which("rclone") or "rclone", "config": str(conf),
                     "remote": "onedrive", "base": "EmuDeck/saves"}}
    profiles = {e: {"locations": [{"label": "Saves"}, {"label": "States"}]} for e in EMUS}
    def resolve(self, emu): return {lab: dirs[(emu, lab)] for lab in ("Saves", "States")}
    def save_cfg(self): pass
    def refresh_all(self): pass
    def open_folder(self, p): pass

U.messagebox.showinfo = lambda *a, **k: None
app = FakeApp(); app.withdraw()
d = U.CloudSyncDialog(app)
def pump(sec=0.3):
    end = time.time() + sec
    while time.time() < end:
        d.update(); time.sleep(0.03)
pump(0.8)
d.state("normal"); d.geometry("1280x760+0+0"); pump(0.6)
rows = d.tree.get_children()
assert len(rows) > 4, len(rows)
assert d.compact and d.tree_sb.winfo_ismapped(), "scrollbar should be visible"
first, last = d.tree.yview()
assert first == 0 and last < 1, (first, last)
print(f"1. {len(rows)} folders, 4 visible, scrollbar shown (view {first:.2f}-{last:.2f}): ok")

# 2. wheel / trackpad
d.tree.event_generate("<Button-5>", x=200, y=60); pump()
assert d.tree.yview()[0] > 0, d.tree.yview()
d.tree.event_generate("<Button-4>", x=200, y=60); pump()
assert d.tree.yview()[0] == 0
print("2. wheel scrolls the list: ok")

# 3. finger drag: press, move up 100 px, release -> scrolled down, nothing switched
before = {k: p["enabled"] for k, p in d.pairs.items()}
d.tree.event_generate("<ButtonPress-1>", x=200, y=110)
for y in range(110, 0, -10):
    d.tree.event_generate("<B1-Motion>", x=200, y=y); pump(0.02)
d.tree.event_generate("<ButtonRelease-1>", x=200, y=10); pump()
assert d.tree.yview()[0] > 0, d.tree.yview()
assert before == {k: p["enabled"] for k, p in d.pairs.items()}, "a drag must not switch rows"
print("3. dragging a finger scrolls, switches nothing:", f"{d.tree.yview()[0]:.2f}")

# 4. scroll to the bottom and tap the ✓ of the last row
d.tree.yview_moveto(1); pump()
key = rows[-1]; d.tree.see(key); pump()
x0, y0, w, h = d.tree.bbox(key, "sync")
was = d.pairs[key]["enabled"]
d.tree.event_generate("<ButtonPress-1>", x=x0 + w // 2, y=y0 + h // 2)
d.tree.event_generate("<ButtonRelease-1>", x=x0 + w // 2, y=y0 + h // 2); pump()
assert d.pairs[key]["enabled"] != was
assert d.tree.set(key, "sync") == ("✓" if not was else "✗")
print("4. tap on ✓/✗ of the last row switched it:", d.tree.set(key, "sync"))

# 5. a tap elsewhere in a row only selects it; the button switches the selected row
k2 = rows[-2]; bx, by, bw, bh = d.tree.bbox(k2, "emu"); was2 = d.pairs[k2]["enabled"]
d.tree.event_generate("<ButtonPress-1>", x=bx + 5, y=by + bh // 2)
d.tree.event_generate("<ButtonRelease-1>", x=bx + 5, y=by + bh // 2); pump()
assert d.pairs[k2]["enabled"] == was2 and d.tree.selection() == (k2,)
d._toggle_selected(); pump()
assert d.pairs[k2]["enabled"] != was2
print("5. tap selects, 'Switch on / off' switches: ok")

# 6. All off / All on, and the choice is saved
d._set_all(False); pump()
assert not any(p["enabled"] for p in d.pairs.values())
assert all(v.get("enabled") is False for v in app.cfg["cloud"]["pairs"].values())
d._set_all(True); pump()
assert all(p["enabled"] for p in d.pairs.values())
print("6. All off / All on, saved to the settings: ok")
d.destroy(); app.destroy()
print("ALL OK")
