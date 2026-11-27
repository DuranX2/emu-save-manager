"""
applog.py - a log file for the apps, and a watchdog that records what was going on when a window
stops responding.

  setup(folder, name)        logging to <folder>/logs/<name>.log (1 MB, 3 old copies kept)
  Watchdog(root).start()     if the Tk window hasn't handled events for FREEZE_SECONDS, write
                             every thread's stack to the log once (and how long it lasted when it
                             recovers), so a freeze can be found from the log afterwards

Never logs credentials: callers log what happened, not command lines with passwords.
"""

import faulthandler
import logging
import logging.handlers
import threading
import time
from pathlib import Path

FREEZE_SECONDS = 10
log = logging.getLogger("esm")
_file = None


def setup(folder, name) -> Path:
    """Log to <folder>/logs/<name>.log; returns the log file's path."""
    global _file
    d = Path(folder) / "logs"
    path = d / f"{name}.log"
    try:
        d.mkdir(parents=True, exist_ok=True)
        if not any(isinstance(h, logging.handlers.RotatingFileHandler) and Path(h.baseFilename) == path
                   for h in log.handlers):
            h = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
            h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s"))
            log.addHandler(h)
        log.setLevel(logging.INFO)
        _file = path
    except OSError:
        pass
    return path


def log_path():
    return _file


class Watchdog:
    """A heartbeat from the Tk thread every second; a background thread notices when it stops."""

    def __init__(self, root, seconds=FREEZE_SECONDS):
        self.root, self.seconds = root, seconds
        self.beat = time.monotonic()
        self.running = False
        self._after = None
        self.frozen_since = None

    def start(self):
        self.running = True
        self._tick()
        threading.Thread(target=self._watch, name="watchdog", daemon=True).start()
        return self

    def stop(self):
        self.running = False
        if self._after:
            try:
                self.root.after_cancel(self._after)
            except Exception:  # noqa - the window is going away
                pass

    def _tick(self):
        self.beat = time.monotonic()
        if self.running:
            try:
                self._after = self.root.after(1000, self._tick)
            except Exception:  # noqa
                self.running = False

    def _watch(self):
        while self.running:
            time.sleep(1)
            idle = time.monotonic() - self.beat
            if idle >= self.seconds and self.frozen_since is None:
                self.frozen_since = self.beat
                log.warning("The window hasn't responded for %d s. What every thread is doing:", idle)
                for h in log.handlers:
                    h.flush()
                if _file:
                    try:
                        with open(_file, "a", encoding="utf-8") as f:
                            faulthandler.dump_traceback(file=f, all_threads=True)
                    except OSError:
                        pass
            elif idle < self.seconds and self.frozen_since is not None:
                log.warning("The window responds again (it was stuck for about %d s).",
                            time.monotonic() - self.frozen_since)
                self.frozen_since = None
