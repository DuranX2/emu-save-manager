"""Faster artwork scraping (libmeta.Scraper / MetaStore): pictures upload in the background while
lookups carry on, ES-DE-only runs use bigger batches, each system's information file is written
once per system, stopping partway keeps what was done, and a failed upload is tried again."""
import io, os, shutil, sys, threading, time
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/romlib_meta_batch"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
sys.path.insert(0, ROOT)
import cloudsync, romlibrary as L, libmeta as M
from PIL import Image

checks = 0


def ok(cond, msg=""):
    global checks
    assert cond, msg
    checks += 1


def png(color):
    b = io.BytesIO(); Image.new("RGB", (60, 80), color).save(b, "PNG"); return b.getvalue()


def mk(path, data=b"x"):
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data); return p


COUNTS = {"ps2": 130, "gc": 6, "psx": 4}
home = T / "home"
media = home / "Emulation/tools/downloaded_media"
lib = T / "library"
for system, n in COUNTS.items():
    games = []
    for i in range(n):
        name = f"Game {system} {i:03d} (USA)"
        mk(lib / system / f"{name}.iso", b"x" * 100)
        mk(media / system / "covers" / f"{name}.png", png((i % 250, 40, 90)))
        games.append(f"<game><path>./{name}.iso</path><name>{name}</name><desc>About {name}.</desc></game>")
    mk(home / f"ES-DE/gamelists/{system}/gamelist.xml", ("<gameList>" + "".join(games) + "</gameList>").encode())
(home / "Emulation/roms").mkdir(parents=True)
cloudsync.emudeck_roots = lambda: [home / "Emulation"]


class Runner(cloudsync.RcloneRunner):
    """Real rclone, but counts picture uploads, can make them slow, and can fail one."""
    def __init__(self):
        super().__init__(shutil.which("rclone"), "", lambda m: None)
        self.copies, self.windows, self.slow, self.fail_next = 0, [], 0.0, False

    def capture(self, args, **kw):
        if args and args[0] == "copy" and "--files-from-raw" in args:
            t0 = time.monotonic()
            time.sleep(self.slow)
            if self.fail_next:
                self.fail_next = False
                self.windows.append((t0, time.monotonic()))
                return 1, "simulated network drop"
            r = super().capture(args, **kw)
            self.copies += 1
            self.windows.append((t0, time.monotonic()))
            return r
        return super().capture(args, **kw)


def setup():
    shutil.rmtree(lib / ".library", ignore_errors=True)
    runner = Runner()
    lb = L.Library(runner, str(lib), str(T / "local"), machine="deck1", label="Steam Deck")
    lb.refresh()
    writes = []
    put = lb._put_json

    def counting_put(loc, data):
        writes.append(loc.rsplit("/", 1)[-1])
        return put(loc, data)
    lb._put_json = counting_put
    M.MEDIA_CACHE = T / f"media_{time.time_ns()}"
    return runner, lb, M.MetaStore(lb), writes


def saved(system):
    d = L._read_json(lib / ".library/meta" / f"{system}.json", {})
    with_cover = [k for k, r in d.items() if r.get("media", {}).get("cover")
                  and (lib / ".library/media" / r["media"]["cover"]).is_file()]
    return len(d), len(with_cover)


total = sum(COUNTS.values())

# 1. ES-DE only: every game saved; one information write per system; few, big uploads
runner, lb, store, writes = setup()
es = M.EsdeSource(M.esde_locations())
keys = sorted(lb.items, key=lambda k: (k.split("/")[0] != "psx", k))   # mixed order on purpose
job = M.Scraper(store, [es], keys)
job.run()
for system, n in COUNTS.items():
    ok(saved(system) == (n, n), (system, saved(system)))
ok(sorted(writes) == sorted(s + ".json" for s in COUNTS), f"one information write per system, got {writes}")
ok(runner.copies <= 6, f"{runner.copies} picture uploads for {total} games")
ok(job.message.startswith(f"Done: information for {total} of {total}"), job.message)
print(f"1. {total} games: {len(writes)} information writes, {runner.copies} picture uploads:", job.message)

# 2. uploads run in the background: lookups carry on during a slow upload
runner, lb, store, writes = setup()
runner.slow = 1.5
seen = []
job = M.Scraper(store, [M.EsdeSource(M.esde_locations())], list(lb.items),
                progress=lambda d, t, m: seen.append((time.monotonic(), d)))
job.FLUSH_EVERY_LOCAL = 20
job.run()
during = [d for t, d in seen if any(a < t < b for a, b in runner.windows)]
ok(len(runner.windows) >= 2 and len(during) >= 10, f"{len(during)} lookups during {len(runner.windows)} uploads")
ok(all(saved(s) == (n, n) for s, n in COUNTS.items()), "everything saved after a slow run")
print(f"2. {len(during)} games looked up while pictures were uploading")

# 3. stopping partway keeps what was done (information and pictures)
runner, lb, store, writes = setup()
job = M.Scraper(store, [M.EsdeSource(M.esde_locations())], [k for k in lb.items if k.startswith("ps2/")])


def stop_at(d, t, m):
    if d == 70:
        job.cancel()
job.progress = stop_at
job.run()
n, covers = saved("ps2")
ok(job.message == "Stopped." and n >= 70 and covers == n, (job.message, n, covers))
job2 = M.Scraper(store, [M.EsdeSource(M.esde_locations())], [k for k in lb.items if k.startswith("ps2/")])
job2.run()
ok(saved("ps2") == (130, 130) and job2.message.startswith(f"Done: information for {130 - n} of {130 - n}"),
   (saved("ps2"), job2.message))
print(f"3. stopped after {n} games, all kept; the next run did the other {130 - n}")

# 4. a failed upload is tried again; nothing is lost
runner, lb, store, writes = setup()
runner.fail_next = True
job = M.Scraper(store, [M.EsdeSource(M.esde_locations())], [k for k in lb.items if not k.startswith("ps2/")])
job.run()
ok(saved("gc") == (6, 6) and saved("psx") == (4, 4), (saved("gc"), saved("psx")))
ok(store.write_ok and not list((store._stage_dir).rglob("*.png")), "staging folder emptied")
print("4. a failed upload was retried: every picture is in the library")

# 5. online sources keep the smaller batches; flush() still works on its own
s = M.Scraper(store, [], [])
ok(s.FLUSH_EVERY == 25 and s.FLUSH_EVERY_LOCAL > s.FLUSH_EVERY)
store.put("gc/" + next(iter(COUNTS)), {"title": "x"})
ok(store.flush() is True)
print(f"{checks} checks passed")
