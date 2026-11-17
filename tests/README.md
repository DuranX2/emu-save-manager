# Tests

Plain scripts, no framework. Each prints `... OK` on success and raises on failure.
Use a Python that has Tkinter (the CI/dev box used 3.12). Run from anywhere (they find the app folder themselves). Needs `rclone` on PATH for the
cloud tests; the `gui_*.py` scripts need a display (on Linux: `xvfb-run -a python3 gui_x.py`).

    for t in tests/test_*.py; do python3 "$t" | tail -1; done
    for g in tests/gui_*.py; do xvfb-run -a python3 "$g" | tail -1; done

They use throw-away folders under /tmp and never touch real saves or settings
(HOME is pointed at a temp folder). Screenshots from gui_* go to /tmp/*.png.
