"""Controller input: XInput button/stick mapping, the Linux joystick device (events through a pipe),
held directions repeating, other buttons not repeating, and one press arriving as both a key
and a button counting once."""
import os, struct, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import gamepad as G

# 1. XInput
assert G.xinput_held(0x1000, 0, 0) == {"ok"}
assert G.xinput_held(0x0001 | 0x4000, 0, 0) == {"up", "mark"}
assert G.xinput_held(0, -30000, 0) == {"left"} and G.xinput_held(0, 0, 30000) == {"up"}
assert G.xinput_held(0, 5000, -5000) == set()                         # dead zone
assert G.xinput_held(0x0100 | 0x0200 | 0x0010, 0, 0) == {"prev", "next", "view"}
print("1. XInput: A/B/X/Y, shoulders, Start, D-pad and stick (with a dead zone): ok")

# 2. Linux joystick device through a pipe
r, w = os.pipe()
js = G.JoystickBackend(pattern="/nonexistent/js*")
js.add_fd(r)
ev = lambda kind, num, value: os.write(w, G.JS_EVENT.pack(0, value, kind, num))
ev(G.JS_BUTTON | G.JS_INIT, 0, 0)                                      # initial state events
ev(G.JS_AXIS, 7, -32767)                                               # D-pad up
assert js.poll() == {"up"}
ev(G.JS_AXIS, 7, 0); ev(G.JS_BUTTON, 0, 1)                             # release, press A
assert js.poll() == {"ok"}
ev(G.JS_BUTTON, 0, 0); ev(G.JS_AXIS, 0, 32000)                         # stick right
assert js.poll() == {"right"}
ev(G.JS_AXIS, 0, 0); ev(G.JS_BUTTON, 4, 1); ev(G.JS_BUTTON, 7, 1)
assert js.poll() == {"prev", "view"}
os.close(w)
js.poll()                                                               # closed: no crash
js.close()
print("2. Linux joystick events (D-pad hat, A, stick, shoulder, Start): ok")

# 3. Navigator: presses, repeats, de-duplication
got = []


class FakeWidget:
    def winfo_toplevel(self): return self
    def bind(self, *a, **k): return "id"
    def after(self, *a): pass
    def focus_get(self): return self


nav = G.Navigator(FakeWidget(), got.append, backend=None)
nav.press({"down"}, now=10.0)
nav.press({"down"}, now=10.2)                    # still held, too soon to repeat
nav.press({"down"}, now=10.4)                    # repeat starts after the delay
nav.press({"down"}, now=10.45)
nav.press({"down"}, now=10.52)
nav.press(set(), now=10.6)
assert got == ["down", "down", "down"], got
got.clear()
nav.press({"ok"}, now=20.0); nav.press({"ok"}, now=21.0); nav.press(set(), now=21.1)
assert got == ["ok"]                              # buttons don't repeat
got.clear()
clock = [100.0]
nav.router.clock = lambda: clock[0]


class Key:
    def __init__(self, k): self.keysym = k


nav._on_key(Key("Return"))                         # Steam sends Enter …
clock[0] += 0.03
nav.router.emit("ok", "pad")                       # … and the button, for one press
assert got == ["ok"], got
clock[0] += 0.5
nav._on_key(Key("Return"))
assert got == ["ok", "ok"]
assert nav._on_key(Key("F2")) == "break" and got[-1] == "view"
assert nav._on_key(Key("z")) is None
print("3. held directions repeat, buttons don't, and a key + button pair counts once: ok")

# 4. the right stick scrolls and LT is "context" (XInput, and the joystick API with its guards)
import struct as _st
assert G.xinput_held(0, 0, 0, 30000, 0) == {"scroll_up"}
assert G.xinput_held(0, 0, 0, -30000, 0) == {"scroll_down"}
assert G.xinput_held(0, 0, 0, 5000, 100) == set()             # small push, light trigger
assert G.xinput_held(0, 0, 0, 0, 200) == {"context"}
H = G.joystick_held
assert H({}, {4: -30000}, centred={4}) == {"scroll_up"} and H({}, {4: 30000}, centred={4}) == {"scroll_down"}
assert H({}, {4: -32767}, centred=set()) == set(), "an axis never seen centred (a trigger at rest) doesn't scroll"
assert H({}, {2: -32767}, rest_low={2}) == set() and H({}, {2: 20000}, rest_low={2}) == {"context"}
assert H({}, {2: 20000}, rest_low=set()) == set(), "a stick on the trigger's axis doesn't open menus"
assert H({}, {3: -30000}, roles={"ry": 3}, centred={3}) == {"scroll_up"}, "the driver's axis map decides"
# through a real joystick event stream: init events (trigger at rest), then the stick and LT
r_fd, w_fd = os.pipe()
be = G.JoystickBackend(pattern="/nonexistent/js*")
be.add_fd(r_fd)
ev = lambda num, val, kind: _st.pack("<IhBB", 0, val, kind, num)
os.write(w_fd, ev(2, -32767, 0x82) + ev(4, 0, 0x82))                 # JS_INIT | JS_AXIS
assert be.poll() == set()
os.write(w_fd, ev(4, -32000, 0x02))
assert be.poll() == {"scroll_up"}
os.write(w_fd, ev(4, 0, 0x02) + ev(2, 32000, 0x02))
assert be.poll() == {"context"}
be.close(); os.close(w_fd)
# scrolling repeats sooner and faster than the D-pad
got.clear()
for t in (30.0, 30.10, 30.15, 30.20, 30.26, 30.31):
    nav.press({"scroll_down"}, now=t)
nav.press(set(), now=30.4)
assert got.count("scroll_down") == 4, got
print("4. right stick scrolls, LT is the context button, with guards for other axis layouts: ok")
print("test_gamepad OK")
