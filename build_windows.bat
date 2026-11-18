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
if not defined APP_VER set APP_VER=0.0.0
py -3 packaging\make_version_info.py %APP_VER% build\version_info.txt || goto :err
py -3 -m PyInstaller --noconfirm --clean --windowed --noupx --name EmuSaveManager --version-file build\version_info.txt ^
  --hidden-import ps2mc --hidden-import ps2folder --hidden-import cloudsync --hidden-import autosync --hidden-import exitwatch --hidden-import biosdb --hidden-import biosdb_data --hidden-import biosvault --hidden-import biostargets --hidden-import ui_biosvault ^
  --hidden-import ui_ps2cards --hidden-import ui_cloud --hidden-import savehistory --hidden-import ui_history --hidden-import syncplan ^
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
