# Emulator Save Manager

Keep all `.py` files in this folder together and run:

    python emu_save_manager.py        # Python 3.8+, no pip packages needed

(On Linux/Steam Deck desktop mode you may need the `python3-tk` / `tk` package.)

| File | Purpose |
|---|---|
| `emu_save_manager.py` | Main window: backup / restore for PCSX2, Xemu, DuckStation, RetroArch, Dolphin, custom |
| `ps2mc.py` | PS2 memory card engine (FAT, ECC, .psu) - no GUI, reusable |
| `ps2folder.py` | PCSX2 *folder* memory card engine (same interface as `ps2mc`) - no GUI |
| `ui_ps2cards.py` | PS2 Card Manager window |
| `cloudsync.py` | rclone wrapper using EmuDeck's folder layout, incl. EmuDeck Cloud - no GUI |
| `ui_cloud.py` | Cloud Sync window |

## PC <-> Steam Deck: EmuDeck Cloud or OneDrive
Cloud layout is the same as EmuDeck CloudSync: `<remote>:<base>/<emulator>/{saves,states}`.

**EmuDeck Cloud** (EmuDeck's own Patreon storage)
1. On the Deck, CloudSync must already be set up with EmuDeck Cloud.
2. PC: open **Cloud Sync...**, get rclone (Download rclone), click **Set up EmuDeck Cloud...** and paste
   your EmuDeck token (`Emulation/saves/.token` on the Deck; it looks like `12345---abc...`).
   Like EmuDeck, the app sends it to `cloud.emudeck.com` to get storage keys, saves those as the rclone
   remotes `Emudeck-cloud` (Backblaze B2) and `Emudeck-cloud2` (Wasabi), picks the one that holds your
   Deck's saves and fills in the base folder (`cs<id>/Emudeck/saves`). The token itself is not stored.
3. Run **Sync both ways** (tick *Preview only* first). After an upload a new `.hash` is written so the
   Deck's CloudSync notices the change and downloads it.

**OneDrive**: on the PC use **Set up OneDrive...**, pick the remote, base `EmuDeck/saves`.

Newer file wins, nothing is deleted. Overwritten local files go to `<backup folder>/cloud_overwritten/`.
EmuDeck's bookkeeping files (`.hash`, `.token`, `.last_upload`...) are never copied.
Tip: use PCSX2 *folder* memory cards (Settings > Memory Cards > Convert) so each game's save is its own
file; whole-card `.ps2` files can't be merged if two devices change the same card between syncs.

## PS2 Card Manager
Two cards side by side (left and right). Each side can be a card **file** (`Mcd001.ps2`) or a PCSX2
**folder memory card** (a `Mcd001.ps2` folder with one sub-folder per save), in any combination.
- **Copy / Move** saves between the cards with the arrow buttons, or drag them across
  (drag = copy, hold **Shift** while dropping = move). Multi-select with Ctrl/Shift-click or Ctrl+A.
  Moves are copied and verified on the target card (re-read from disk) before they are removed
  from the source, so an interrupted move never loses a save.
- **Cards**: create a blank 8 MB card file or a new (formatted) folder card, duplicate, rename, delete.
  Folder cards elsewhere can be opened with *Browse... > Folder memory card...*.
- Import / export `.psu`, delete saves, double-click a save for its file list. These act on the
  highlighted side (the one you last clicked).
- Name clashes ask once per operation: replace, skip, or cancel.

Close PCSX2 first. Nothing is ever erased outright - everything goes to `<backup folder>/_ps2_card_safety/`:
- card files: a copy of the card before each change (newest 10 per card kept);
- folder cards: the save folders that were replaced, deleted or moved away, one folder per change
  in `folder_cards/` (newest 10 per card kept);
- deleted folder cards: moved whole into `deleted_cards/` (never pruned).

Folder cards are written the way PCSX2 writes them (`_pcsx2_index` timestamps and file order,
`_pcsx2_meta*` for names Windows can't store or non-standard attributes). PCSX2 only loads the
running game's saves from a folder card, so the 8 MB limit applies per game, not to the whole card.
A folder card PCSX2 created but never formatted is formatted automatically when saves are added.

## Building installable packages (no Python needed on the target)
PyInstaller can't cross-compile, so each OS is built on that OS:
- **Windows**: run `build_windows.bat` -> `dist\EmuSaveManager-Setup.exe` (needs Inno Setup 6) and a portable folder.
- **Steam Deck / Linux**: run `build_linux.sh` -> `dist/EmuSaveManager-linux-x86_64.tar.gz`.
  On the Deck (Desktop Mode): extract it, run `./install.sh`. Uninstall with `./install.sh --remove`.
- **No build machine?** Push this folder to a GitHub repo; `.github/workflows/build.yml` builds both
  (Actions tab > Build packages > Run workflow, then download the artifacts).
