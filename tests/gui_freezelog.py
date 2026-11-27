"""The Library's log file and freeze watchdog, and the window staying responsive while a scrape
floods it with progress and pictures (run with xvfb-run -a):
  * ~/.emu_rom_library/logs/library.log is written: start-up, the scrape's progress, uploads;
  * a window stuck for longer than the watchdog's limit gets every thread's stack written to the
    log, and a line when it recovers;
  * 30,000 progress events and thousands of pictures arriving in a few seconds don't stop the
    window from handling its own events (before, the event loop ran until the queue was empty);
  * covers already loaded stay loaded during a scrape (only "no picture yet" ones are asked again)."""
import faulthandler, io, os, shutil, sys, threading, time
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/freezelog"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
sys.path.insert(0, ROOT)
faulthandler.dump_traceback_later(170, exit=True)
from PIL import Image
import romlibrary as L, libmeta as M, ui_library as U, applog

checks = 0


def ok(cond, what):
    global checks
    assert cond, what
    checks += 1


lib, local = T / "NUC/Games/roms", T / "Emulation/roms"
for i in range(40):
    p = lib / f"snes/Game {i:02d} (USA).sfc"; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(os.urandom(300))
local.mkdir(parents=True)
app = U.LibraryApp(cfg={"library": str(lib), "local_roms": str(local), "view": "covers"})
app.geometry("1100x700+0+0")


def pump(sec=0.3, until=None):
    end = time.time() + sec
    while time.time() < end:
        app.update(); time.sleep(0.02)
        if until and until():
            return True
    return bool(until and until())


assert pump(30, lambda: app.lib and len(app.lib.items) == 40 and not app._indexing)
logf = L.APP_DIR / "logs/library.log"
ok(applog.log_path() == logf and "ROM Library" in logf.read_text() and "starting" in logf.read_text(), "start-up logged")

# 1. a scrape is logged (a stand-in source; 60 games so the 50-game progress line appears)
for i in range(40, 60):
    (lib / f"snes/Game {i:02d} (USA).sfc").write_bytes(os.urandom(300))
app.refresh(); pump(20, lambda: len(app.lib.items) == 60 and not app._indexing)


def png():
    b = io.BytesIO(); Image.new("RGB", (300, 220), "teal").save(b, "PNG"); return b.getvalue()


class Source:
    name, label, available = "screenscraper", "test", True
    def lookup(self, item):
        if "Game 13" in item.title:
            raise ValueError("odd data from the source")
        return {"title": item.title, "desc": "x"}, {"cover": png()}


sc = M.Scraper(app.meta, [Source()], list(app.lib.items), progress=lambda d, t, m: app.events.put(("scrape", d, t, m)))
th = threading.Thread(target=sc.run); th.start()
ok(pump(60, lambda: not th.is_alive()), "scrape finished")
pump(1.0)
text = logf.read_text()
ok("artwork: looking up 60 game(s) with screenscraper" in text, text[-800:])
ok("artwork: 50 of 60" in text and "artwork: uploaded" in text and "artwork: finished" in text, text[-1200:])
ok("failed on snes/Game 13 (USA).sfc" in text and "ValueError: odd data" in text, "one game's error is logged...")
ok(app.meta.get("snes/Game 14 (USA).sfc").get("desc") == "x", "...and the look-up carried on")
print("1. start-up and the scrape (progress, uploads, an error) are in the log: ok")

# 2. the watchdog: a stuck window gets every thread's stack in the log
wd = applog.Watchdog(app, seconds=1).start()
pump(1.5)


def stuck_here():
    time.sleep(3)                      # the window can't handle events meanwhile


stuck_here()
pump(2.5)
wd.stop()
text = logf.read_text()
ok("The window hasn't responded for" in text, text[-500:])
ok("stuck_here" in text and "gui_freezelog.py" in text, "the stack shows where it was stuck")
ok("The window responds again" in text, "and when it recovered")
print("2. a stuck window is recorded with every thread's stack: ok")

# 3. a flood of progress and pictures: the window keeps handling its own events
beats, stop = [], threading.Event()


def beat():
    beats.append(time.monotonic())
    if not stop.is_set():
        app.after(100, beat)


def flood():
    for n in range(30000):
        app.events.put(("scrape", n, 30000, f"Game {n}"))
        if n % 10 == 0:
            app.events.put(("call", lambda: app.on_image("snes/Game 01 (USA).sfc")))
        if n % 3000 == 0:
            time.sleep(0.05)


app.after(100, beat)
t = threading.Thread(target=flood); t.start()
ok(pump(60, lambda: not t.is_alive() and app.events.empty()), "all events handled")
stop.set(); pump(0.3)
gaps = [b - a for a, b in zip(beats, beats[1:])]
ok(gaps and max(gaps) < 1.0, f"the window kept responding (longest gap {max(gaps):.2f} s)")
ok(app.scrape_status.startswith("Artwork: 29999 of 30000"), app.scrape_status)
print(f"3. a flood of 30,000 progress events + 3,000 pictures: longest pause {max(gaps):.2f} s: ok")

# 4. covers already loaded stay loaded during a scrape
pump(5, lambda: any(v for v in app.images.images.values()))
loaded = {k for k, v in app.images.images.items() if v is not None}
app.images.images[("snes/none", "cover", 1, 1)] = None
ok(loaded, "some covers loaded")
app._scrape_progress(10, 100, "x")
ok(loaded <= set(app.images.images) and ("snes/none", "cover", 1, 1) not in app.images.images,
   "loaded covers kept, 'no picture yet' asked again")
print("4. loaded covers stay loaded during a scrape: ok")

app.destroy()
print(f"gui_freezelog: {checks} checks passed OK")
