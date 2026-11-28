"""Start-up waits out Tk's DLLs being briefly locked after an update (Windows), and still fails fast
for any other import error."""
import builtins, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import emu_save_manager as E

real = builtins.__import__
calls = {"n": 0}


def flaky(name, *a, **k):
    if name == "tkinter":
        calls["n"] += 1
        if calls["n"] <= 3:
            raise ImportError("DLL load failed while importing _tkinter: The process cannot access "
                              "the file because it is being used by another process.")
    return real(name, *a, **k)


builtins.__import__ = flaky
try:
    E._import_tkinter(wait=0.01)
    assert calls["n"] == 4, calls
    calls["n"] = -100                                    # locked for good: gives up after `tries`
    try:
        E._import_tkinter(tries=5, wait=0.01); raise SystemExit("should have failed")
    except ImportError:
        pass

    def broken(name, *a, **k):
        if name == "tkinter":
            calls["n"] += 1
            raise ImportError("No module named '_tkinter'")
        return real(name, *a, **k)
    builtins.__import__ = broken; calls["n"] = 0
    try:
        E._import_tkinter(wait=0.01); raise SystemExit("should have failed")
    except ImportError:
        assert calls["n"] == 1, "other errors aren't retried"
finally:
    builtins.__import__ = real
print("test_tk_start_retry OK")
