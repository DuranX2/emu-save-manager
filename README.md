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
| `autosync.py` | background two-way sync (`--sync`) and its Windows / systemd scheduling - no GUI |

## PC <-> Steam Deck: two-way sync through OneDrive (or EmuDeck Cloud)
Install the app on both (Windows: `EmuSaveManager-Setup.exe`; Steam Deck: see *Building* below), then on
**each** device open **Cloud Sync...**:
1. **Download rclone**, then **Set up OneDrive...** (a terminal opens `rclone config`; sign in in the browser).
2. Pick the OneDrive remote and use the **same base folder on both devices** (default `EmuDeck/saves`;
   any name works, e.g. `Emulator Saves`). Each emulator folder goes to `<base>/<emulator>/{saves,states}`,
   so the cloud copy is easy to browse and manage in OneDrive.
3. **Sync both ways** once with *Preview only* ticked, then for real.
4. Tick **Automatic sync** (every 5-120 min). It keeps running while the app is closed:
   a Task Scheduler task on Windows, a systemd user timer on the Steam Deck (Desktop and Game Mode,
   catches up after sleep). Log: `~/.emu_save_manager/logs/autosync.log`.

How conflicts are handled: newest file wins, nothing is ever deleted. Files a download replaces go to
`<backup folder>/cloud_overwritten/`; files an upload replaces go to `<base parent>/EmuSaveManager-overwritten/`
in the cloud (OneDrive's own version history is there as well). A background sync is skipped while PCSX2,
DuckStation, RetroArch, Dolphin or xemu is running, and only one sync runs at a time.
Use PCSX2 *folder* memory cards (Settings > Memory Cards > Convert) so each game's save is its own file;
a whole-card `.ps2` file changed on both devices between syncs keeps only the newer copy (the other is backed up).

Command line (what the scheduler runs): `EmuSaveManager --sync` (add `--dry-run` to preview,
`--download` / `--upload` for one direction, `--force` to sync even while an emulator runs).

On the Steam Deck the app finds EmuDeck's `Emulation/saves/<emulator>/...` folders automatically.

**EmuDeck Cloud** works too: **Set up EmuDeck Cloud...** uses the EmuDeck app's Patreon sign-in on that
computer, picks Backblaze (`Emudeck-cloud`) or Wasabi (`Emudeck-cloud2`) and the `cs<id>/Emudeck/saves`
folder, and writes a new `.hash` after uploads so EmuDeck's own CloudSync notices the change.

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
