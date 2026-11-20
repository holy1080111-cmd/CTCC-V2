@echo off
setlocal
if not exist "%~dp0..\runtime\Scripts\pythonw.exe" (
  echo CTCC is not installed in this release directory.
  echo Install the reviewed release before opening the tool.
  pause
  exit /b 1
)
start "" "%~dp0..\runtime\Scripts\pythonw.exe" -I -B "%~dp0scripts\desktop_windows_tool_0058_v1.py"
exit /b 0
