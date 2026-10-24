@echo off
rem Double-click to start Resonance on Windows. First run sets everything up.
cd /d "%~dp0"
py -3 --version >nul 2>nul && (py -3 resonance\launch.py %* & goto done)
python --version >nul 2>nul && (python resonance\launch.py %* & goto done)
echo Python 3 is not installed. Install Python 3.12 from https://www.python.org/downloads/
echo (tick "Add python.exe to PATH"), then double-click this file again.
:done
pause
