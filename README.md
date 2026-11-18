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
| `biosdb.py`, `biosdb_data.py` | identify BIOS files by content (PS2 version record, libretro fingerprints) - no GUI |
| `biosvault.py`, `biostargets.py` | BIOS Vault: store, cloud sync, find emulator BIOS folders, install - no GUI |
| `ui_biosvault.py` | BIOS Vault window |
| `tools/build_bios_db.py` | regenerates `biosdb_data.py` from libretro's System.dat |
| `autosync.py` | background two-way sync (`--sync`) and its Windows / systemd scheduling - no GUI |
| `exitwatch.py` | sync after an emulator or ES-DE closes (`--watch`) and starting it at sign-in - no GUI |
| `savehistory.py` | Save History: earlier versions of synced saves, kept at every sync; restore - no GUI |
| `ui_history.py` | Save History window |

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
5. Optionally tick **Also sync right after an emulator closes** and/or **EmulationStation (ES-DE) closes**.
   A small watcher (`EmuSaveManager --watch`) then runs from sign-in - a per-user Run entry on Windows,
   a systemd user service on the Steam Deck - and syncs about 15 seconds after the program closes
   (waiting for any other emulator to close first). On the Deck, "ES-DE closes" means quitting
   EmulationStation in Game Mode; on the PC, "an emulator closes" covers emulators started any way.

How conflicts are handled: newest file wins, nothing is ever deleted. Files a download replaces go to
`<backup folder>/cloud_overwritten/`; files an upload replaces go to `<base parent>/EmuSaveManager-overwritten/`
in the cloud (OneDrive's own version history is there as well). A background sync is skipped while PCSX2,
DuckStation, RetroArch, Dolphin or xemu is running, and only one sync runs at a time.
Use PCSX2 *folder* memory cards (Settings > Memory Cards > Convert) so each game's save is its own file;
a whole-card `.ps2` file changed on both devices between syncs keeps only the newer copy (the other is backed up).

Command line (what the scheduler runs): `EmuSaveManager --sync` (add `--dry-run` to preview,
`--download` / `--upload` for one direction, `--force` to sync even while an emulator runs).

On the Steam Deck the app finds EmuDeck's `Emulation/saves/<emulator>/...` folders automatically.

**Empty files never replace real ones.** If the copy on one side is empty (0 bytes) and the other side's file has content, the sync keeps the real one and says so in the log, whichever date is newer. New empty files (some games use them) still sync.

## Save History
Every sync keeps earlier versions of the synced saves on that device: once before the sync (what you changed
by playing there) and once after it (what arrived from the other device). Only changed files are stored, and
identical contents only once, in `<backup folder>/history/`. **Save History...** in the main window lists each
file's versions; **Restore this version** puts one back, and **Restore folder...** puts a whole folder back the
way it was at a given time (use this for PCSX2 folder memory cards). A restored file gets the current date,
so the next sync uploads it and the other device picks it up; the version it replaced is kept in the history
first, so a restore can be undone. Defaults: 10 versions per file, at most 2 GB in total (oldest go first,
the newest version of a file is never dropped), files over 256 MB (Xemu's hard disk) are skipped.

**EmuDeck Cloud** works too: **Set up EmuDeck Cloud...** uses the EmuDeck app's Patreon sign-in on that
computer, picks Backblaze (`Emudeck-cloud`) or Wasabi (`Emudeck-cloud2`) and the `cs<id>/Emudeck/saves`
folder, and writes a new `.hash` after uploads so EmuDeck's own CloudSync notices the change.

## BIOS Vault
Your own BIOS files, identified by their contents, kept in one place, synced between devices
and installed into every emulator. The app never downloads BIOS files from anywhere except
your own cloud drive; dump them from consoles you own.

- **Add files / Add folder**: files, folders and zip archives. Each file is identified by content:
  PS2 by reading the BIOS's own version record (as PCSX2 does: region, version, date, truncated
  dumps), everything else by libretro's fingerprint list (also gives the file names RetroArch cores
  expect), Xbox MCPX by the hashes in xemu's documentation. A folder laid out by system
  (`Sony - PlayStation 2/...`) keeps its layout; folder sets such as `Sega - Dreamcast/dc/` or
  blueMSX's `Machines/` are kept exactly as they are.
- **Vault layout**: `~/.emu_save_manager/bios_vault/<System>/<file>`; the folder is the system, so
  "Move to system..." is all it takes to place an unknown file.
- **This device**: finds each emulator's BIOS folder - PCSX2 (`[Folders] Bios` in PCSX2.ini),
  DuckStation, RetroArch (`system_directory`), xemu (the paths in xemu.toml), Dolphin
  (`GC/<USA|EUR|JAP>/IPL.bin`) and EmuDeck's `Emulation/bios` - and shows what is in place.
  **Install** copies what is missing; nothing that is already there is replaced (a different file
  with the same name is reported as a conflict). Unknown and damaged files are never installed.
  **Remove what the app installed** moves only its own copies to the backup folder.
- **Cloud**: "Sync vault with cloud" copies the vault both ways with `Emulator BIOS Vault` at the
  top of your cloud drive (newest wins, nothing deleted). Tick the two boxes to include the vault in
  automatic sync and to install new files automatically - a new Steam Deck then gets everything on
  its next sync.
- Command line: `EmuSaveManager --bios-scan <folder>` lists what every file is (changes nothing).

Fingerprints: `biosdb_data.py` is generated from libretro's
[System.dat](https://github.com/libretro/libretro-database) (CC BY-SA 4.0) and shared under the
same licence; it contains hashes and names only.

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
