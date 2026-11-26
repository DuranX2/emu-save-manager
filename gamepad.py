"""
gamepad.py - controller and keyboard navigation for Tk windows, with no extra packages.

  * Windows: XInput (Xbox-style controllers, and anything Steam Input presents as one).
  * Linux / Steam Deck: the joystick devices /dev/input/js*. Steam presents the Deck's
    controls (and other pads) as an Xbox-style controller to programs it starts.
  * Keyboard: arrows, Enter/Space, Escape/Backspace, X (mark), Tab/Menu (menu),
    Q/E or Page Up/Down (previous/next), F2 (switch view).

Everything becomes one of these actions, sent to a callback on the Tk thread:
    up down left right ok back mark menu prev next view
Directions repeat while held. The same action from two sources within a moment (Steam can
send both a key and a button for one press) counts once.
"""
from __future__ import annotations

import ctypes
import glob
import os
import struct
import sys
import time
import weakref

ACTIONS = ("up", "down", "left", "right", "ok", "back", "mark", "menu", "prev", "next", "view")
REPEATING = {"up", "down", "left", "right"}
REPEAT_DELAY, REPEAT_EVERY = 0.38, 0.11
DEDUPE = 0.09
POLL_MS = 16
AXIS_ON = 0.55

KEYS = {
    "Up": "up", "Down": "down", "Left": "left", "Right": "right",
    "Return": "ok", "KP_Enter": "ok", "space": "ok",
    "Escape": "back", "BackSpace": "back",
    "x": "mark", "X": "mark", "Insert": "mark",
    "Tab": "menu", "Menu": "menu", "m": "menu",
    "q": "prev", "Q": "prev", "Prior": "prev", "e": "next", "E": "next", "Next": "next",
    "F2": "view",
}


class Router:
    """Sends actions to `callback`, dropping the same action from another source right after."""

    def __init__(self, callback, clock=time.monotonic):
        self.callback = callback
        self.clock = clock
        self.last = {}                # action -> (time, source)

    def emit(self, action, source, repeat=False):
        now = self.clock()
        t, src = self.last.get(action, (-1e9, None))
        if not repeat and src is not None and src != source and now - t < DEDUPE:
            return False
        self.last[action] = (now, source)
        self.callback(action)
        return True


# ---------------------------------------------------------------------------
# Backends: each poll() returns the set of actions currently held
# ---------------------------------------------------------------------------
XI_BUTTONS = {0x0001: "up", 0x0002: "down", 0x0004: "left", 0x0008: "right",
              0x0010: "view", 0x0020: "menu", 0x0100: "prev", 0x0200: "next",
              0x1000: "ok", 0x2000: "back", 0x4000: "mark", 0x8000: "menu"}


def xinput_held(buttons: int, lx: int, ly: int) -> set:
    held = {a for bit, a in XI_BUTTONS.items() if buttons & bit}
    lim = int(32767 * AXIS_ON)
    if lx < -lim:
        held.add("left")
    elif lx > lim:
        held.add("right")
    if ly > lim:                      # XInput: up is positive
        held.add("up")
    elif ly < -lim:
        held.add("down")
    return held


class _XInputGamepad(ctypes.Structure):
    _fields_ = [("wButtons", ctypes.c_ushort), ("bLeftTrigger", ctypes.c_ubyte),
                ("bRightTrigger", ctypes.c_ubyte), ("sThumbLX", ctypes.c_short),
                ("sThumbLY", ctypes.c_short), ("sThumbRX", ctypes.c_short), ("sThumbRY", ctypes.c_short)]


class _XInputState(ctypes.Structure):
    _fields_ = [("dwPacketNumber", ctypes.c_uint), ("Gamepad", _XInputGamepad)]


class XInputBackend:
    name = "XInput"

    def __init__(self):
        self.dll = None
        for n in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
            try:
                self.dll = ctypes.WinDLL(n)
                break
            except (OSError, AttributeError):
                continue
        if self.dll is None:
            raise OSError("XInput isn't available")
        self.state = _XInputState()
        self.missing = {}            # pad -> next time to look again (absent pads are slow to query)
        self.seen = set()            # pads that answered the last time they were asked

    def poll(self) -> set:
        held = set()
        now = time.monotonic()
        for pad in range(4):
            if self.missing.get(pad, 0) > now:
                continue
            if self.dll.XInputGetState(pad, ctypes.byref(self.state)) != 0:
                self.missing[pad] = now + 2.0
                self.seen.discard(pad)
                continue
            self.seen.add(pad)
            g = self.state.Gamepad
            held |= xinput_held(g.wButtons, g.sThumbLX, g.sThumbLY)
        return held

    def present(self) -> bool:
        return bool(self.seen)

    def close(self):
        pass


# Linux joystick API: struct js_event { u32 time; s16 value; u8 type; u8 number; }
JS_EVENT = struct.Struct("<IhBB")
JS_BUTTON, JS_AXIS, JS_INIT = 0x01, 0x02, 0x80
# Xbox-style layout (xpad, Steam's virtual controller, the Deck through Steam)
JS_BUTTON_MAP = {0: "ok", 1: "back", 2: "mark", 3: "menu", 4: "prev", 5: "next",
                 6: "menu", 7: "view", 11: "left", 12: "right", 13: "up", 14: "down"}


def joystick_held(buttons: dict, axes: dict, button_map=None) -> set:
    bm = JS_BUTTON_MAP if button_map is None else button_map
    held = {bm[b] for b, v in buttons.items() if v and b in bm}
    lim = int(32767 * AXIS_ON)
    for xa, ya in ((0, 1), (6, 7)):          # left stick, D-pad (hat)
        x, y = axes.get(xa, 0), axes.get(ya, 0)
        if x < -lim:
            held.add("left")
        elif x > lim:
            held.add("right")
        if y < -lim:                            # joystick API: up is negative
            held.add("up")
        elif y > lim:
            held.add("down")
    return held


class JoystickBackend:
    name = "joystick"

    def __init__(self, pattern="/dev/input/js*"):
        self.pattern = pattern
        self.devs = {}               # path -> [fd, buttons, axes]
        self.next_scan = 0.0
        self._scan()
        if not self.devs and not glob.glob(pattern):
            pass                     # no pad now; one may be plugged in later

    def _scan(self):
        self.next_scan = time.monotonic() + 2.0
        for path in glob.glob(self.pattern):
            if path in self.devs:
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            self.devs[path] = [fd, {}, {}]

    def add_fd(self, fd, key="test"):
        """For tests: read events from an already open file descriptor."""
        os.set_blocking(fd, False)
        self.devs[key] = [fd, {}, {}]

    def poll(self) -> set:
        if time.monotonic() >= self.next_scan:
            self._scan()
        held = set()
        for path, dev in list(self.devs.items()):
            fd, buttons, axes = dev
            try:
                while True:
                    data = os.read(fd, JS_EVENT.size * 32)
                    if not data:
                        break
                    for k in range(0, len(data) - JS_EVENT.size + 1, JS_EVENT.size):
                        _t, value, kind, num = JS_EVENT.unpack_from(data, k)
                        if kind & JS_BUTTON:
                            buttons[num] = value
                        elif kind & JS_AXIS:
                            axes[num] = value
            except BlockingIOError:
                pass
            except OSError:                     # unplugged
                try:
                    os.close(fd)
                except OSError:
                    pass
                del self.devs[path]
                continue
            held |= joystick_held(buttons, axes)
        return held

    def present(self) -> bool:
        return bool(self.devs)

    def close(self):
        for fd, *_x in self.devs.values():
            try:
                os.close(fd)
            except OSError:
                pass
        self.devs.clear()


def default_backend():
    try:
        if sys.platform == "win32":
            return XInputBackend()
        if sys.platform.startswith("linux"):
            return JoystickBackend()
    except OSError:
        return None
    return None


# ---------------------------------------------------------------------------
# Tk glue
# ---------------------------------------------------------------------------
class Navigator:
    """Polls a controller and listens to keys on `widget`'s window; calls callback(action).
    Only acts while the window has focus. stop() when the window goes away."""

    active = weakref.WeakSet()       # running navigators, so a window-wide one can leave others alone

    def __init__(self, widget, callback, backend="auto", keys=True):
        Navigator.active.add(self)
        self.widget = widget
        self.router = Router(callback)
        self.backend = default_backend() if backend == "auto" else backend
        self.held = set()
        self.since = {}               # action -> (pressed at, last repeat)
        self.running = True
        self.enabled = True
        if keys:
            top = widget.winfo_toplevel()
            self._bind = top.bind("<KeyPress>", self._on_key, add="+")
        self._after = None
        if self.backend is not None:
            self._after = widget.after(POLL_MS, self._tick)

    @property
    def controller_name(self):
        return getattr(self.backend, "name", "")

    def _focused(self):
        try:
            f = self.widget.focus_get()
            return f is not None and f.winfo_toplevel() is self.widget.winfo_toplevel()
        except (KeyError, Exception):  # noqa - focus in another app / a destroyed widget
            return False

    def _on_key(self, event):
        if not self.enabled:
            return None
        action = KEYS.get(event.keysym)
        if action is None:
            return None
        self.router.emit(action, "key")
        return "break"

    def press(self, held: set, now: float | None = None):
        """Process the set of held actions (from a backend poll)."""
        now = time.monotonic() if now is None else now
        for a in held - self.held:
            self.since[a] = (now, now)
            self.router.emit(a, "pad")
        for a in held & self.held:
            if a in REPEATING:
                t0, last = self.since.get(a, (now, now))
                if now - t0 >= REPEAT_DELAY and now - last >= REPEAT_EVERY:
                    self.since[a] = (t0, now)
                    self.router.emit(a, "pad", repeat=True)
        for a in self.held - held:
            self.since.pop(a, None)
        self.held = set(held)

    def _tick(self):
        if not self.running:
            return
        try:
            held = self.backend.poll() if self.enabled else set()
            if not self._focused():
                held = set()
            self.press(held)
        except Exception:  # noqa - a misbehaving pad must not break the window
            pass
        try:
            self._after = self.widget.after(POLL_MS, self._tick)
        except Exception:  # noqa - window closed
            self.running = False

    def stop(self):
        self.running = False
        Navigator.active.discard(self)
        if getattr(self, "_after", None):
            try:
                self.widget.after_cancel(self._after)
            except Exception:  # noqa - already gone
                pass
            self._after = None
        if self.backend is not None:
            self.backend.close()
