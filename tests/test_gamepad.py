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
print("test_gamepad OK")
