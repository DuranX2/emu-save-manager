"""The main window has the app icon (window, taskbar, and every dialog it opens)."""
import os, shutil, sys
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/iconapp"); shutil.rmtree(T, ignore_errors=True); T.mkdir()
os.environ["HOME"] = str(T)
sys.path.insert(0, ROOT)
import emu_save_manager as esm
app = esm.App(); app.update()
assert len(app._icons) == 4 and app._icons[0].width() == 256, app._icons
print("window icon set:", [i.width() for i in app._icons])
app.destroy()
print("GUI ICON OK")
