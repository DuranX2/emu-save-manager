@echo off
rem Builds EmuSaveManager for Windows: a self-contained folder (no Python needed on the target)
rem and, if Inno Setup 6 is installed, an installer: dist\EmuSaveManager-Setup.exe
rem Requires Python 3.8+ with tkinter (the python.org installer includes it) on THIS machine only.
setlocal
cd /d "%~dp0"

py -3 -m pip install --upgrade pyinstaller || goto :err
py -3 -m PyInstaller --noconfirm --clean --windowed --name EmuSaveManager ^
  --hidden-import ps2mc --hidden-import ps2folder --hidden-import cloudsync --hidden-import autosync ^
  --hidden-import ui_ps2cards --hidden-import ui_cloud ^
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
