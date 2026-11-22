@echo off
rem Builds EmuSaveManager for Windows: a self-contained folder (no Python needed on the target)
rem and, if Inno Setup 6 is installed, an installer: dist\EmuSaveManager-Setup.exe
rem Requires Python 3.8+ with tkinter (the python.org installer includes it) on THIS machine only.
setlocal
cd /d "%~dp0"

rem Compile PyInstaller's launcher from source (needs Visual Studio's C++ build tools): the prebuilt
rem one is shared by many programs and often trips antivirus heuristics. Set QUICK=1 to skip that.
if defined QUICK (
  py -3 -m pip install --upgrade pyinstaller || goto :err
) else (
  set PYINSTALLER_COMPILE_BOOTLOADER=1
  py -3 -m pip uninstall -y pyinstaller
  py -3 -m pip install --no-cache-dir --no-binary=pyinstaller "pyinstaller>=6.10,<7" || goto :err
)
py -3 -m pip install "pillow>=10" || goto :err
if not defined APP_VER set APP_VER=0.0.0
py -3 packaging\make_version_info.py %APP_VER% build\version_info.txt || goto :err
rem the version the update check compares against
>build\app_version.txt <nul set /p "=%APP_VER%"
py -3 -m PyInstaller --noconfirm --clean --windowed --noupx --name EmuSaveManager --version-file build\version_info.txt --icon packaging\art\emu-save-manager.ico --add-data "packaging\art;art" --add-data "build\app_version.txt;." ^
  --hidden-import ps2mc --hidden-import ps2folder --hidden-import cloudsync --hidden-import autosync --hidden-import exitwatch --hidden-import biosdb --hidden-import biosdb_data --hidden-import biosvault --hidden-import biostargets --hidden-import ui_biosvault ^
  --hidden-import ui_ps2cards --hidden-import ui_cloud --hidden-import savehistory --hidden-import ui_history --hidden-import syncplan --hidden-import steamshortcut --hidden-import romlibrary --hidden-import libqueue --hidden-import ui_library --hidden-import libmeta --hidden-import ui_covers --hidden-import updater --hidden-import ui_update --hidden-import saveinfo --hidden-import titledb --hidden-import ui_saves --hidden-import ps2icon --hidden-import gamepad --hidden-import ui_ps2console --hidden-import ui_console --hidden-import gcfolder --hidden-import ui_gccards --hidden-import ps1card --hidden-import ui_ps1cards --hidden-import wiinand --hidden-import ui_wiisaves --hidden-import safecopy --hidden-import pspsaves --hidden-import ui_pspsaves --hidden-import rasaves --hidden-import raconvert --hidden-import ui_rasaves --hidden-import PIL.Image --hidden-import PIL.PngImagePlugin --hidden-import PIL.JpegImagePlugin ^
  emu_save_manager.py || goto :err

set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe
if defined ISCC (
  "%ISCC%" packaging\installer.iss || goto :err
  echo.
  echo Installer: dist\EmuSaveManager-Setup.exe
) else (
  echo.
  echo Inno Setup not found - skipping installer. Install it from jrsoftware.org to get a Setup.exe.
)
echo Portable build: dist\EmuSaveManager\EmuSaveManager.exe
exit /b 0

:err
echo Build failed.
exit /b 1
