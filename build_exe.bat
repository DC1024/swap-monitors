@echo off
rem ============================================================
rem  Build a single-file Windows exe (dist\SwapMonitors.exe)
rem
rem  Requirements:
rem    - A python.org CPython 3.10-3.13 WITH tkinter
rem      (the Microsoft Store build works too)
rem    - Internet access for the first run (installs PyInstaller)
rem
rem  Usage:  double-click, or run  build_exe.bat  from cmd
rem
rem  NOTE: keep this file ASCII-only. cmd.exe reads batch files
rem  using the OEM codepage; non-ASCII characters can corrupt
rem  the parsing on non-UTF-8 consoles.
rem ============================================================
setlocal
set ROOT=%~dp0

rem --- 1. locate a suitable interpreter -----------------------
set PY=
rem 1a. honour an explicit override
if defined SWAPMONITORS_PY set PY=%SWAPMONITORS_PY%

rem 1b. the py launcher with a pinned version
if not defined PY (
  py -3.12 -c "import tkinter" >nul 2>&1 && set PY=py -3.12
)
if not defined PY (
  py -3 -c "import tkinter" >nul 2>&1 && set PY=py -3
)
rem 1c. PATH
if not defined PY (
  python -c "import tkinter" >nul 2>&1 && set PY=python
)
rem 1d. well-known per-user install
if not defined PY (
  if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not defined PY (
  if exist "%LOCALAPPDATA%\Python\pythoncore-3.12-64\python.exe" set PY="%LOCALAPPDATA%\Python\pythoncore-3.12-64\python.exe"
)

if not defined PY (
  echo [x] No Python with tkinter found.
  echo     Install one from https://www.python.org/downloads/windows/
  echo     then re-run this script, or set SWAPMONITORS_PY to its full path.
  exit /b 1
)
echo [i] Using interpreter: %PY%

rem --- 2. virtualenv ------------------------------------------
set VENV=%TEMP%\swapmonitors-venv
set MIRROR=%SWAPMONITORS_PIP_MIRROR%
if not defined MIRROR set MIRROR=https://pypi.org/simple

echo [1/3] Preparing build env: %VENV%
if not exist "%VENV%\Scripts\python.exe" %PY% -m venv "%VENV%" || exit /b 1
"%VENV%\Scripts\python.exe" -m pip install --quiet --upgrade pip -i %MIRROR% || exit /b 1
"%VENV%\Scripts\python.exe" -m pip install --quiet pyinstaller pillow -i %MIRROR% || exit /b 1

rem --- 3. icon ------------------------------------------------
echo [2/3] Generating app_icon.ico
"%VENV%\Scripts\python.exe" "%ROOT%make_icon.py" || exit /b 1

rem --- 4. freeze ----------------------------------------------
echo [3/3] Freezing with PyInstaller
set W=%TEMP%\swapmonitors-pyi
if exist "%W%" rmdir /s /q "%W%"
mkdir "%W%"
"%VENV%\Scripts\pyinstaller.exe" --noconfirm --clean --onefile --windowed ^
  --name "SwapMonitors" --icon "%ROOT%app_icon.ico" ^
  --add-data "%ROOT%app_icon.ico;." --hidden-import swap_windows ^
  --workpath "%W%\build" --specpath "%W%\spec" --distpath "%ROOT%dist" ^
  "%ROOT%swap_gui.py" || exit /b 1

echo.
echo [OK] %ROOT%dist\SwapMonitors.exe
dir /b "%ROOT%dist"
endlocal
