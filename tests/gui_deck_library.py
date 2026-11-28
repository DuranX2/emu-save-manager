"""Steam Deck Game Mode shows one window per app, so the Library opened from the Save Manager stayed
hidden behind it. In Game Mode the Save Manager steps aside while the Library (or the BIOS Vault in it)
is open and comes back when it closes; on the desktop nothing changes."""
import faulthandler, os, shutil, sys, time
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/decklib"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
sys.path.insert(0, ROOT)
faulthandler.dump_traceback_later(90, exit=True)
import emu_save_manager as esm

checks = 0


def ok(c, what):
    global checks
    assert c, what
    checks += 1


class FakeProc:
    def __init__(self):
        self.code = None

    def poll(self):
        return self.code


procs = []
esm.launch_library = lambda *a: (procs.append((a, FakeProc())) or procs[-1][1])
app = esm.App()


def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        app.update(); time.sleep(0.03)


pump(1)
for k in ("XDG_CURRENT_DESKTOP", "SteamGamepadUI", "GAMESCOPE_WAYLAND_DISPLAY"):
    os.environ.pop(k, None)
ok(not esm.in_game_mode(), "desktop")
app.open_library(); pump(0.8)
ok(app.state() != "withdrawn", "desktop: the Save Manager stays")
ok("Opening the ROM Library" in app.status.cget("text"), app.status.cget("text"))

os.environ["XDG_CURRENT_DESKTOP"] = "gamescope"
ok(esm.in_game_mode(), "Game Mode detected")
app.open_library(); pump(0.8)
ok(app.state() == "withdrawn", "Game Mode: steps aside for the Library")
pump(3)
ok(app.state() == "withdrawn", "stays aside while the Library is open")
procs[-1][1].code = 0; pump(1)
ok(app.state() == "normal", "back when the Library closes")
ok(app.status.cget("text") == "Ready.", app.status.cget("text"))

app.open_bios_vault(); pump(0.8)
ok(procs[-1][0] == ("--bios",) and app.state() == "withdrawn", "BIOS Vault too")
procs[-1][1].code = 1; pump(1)
ok(app.state() == "normal" and "closed straight away" in app.status.cget("text"), app.status.cget("text"))

os.environ.pop("XDG_CURRENT_DESKTOP"); os.environ["SteamGamepadUI"] = "1"
ok(esm.in_game_mode(), "SteamGamepadUI also means Game Mode")
app.destroy()
print(f"gui_deck_library: {checks} checks passed OK")
