@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist "discord_token.env" (
  echo [錯誤] 找不到 discord_token.env,無法讀取 Discord 設定。
  exit /b 1
)

for /f "usebackq tokens=1,2 delims==" %%A in ("discord_token.env") do (
  set "%%A=%%B"
)

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if not exist ".venv\Scripts\python.exe" (
  echo [錯誤] 找不到 .venv,請先照 README 設定虛擬環境。
  exit /b 1
)

rem follow-up-watch —— 這支 .bat 給 Windows「工作排程器」每小時呼叫一次用,
rem 查詢到期的追蹤項目並主動發送提醒,不需要 start-pilot.bat 那個互動式
rem Discord session 開著也能運作。與 08:15/22:00 的 morning-brief/daily-close
rem 是各自獨立的排程 —— 不要把這三個合併成一個工作。
.venv\Scripts\python.exe -m pilot_agent.main follow-up-watch
