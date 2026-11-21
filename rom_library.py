"""ROM Library: a central ROM library on a network share or drive; check games out to this machine
and back in.  Companion to the Emulator Save Manager (saves sync separately, so they're never lost
when a game is checked in)."""
import sys


def main(argv=None):
    import ui_library
    ui_library.main()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
