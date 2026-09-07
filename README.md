# AIagent Assistant Pilot

Henry 的簡易 Assistant Agent，目的不是建立完整平台，而是先讓 Henry 每天
真的會用，實際運行 1–2 個月，取得真實使用資料，再回頭驗證/修正正式
**AIagent Platform**（獨立 repository：`C:\Claude專案\AIanger`，M6R
runtime，目前凍結於 milestone 2）的架構判斷。

這個 repository 與 AIanger **完全隔離**：不 import 任何 AIanger 程式碼，
不共用資料庫，不影響 AIanger 的任何 branch/worktree。

## 架構

```
Discord（既有：plugin:discord@claude-plugins-official + Channels(experimental)）
   │  （現況限制：直接注入一個活的 Claude Code CLI session,
   │   無法在這層插入獨立 adapter 程式 —— 見 pilot_agent/adapters/discord_adapter.py）
   ▼
本 repository 的 Claude Code session（依 CLAUDE.md 指示,只做訊息轉發，
不用自己的一般能力處理 Discord 訊息內容）
   │  執行 python -m pilot_agent.main handle ...
   ▼
pilot_agent.main（唯一入口，決定性程式碼，不是活的對話）
   ├─ intake_classifier.py  → model_interface.py → providers/claude_provider.py
   │  （真正、獨立的 Anthropic API 呼叫，非活 session 的對話輪次本身，
   │   所以 token/cost/latency 是真實量測值，不是估計）
   └─ storage/interaction_repository.py → storage/sqlite_interaction_repository.py
      （schema/interaction.schema.json 全欄位，Henry 自己機器上的 SQLite）
```

## 為什麼這樣設計

- **Discord transport 完全重用既有的**：不重寫 bot，直接用你已經在用的
  官方 plugin + Channels。
- **Model Interface 是真正抽象層**：現在只接 Claude，之後要換
  GPT／其他 provider，只需要新增一個 `providers/*.py`，`intake_classifier.py`
  和 `main.py` 都不用動。
- **Storage 是真正抽象層**：現在是 SQLite，之後要遷移到你自己的私有
  server，只需要換 `storage/sqlite_interaction_repository.py` 這一個
  實作，介面（`InteractionRepository`）不變。
- **每次真實互動都走 `pilot_agent.main`（決定性程式碼），不是靠活 session
  自己記得要記錄**：這是為了讓資料記錄可靠，不依賴 LLM 自律。

## 已知限制（誠實記錄，不是要隱藏的缺陷）

- Discord ↔ Model 呼叫的分離，目前是靠 **CLAUDE.md 指示** 一個活的
  Claude Code session「只做轉發、不要自由發揮」，不是程式碼層級的
  強制隔離——因為 Channels (experimental) 目前的機制就是「注入一個
  完整、全權限的 CLI session」，沒有更細的 routing 可以插入。如果
  Channels 之後支援更細的 routing，這裡可以收得更緊。
- Token/cost/latency 只有在真的透過 `claude_provider.py` 呼叫 API 時才
  拿得到精確值；如果活 session 自己回覆而沒有呼叫 `pilot_agent.main`，
  就不會有這筆記錄——這正是 CLAUDE.md 要求「一定要呼叫,不要自由發揮」
  的原因。
- v1 不做任何 deterministic rule 或小模型分流（Step 6 明確要求：前期
  先求正確分類與真實使用行為，不是先省 token）。

## 安全性提醒

在稽核既有 Discord 整合時，發現 `C:\Claude專案\discord.txt` 是**明文存
放的 Discord bot token**。這與這個 Pilot repository 無關（不在這裡面，
也不會被這裡的任何檔案讀取或複製），但建議之後把它移出這種容易被看到
的共用資料夾，改用環境變數或至少是一個不會被誤 commit 的位置。這個
Pilot 分類呼叫改用已登入的 `claude` CLI 執行（見下方設定），不再需要
另外申請、儲存 ANTHROPIC_API_KEY。Discord bot token 仍只從環境變數
讀取，`.gitignore` 也擋掉任何 `.env`／`*token*.txt`／`*secret*` 檔案。

## 設定

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

分類呼叫透過 subprocess 呼叫 `claude -p ...`（已登入的 Claude Pro
session，不需要 ANTHROPIC_API_KEY），所以執行 `pilot_agent.main handle`
的環境必須能在 PATH 裡找到 `claude` 指令。

啟動一個綁定這個 repository、帶 `--channels` 的 Claude Code session
（實際指令依你 Discord channel 設定調整），CLAUDE.md 會自動生效。

## 資料

- `data/pilot.db`：SQLite，每一筆互動的完整記錄（`schema/interaction.schema.json`）。
  不進 git（見 `.gitignore`）——這是 Henry 自己機器上的資料，可匯出、
  可備份、可遷移，不綁死這個 repository 本身。
- `data/knowledge/`：純文字知識檔案，概念上與上面的 structured DB 分開。

## 手動查詢範例

```bash
.venv/Scripts/python.exe -m pilot_agent.main list-open --action-type todo
```

## 主動通知：08:15 今日行程 / 22:00 今日總結（2026-09-07 新增）

依 Henry 明確要求、參考規格書 SS5.1/5.3 精神，加了兩個排程指令：

```bash
.venv/Scripts/python.exe -m pilot_agent.main morning-brief   # 08:15 今日行程
.venv/Scripts/python.exe -m pilot_agent.main daily-close     # 22:00 今日總結
```

兩者都是**純粹查詢 `data/pilot.db`**（不呼叫 claude CLI、不花模型費用），組出訊息後透過
`pilot_agent/notifications.py` 直接用 Discord Bot REST API（`POST /channels/{id}/messages`）
發送到 `#assistant-pilot`——**不需要**那個互動式 `claude --channels` session 開著也能發送，
因為排程觸發的當下不一定有活的 session 在跑。

這不是重新做一個 Discord client：只送出，不接收；接收訊息仍然完全交給
`plugin:discord@claude-plugins-official` + Channels（見 `pilot_agent/adapters/discord_adapter.py`）。

需要的設定：
- `discord_token.env` 裡除了 `DISCORD_BOT_TOKEN`，多一行 `DISCORD_PILOT_CHANNEL_ID`
  （`#assistant-pilot` 的 Discord 頻道數字 ID）。
- 加 `--no-send` 只印出內容、不真的發送，方便手動測試：
  `.venv/Scripts/python.exe -m pilot_agent.main morning-brief --no-send`

**排程本身要用 Windows「工作排程器」手動設定**（見 repo 根目錄
`run-morning-brief.bat` / `run-daily-close.bat`，工作排程器設每天 08:15 / 22:00
分別執行這兩個 `.bat`）——這步驟目前沒有辦法從這個對話自動幫 Henry 完成，
Task Scheduler 是否已經設定、有沒有正常觸發，都需要 Henry 自己確認。

### 已知簡化（誠實記錄）

- Morning Brief／Daily Close 依賴新加的 `due_at` 欄位（規格書 SS4.1 的
  Planned Date／Deadline／Calendar Time／Reminder Time 四個獨立日期概念，
  v1 先合併成單一 `due_at`，不做四欄分開——這是刻意的簡化，不是遺漏）。
  `due_at` 只有在 Henry 訊息裡有明確或可合理推算的日期時才會被模型填入，
  沒有日期就是 null，不會被瞎猜。
- 22:00 Daily Close 目前是「還開著的事項快照」，不是規格書 SS5.3 原本設計的
  「只在真的有 Open Loop 卡在 Henry 身上才發送」那種判斷——Pilot v1 還沒有
  Open Loop 這個物件類型，`daily-close` 指令本身的輸出也會誠實註明這個簡化。
