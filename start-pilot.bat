@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist "discord_token.env" (
  echo [錯誤] 找不到 discord_token.env,請先照指示建立這個檔案並貼上 token。
  pause
  exit /b 1
)

for /f "usebackq tokens=1,2 delims==" %%A in ("discord_token.env") do (
  set "%%A=%%B"
)

rem 2026-09-07 fix: force UTF-8 everywhere Python touches this process
rem (console codepage cp950 was mangling Chinese --input args and CLI output).
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

echo 正在啟動 Pilot Discord 連線...
claude --channels plugin:discord@claude-plugins-official
pause
