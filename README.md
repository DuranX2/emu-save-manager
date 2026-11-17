# Emulator Save Manager

Keep all `.py` files in this folder together and run:

    python emu_save_manager.py        # Python 3.8+, no pip packages needed

(On Linux/Steam Deck desktop mode you may need the `python3-tk` / `tk` package.)

| File | Purpose |
|---|---|
| `emu_save_manager.py` | Main window: backup / restore for PCSX2, Xemu, DuckStation, RetroArch, Dolphin, custom |
| `ps2mc.py` | PS2 memory card engine (FAT, ECC, .psu) - no GUI, reusable |
| `ui_ps2cards.py` | PS2 Card Manager window |
| `cloudsync.py` | rclone wrapper using EmuDeck's folder layout - no GUI |
| `ui_cloud.py` | Cloud Sync window |

## PC <-> Steam Deck via OneDrive
Cloud layout (same as EmuDeck CloudSync): `<remote>:EmuDeck/saves/<emulator>/{saves,states}`.
1. Steam Deck: enable CloudSync with OneDrive in EmuDeck.
2. PC: open **Cloud Sync...**, get rclone (Download rclone), **Set up OneDrive...**, pick the remote.
3. Run **Sync both ways** (tick *Preview only* first to see what would happen).

Newer file wins, nothing is deleted. Overwritten local files go to `<backup folder>/cloud_overwritten/`.
Tip: use PCSX2 *folder* memory cards (Settings > Memory Cards > Convert) so each game's save is its own
file; whole-card `.ps2` files can't be merged if two devices change the same card between syncs.

## PS2 Card Manager
Two cards side by side (left and right), each with its saves, free space and a usage bar.
- **Copy / Move** saves between the cards with the arrow buttons, or drag them across
  (drag = copy, hold **Shift** while dropping = move). Multi-select with Ctrl/Shift-click or Ctrl+A.
  Moves are copied and verified on the target card (re-read from disk) before they are removed
  from the source, so an interrupted move never loses a save.
- **Card files**: create a blank formatted 8 MB card, duplicate, rename, delete.
- Import / export `.psu`, delete saves, double-click a save for its file list. These act on the
  highlighted side (the one you last clicked).
- Name clashes ask once per operation: replace, skip, or cancel.

Close PCSX2 first. A safety copy of every card that is written to is made before each change
(`<backup folder>/_ps2_card_safety/`, newest 10 per card kept).

## Building installable packages (no Python needed on the target)
PyInstaller can't cross-compile, so each OS is built on that OS:
- **Windows**: run `build_windows.bat` -> `dist\EmuSaveManager-Setup.exe` (needs Inno Setup 6) and a portable folder.
- **Steam Deck / Linux**: run `build_linux.sh` -> `dist/EmuSaveManager-linux-x86_64.tar.gz`.
  On the Deck (Desktop Mode): extract it, run `./install.sh`. Uninstall with `./install.sh --remove`.
- **No build machine?** Push this folder to a GitHub repo; `.github/workflows/build.yml` builds both
  (Actions tab > Build packages > Run workflow, then download the artifacts).
