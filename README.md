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
Pilot 自己的 `ANTHROPIC_API_KEY` 一律只從環境變數讀取，`.gitignore`
也擋掉任何 `.env`／`*token*.txt`／`*secret*` 檔案。

## 設定

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...   # Windows: set ANTHROPIC_API_KEY=...
```

啟動一個綁定這個 repository、帶 `--channels` 的 Claude Code session
（實際指令依你 Discord channel 設定調整），CLAUDE.md 會自動生效。

## 資料

- `data/pilot.db`：SQLite，每一筆互動的完整記錄（`schema/interaction.schema.json`）。
  不進 git（見 `.gitignore`）——這是 Henry 自己機器上的資料，可匯出、
  可備份、可遷移，不綁死這個 repository 本身。
- `data/knowledge/`：純文字知識檔案，概念上與上面的 structured DB 分開。

## 手動查詢範例

```bash
python -m pilot_agent.main list-open --action-type todo
```
