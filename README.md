# Emulator Save Manager

Keep all `.py` files in this folder together and run:

    python emu_save_manager.py        # Python 3.8+, no pip packages needed

(On Linux/Steam Deck desktop mode you may need the `python3-tk` / `tk` package.)

| File | Purpose |
|---|---|
| `emu_save_manager.py` | Main window: backup / restore for PCSX2, Xemu, DuckStation, Dolphin, RetroArch, Yuzu, Azahar, melonDS, PPSSPP, RPCS3, Xenia, PrimeHack, Cemu, custom |
| `ps2mc.py` | PS2 memory card engine (FAT, ECC, .psu) - no GUI, reusable |
| `ps2folder.py` | PCSX2 *folder* memory card engine (same interface as `ps2mc`) - no GUI |
| `ui_ps2cards.py` | PS2 Card Manager window |
| `ui_console.py` | the shared parts of the console-style card managers (look, controls, in-window menus and questions) |
| `ps1card.py` | PS1 memory cards: read, verified copy / move, delete, export (.mcs), blank cards - no GUI |
| `ui_ps1cards.py` | the PS1 Card Manager window |
| `wiinand.py` | Wii saves in Dolphin's NAND and their backups: banner.bin, verified folder copies - no GUI |
| `ui_wiisaves.py` | the Wii Save Manager window |
| `gcfolder.py` | GameCube saves in Dolphin's GCI folders: list, verified copy / move, delete, export - no GUI |
| `ui_gccards.py` | the GameCube Card Manager window |
| `ui_ps2console.py` | the PS2 Card Manager's console view (controller-friendly, 3D save icons) |
| `ps2icon.py` | PS2 save icons: `icon.sys`, the 3D icon format, and a Pillow renderer - no GUI |
| `gamepad.py` | controller (XInput / Linux joystick) and keyboard navigation for Tk windows |
| `cloudsync.py` | rclone wrapper using EmuDeck's folder layout, incl. EmuDeck Cloud - no GUI |
| `ui_cloud.py` | Cloud Sync window |
| `biosdb.py`, `biosdb_data.py` | identify BIOS files by content (PS2 version record, libretro fingerprints) - no GUI |
| `biosvault.py`, `biostargets.py` | BIOS Vault: store, cloud sync, find emulator BIOS folders, install - no GUI |
| `ui_biosvault.py` | BIOS Vault window |
| `tools/build_bios_db.py` | regenerates `biosdb_data.py` from libretro's System.dat |
| `autosync.py` | background two-way sync (`--sync`) and its Windows / systemd scheduling - no GUI |
| `exitwatch.py` | sync after an emulator or ES-DE closes (`--watch`) and starting it at sign-in - no GUI |
| `steamshortcut.py` | Steam Deck: adds the app to the Steam library (Game Mode) with artwork - no GUI |
| `syncplan.py` | decides what each sync copies: remembers the last synced state, conflicts, folder-card saves as one item - no GUI |
| `savehistory.py` | Save History: earlier versions of synced saves, kept at every sync; restore - no GUI |
| `ui_history.py` | Save History window |
| `saveinfo.py` | works out which game and which save each file belongs to (GameCube/Wii/PSP/PS3/PS1 metadata and icons, state file names, title-ID folders) - no GUI |
| `titledb.py` | game names for IDs from the emulators' own lists, Cemu's title cache and ROM headers - no GUI |
| `ui_saves.py` | the main window's save list, grouped by game |
| `updater.py` / `ui_update.py` | update check and installer hand-off / the update bar and windows |

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

Supported emulators and the EmuDeck cloud folders they sync to: PCSX2 (`pcsx2/saves`, `pcsx2/states`),
DuckStation (`duckstation/…`), Dolphin (`dolphin/GC`, `dolphin/Wii`, `dolphin/states`), RetroArch
(`retroarch/…`), Xemu (`xemu`, off by default), Yuzu (`yuzu/saves`, `yuzu/profiles`), Azahar
(`azahar/saves` = its SD card folder, `azahar/states`), melonDS (`melonds/…`), PPSSPP (`ppsspp/saves`,
`ppsspp/states`), RPCS3 (`rpcs3/saves`, `rpcs3/trophy`), Xenia (`xenia/saves`, its content folder
without DLC, title updates and installed games), PrimeHack (`primehack/GC`, `primehack/Wii`,
`primehack/states`) and Cemu (`Cemu/saves`, its `mlc01/usr/save` folder). With EmuDeck these are the `Emulation/saves/<emulator>/`
folders on both Windows and the Steam Deck, so both devices meet in the same cloud folders.

How changes are decided: after every sync the app remembers what each item looked like on both sides
(`~/.emu_save_manager/sync_state/`). Next time, the side that **changed** wins, even if its date is older.
If **both** sides changed the same item since the last sync, nothing is copied either way: it is listed
under **Conflicts** in Cloud Sync until you choose "Keep this device's" or "Keep the cloud's". Items never
synced before fall back to "the newer copy wins". Inside a PCSX2 *folder* memory card (`<name>.ps2/`
folder) each game's save folder is one item that is always copied whole, so a save can't end up half from
each device; a card that is a file on one device and a folder on the other is reported once and skipped.
Nothing is ever lost: files a download replaces go to `<backup folder>/cloud_overwritten/`, files an upload
replaces go to `<base parent>/EmuSaveManager-overwritten/` in the cloud (OneDrive's own version history is
there as well). A background sync is skipped while PCSX2, DuckStation, RetroArch, Dolphin or xemu is
running, and only one sync runs at a time. Use PCSX2 *folder* memory cards (Settings > Memory Cards >
Convert) so each game's save syncs on its own.

Command line (what the scheduler runs): `EmuSaveManager --sync` (add `--dry-run` to preview,
`--download` / `--upload` for one direction, `--force` to sync even while an emulator runs).

On the Steam Deck the app finds EmuDeck's `Emulation/saves/<emulator>/...` folders automatically.

**rclone 1.66 or newer for OneDrive.** Older rclone (EmuDeck shipped 1.60 on the Steam Deck) can list and download from OneDrive but every upload fails with "unauthenticated" - only empty files get through. For a OneDrive remote the app uses a new-enough rclone found on the device, or downloads the current one into `~/.emu_save_manager/rclone`, leaving EmuDeck's copy alone.

**Empty files never replace real ones.** If the copy on one side is empty (0 bytes) and the other side's file has content, the sync keeps the real one and says so in the log, whichever date is newer. New empty files (some games use them) still sync.

**Manual syncs take over.** Pressing a sync button while a background sync runs stops the background sync (cleanly, between files) and runs yours. A lock left by a sync that died, or from before a restart, is cleared straight away. The Cloud Sync window shows the result in a coloured banner (green: complete, orange: some steps failed) and fits small screens such as the Steam Deck's: the connection settings fold into one line once they're set up.

## Steam Deck Game Mode
`./install.sh` on the Deck installs the app with its icon (application menu and Desktop shortcut) and offers
to add it to your Steam library with its own artwork (library tile, wide tile, banner and logo), so it can
be found and started in Game Mode under *Non-Steam*. Steam has to close for a moment while its shortcut
list changes; the installer closes it and starts it again. Run `./install.sh --steam` to do it without the
question, or later: `~/.local/share/EmuSaveManager/EmuSaveManager --add-to-steam`
(`--remove-from-steam` takes it out; `./install.sh --remove` does too). Steam's `shortcuts.vdf` is backed up
next to itself before every change. The artwork is in `packaging/art/` (`tools/make_art.py` draws it).
In Game Mode, use the touchscreen or the trackpad as the mouse, and Steam + X for the keyboard.

## Readable save lists
The main window groups each emulator's files by save and shows the **game's name, the save's own
description and its icon**, with each file's role underneath (save data, save information, icon,
screenshot, save state slot…). Select a save to see its banner and details below the list; backing
up a selected save takes all its files. Untick **Group by game** for the plain file list.
- **Read from the saves themselves:** GameCube (`.gci` and whole-card `.raw` images: game ID, both
  comment lines, banner, icon), Wii (`banner.bin`: title, subtitle, icon), PSP and PS3
  (`PARAM.SFO` and `ICON0.PNG`), PS3 trophies, and PS1 cards (each save's product code, title and icon).
- **Named from IDs:** save states (Dolphin, PPSSPP, DuckStation, PCSX2, Azahar) and title-ID folders
  (Switch, 3DS, Wii U, Xbox 360). Names come from what's already on your machine, never downloaded:
  the lists Dolphin, PCSX2 and DuckStation ship with, Cemu's `title_list_cache.xml`, the headers of
  your ROMs in EmuDeck's `Emulation/roms` (GameCube/Wii discs, 3DS, Switch, Wii U, PS3), and names
  seen in your saves. They're cached in `~/.emu_save_manager/titles.json`.

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

### Console view
**Console view (F2)** turns the manager into a full-window, controller-friendly screen: each save's
own 3D icon (from its icon files, lit and coloured by its `icon.sys`) in a grid, and the selected
save spinning on its own background colours with its two-line title, size and date. The two cards
are slots 1 and 2. It's an original design in the spirit of a console's memory card screen (no
console artwork, fonts or sounds).
- **Controller** (Xbox-style; on the Steam Deck through Steam): D-pad / stick to move, **A** options
  (copy or move to the other slot, delete, export, details), **X** mark several saves, **Y** menu
  (choose the card in each slot, refresh, list view, close), **B** back, **LB / RB** switch slot,
  **Start (☰)** back to the list view.
- **Keyboard**: arrows, Enter, X, Tab, Esc, Q / E, F2. The mouse works too.
- Copies, moves and deletes use the same verification and safety copies as the list view; the
  questions are asked inside the window. Exports go to `<backup folder>/PS2 exports/`.
  Creating, renaming and deleting card files, and importing `.psu` files, are in the list view.
- The view you used last is remembered.

## PS1 Card Manager
**Memory Cards → PS1 Card Manager…** opens a console-style, controller-friendly window for
PlayStation memory card files: DuckStation's cards (`.mcd`, per-game and shared) and RetroArch's
`.srm` cards, plus **Exported saves** (`<backup folder>/PS1 exports/`, one `.mcs` file per save,
the format DuckStation exports and imports). Each save shows its animated icon, its own title, the
game it belongs to (from the game-name lookup), its region and a map of the card's 15 blocks.
- **A** options: copy or move to the other slot, delete, export a copy to the backups, details.
  **X** marks, **Y** menu (choose each slot, create a new blank card, open the folder, refresh,
  close), **B** back, **LB / RB** switch slot. Keyboard and mouse work too.
- Cards are written the way DuckStation writes them (directory entries, links and checksums). Every
  change writes a new card image, reads it back, then replaces the card; the card as it was is
  copied first to `<backup folder>/_ps1_card_safety/` (newest 20 changes kept). Copies are verified by
  reading the card back. Deleting marks the save deleted, as the console does. Damaged saves are
  shown with the problem and never copied. `.gme`/`.vgs` cards keep their headers; PSP `.vmp` cards
  are read only. Close DuckStation / RetroArch first.

## Wii Save Manager
**Memory Cards → Wii Save Manager…** opens a console-style, controller-friendly window for the game
saves in Dolphin's (or PrimeHack's) emulated Wii system memory (`Wii/title/<type>/<id>/data`), with
**Backups** (`<backup folder>/Wii saves/`, one folder per game) playing the part of the SD card.
Each save shows its animated icon and, when selected, its banner, title, subtitle and size in Wii
blocks. System software (IOS, the System Menu, system channels) is never listed.
- **A** options: back up / restore / move to the other slot, delete, details. **X** marks, **Y** menu
  (choose each slot, including PrimeHack's system memory, open the folder, refresh, close),
  **B** back, **LB / RB** switch slot.
- Whole save folders are copied: staged next to their destination, every file read back and
  compared, then swapped in. Replaced, moved-away and deleted saves go to
  `<backup folder>/_wii_safety/` (newest 20 changes kept). Backups are plain copies of Dolphin's
  folders: no Wii console keys are used (the Wii's own SD-card format is encrypted, so it isn't
  offered). Dolphin accepts restored folders as they are. Close Dolphin first.

## GameCube Card Manager
**Memory Cards → GameCube Card Manager…** opens a console-style, controller-friendly window for
Dolphin's memory cards (Dolphin's default "GCI folder" cards: `GC/<region>/Card A` and `Card B`,
one `.gci` file per save). Each save shows its own **animated icon** (every frame, with the game's
timing) and its **banner**, with its two comment lines, size, date and game ID.
- Two slots: any Card A / Card B of any region, or **Exported saves** (`<backup folder>/GameCube exports/`).
- **A** options: copy or move to the other slot, delete, export a copy to the backups, details.
  **X** marks several saves, **Y** menu (choose each slot, open the folder, refresh, close), **B** back,
  **LB / RB** switch slot. Keyboard and mouse work too (same keys as the PS2 console view).
- Copies are verified (written to a temporary file, read back, then put in place). Nothing is erased
  outright: replaced, moved-away and deleted saves go to `<backup folder>/_gc_card_safety/` (newest 20
  changes kept). Saves the game marked "no copy" / "no move" aren't copied / moved between cards
  (exporting a backup is always allowed). Copying to another region's card asks first.
- Close Dolphin first. Whole-card `.raw` images aren't supported here yet.

## ROM Library (preview)

A second app that comes with the save manager: keep your whole ROM collection in one place (a drive
shared from another computer, a NAS, or any drive) and **check games out** to whichever machine you're
playing on, then **check them in** when you're done to free the space. Open it from the Start menu or
the application menu (**ROM Library**), or run `EmuSaveManager --rom-library`.

- **The library** is a folder laid out like EmuDeck's ROM folder: one folder per system (`ps2`, `gc`,
  `xbox`...), games inside. Copy your existing `Emulation/roms` folder there as it is.
- **Where it can be:** on Windows, a network path such as `\\NUC\Games\roms` (signs in with Windows'
  saved login); on the Steam Deck, *Network share* in Settings signs in with a username and password
  (kept scrambled in the app's own rclone config). A local or mounted drive works too.
- **Check out** copies a game into this machine's ROM folder (`Emulation/roms/<system>`), so ES-DE and
  Steam ROM Manager find it. Transfers queue up, show progress, and resume if the app is closed part-way.
  There's a space check first, and 2 GB is always left free.
- **Check in** removes the local copy, but only after checking the library has an identical copy.
  Games that are only on this machine can be copied up with **Add to library**. Saves are separate
  (the save manager syncs them), so checking a game in never touches your progress.
- **Every machine sees the same library.** Favourites, and which machine has which game checked out,
  are kept in a `.library` folder inside the library. The app never renames, moves or deletes anything
  else in the library.
- Disc sets (`.m3u` + `.cue`/`.bin`), PS3 and Wii U game folders, and category subfolders are each
  treated as one game.
- **Covers and game information.** The library opens as a grid of box art (or a list, if you prefer),
  with a details panel: cover, release date, genre, players, developer, description and a screenshot.
  **Artwork…** fills in what's missing, from these sources in order:
  - **ES-DE on this machine:** anything you've already scraped in ES-DE (its gamelists and
    `Emulation/tools/downloaded_media`). Run it on the machine where you scraped.
  - **ScreenScraper:** your screenscraper.fr account plus a developer ID (request one on their forum).
    Both are kept only on that machine, in `~/.emu_rom_library/screenscraper.json`, never in the library.
    The daily limit is respected, and games with no match aren't asked about again for 30 days.
    If a game is matched wrong, **Wrong game? Find the right one…** lets you pick it.
  - **libretro thumbnails:** free box art and screenshots, no account needed.

  Everything found is stored once in the library's `.library` folder (covers shrunk to a sensible
  size), so every machine shows the same artwork. Only artwork and text are downloaded, never games.

## Updates
Once a day the app asks GitHub for the latest release (only the version number and release notes
are fetched). When there's a newer one, a bar at the top of the main window offers **What's new**,
**Update** and **Skip this version**. Nothing is downloaded or installed until you click Update:
- **Windows (installed with Setup.exe):** the new Setup.exe is downloaded, checked against its
  SHA-256, and runs silently over the current install; the app then starts again by itself. Running
  copies (the ROM Library, the sync-on-exit watcher) are closed first, and the watcher starts again.
- **Steam Deck / Linux:** the new package is downloaded and checked, and its `install.sh` runs once
  the app has closed (your Steam library entry is kept); then the app starts again.
  The log is in `~/.emu_save_manager/updates/update.log`.
- **Windows portable zip:** Update opens the release page instead, since there's nothing to install.

**Updates…** at the bottom of the window shows your version, checks now, and turns the daily check
off. Copies run from source (and test builds) don't check.

## Building installable packages (no Python needed on the target)
PyInstaller can't cross-compile, so each OS is built on that OS:
- **Windows**: run `build_windows.bat` -> `dist\EmuSaveManager-Setup.exe` (needs Inno Setup 6) and a portable folder.
- **Steam Deck / Linux**: run `build_linux.sh` -> `dist/EmuSaveManager-linux-x86_64.tar.gz`.
  On the Deck (Desktop Mode): extract it, run `./install.sh`. Uninstall with `./install.sh --remove`.
- **No build machine?** Push this folder to a GitHub repo; `.github/workflows/build.yml` builds both
  (Actions tab > Build packages > Run workflow, then download the artifacts).

## Licence
MIT - see [LICENSE](LICENSE). `biosdb_data.py` is derived from libretro's database and stays under
CC BY-SA 4.0, as its header says.
