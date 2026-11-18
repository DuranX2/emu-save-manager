#!/usr/bin/env bash
# Installs Emulator Save Manager for the current user (no root needed) - works on Steam Deck.
#   ./install.sh            install / update (asks whether to add it to Steam for Game Mode)
#   ./install.sh --steam    install / update and add it to Steam without asking
#   ./install.sh --no-steam install / update only
#   ./install.sh --remove   uninstall, also from Steam (your settings and backups are kept)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
DEST="$DATA/EmuSaveManager"
APPS="$DATA/applications"
ICONS="$DATA/icons/hicolor"
DESKTOP="$APPS/emu-save-manager.desktop"
SIZES="16 32 48 64 128 256"

if [ "${1:-}" = "--remove" ]; then
  # take it out of the Steam library first (needs the app itself)
  if [ -x "$DEST/EmuSaveManager" ]; then "$DEST/EmuSaveManager" --remove-from-steam || true; fi
  # stop the automatic sync timer, if it was switched on
  systemctl --user disable --now emu-save-manager-sync.timer emu-save-manager-watch.service >/dev/null 2>&1 || true
  rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/emu-save-manager-sync."{timer,service} \
        "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/emu-save-manager-watch.service"
  systemctl --user daemon-reload >/dev/null 2>&1 || true
  rm -rf "$DEST" "$DESKTOP" "$HOME/.local/bin/emu-save-manager" "$HOME/Desktop/Emulator Save Manager.desktop"
  for s in $SIZES; do rm -f "$ICONS/${s}x${s}/apps/emu-save-manager.png"; done
  command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" || true
  echo "Removed. Settings/backups remain in ~/.emu_save_manager"
  exit 0
fi

[ -x "$HERE/app/EmuSaveManager" ] || { echo "Run this from the extracted folder (app/ next to install.sh)."; exit 1; }
rm -rf "$DEST"; mkdir -p "$DEST" "$APPS" "$HOME/.local/bin"
cp -r "$HERE/app/." "$DEST/"
chmod +x "$DEST/EmuSaveManager"
ln -sf "$DEST/EmuSaveManager" "$HOME/.local/bin/emu-save-manager"

# icon for the application menu and the desktop shortcut
ART=""
for d in "$DEST/_internal/art" "$DEST/art"; do [ -f "$d/icon-256.png" ] && ART="$d" && break; done
ICON=emu-save-manager
if [ -n "$ART" ]; then
  for s in $SIZES; do
    mkdir -p "$ICONS/${s}x${s}/apps"
    cp "$ART/icon-$s.png" "$ICONS/${s}x${s}/apps/emu-save-manager.png"
  done
  command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$ICONS" 2>/dev/null || true
  ICON="$ICONS/256x256/apps/emu-save-manager.png"      # full path: works on every desktop
fi

cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Emulator Save Manager
Comment=Back up, restore and cloud-sync emulator saves
Exec="$DEST/EmuSaveManager"
Icon=$ICON
Terminal=false
Categories=Game;Utility;
EOF
chmod +x "$DESKTOP"
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" || true

# Steam Deck: also put a shortcut on the Desktop (Desktop Mode).
if [ -d "$HOME/Desktop" ]; then cp "$DESKTOP" "$HOME/Desktop/Emulator Save Manager.desktop"; chmod +x "$HOME/Desktop/Emulator Save Manager.desktop"; fi

echo "Installed to $DEST"
echo "Start it from the application menu (Games or Utilities) or the Desktop shortcut."

# Game Mode: a Steam library entry with artwork
if [ "${1:-}" != "--no-steam" ] && { [ -d "$HOME/.local/share/Steam/userdata" ] || [ -d "$HOME/.steam/steam/userdata" ]; }; then
  ans=y
  if [ "${1:-}" != "--steam" ]; then
    if [ -t 0 ]; then
      read -r -p "Add it to your Steam library so it shows in Game Mode (with artwork)? Steam closes and restarts for a moment. [Y/n] " ans || ans=n
    else
      ans=n
      echo "To add it to Game Mode later: $DEST/EmuSaveManager --add-to-steam"
    fi
  fi
  case "${ans:-y}" in
    [Yy]*|"") "$DEST/EmuSaveManager" --add-to-steam --yes || echo "Adding to Steam didn't work - run: $DEST/EmuSaveManager --add-to-steam" ;;
    *) echo "Skipped. To add it later: $DEST/EmuSaveManager --add-to-steam" ;;
  esac
fi
