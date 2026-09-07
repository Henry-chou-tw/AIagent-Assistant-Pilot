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

## 主動通知與主動追蹤（2026-09-07 新增，2026-09-07 Follow-up Runtime Closure 這輪擴充）

三個各自獨立的排程指令,依 Henry 明確要求、參考規格書 SS5.1/5.3 精神:

```bash
.venv/Scripts/python.exe -m pilot_agent.main morning-brief    # 08:15 今日行程（摘要層）
.venv/Scripts/python.exe -m pilot_agent.main daily-close      # 22:00 今日總結（摘要層，條件式發送，見下）
.venv/Scripts/python.exe -m pilot_agent.main follow-up-watch  # 每小時,真正的追蹤到期檢查
```

三者都是**純粹查詢 `data/pilot.db`**（不呼叫 claude CLI、不花模型費用），組出訊息後透過
`pilot_agent/notifications.py` 直接用 Discord Bot REST API（`POST /channels/{id}/messages`）
發送到 `#assistant-pilot`——**不需要**那個互動式 `claude --channels` session 開著也能發送，
因為排程觸發的當下不一定有活的 session 在跑。

這不是重新做一個 Discord client：只送出，不接收；接收訊息仍然完全交給
`plugin:discord@claude-plugins-official` + Channels（見 `pilot_agent/adapters/discord_adapter.py`）。

### `due_at` 跟 `next_check_at` 是兩個不同的欄位

`due_at` 是這件事「本身」的時間（截止日期／行程／提醒時間），只有 Henry 訊息裡有明確或
可合理推算的日期時才會被填入，沒有就是 null，絕不瞎猜。`next_check_at` 是 Pilot **自己**
決定「下次該回頭檢查/提醒」的時間——一個排程上的操作決定，不是對現實世界的事實陳述。

**分類模型不會直接算出 `next_check_at` 的精確時間**（2026-09-07 Temporal Follow-up
Reasoning Correction 這輪修正）：模型只判斷一個**分類** `next_check_hint`
（`"same_day"` / `"next_day"` / `"later"` / null），實際時間由
`pilot_agent/followup_timing.py` 的固定策略（deterministic policy）算出來，不讓模型自己
做時間運算——這樣算出來的時間才是可重現、可測試的，也才不會讓模型把「不確定」偷偷腦補成一個
看起來很篤定的時間點：

- `"same_day"`：訊息暗示**今天稍後**會有新資訊（「稍後」「晚點」「待會」「稍晚」「馬上」
  「等一下」「今天再」「今天會」「晚一點」「稍後補充」這類詞）→ 現在時間 +2.5 小時左右
  （若超過當天 21:00 才觸發，往回收斂到當天內，絕不跨到隔天）。
- `"next_day"`：訊息**明確講明天**才會有結果 → 隔天 09:00。
- `"later"`：更久以後（下週、改天、還沒定日期）→ 不排程，留 null，交給 Daily Close 的
  「尚未排時間」列出，不用猜。
- `null`：這則不需要之後主動追蹤。

即使 `due_at` 因為資訊不足留 null（例如「廠商明天親送,確切時段稍後補充」），`next_check_hint`
仍然可以合理判斷成 `"same_day"`——因為「今天稍晚該不該回頭問」跟「事情本身幾點發生」是兩回事。
**「明天親送」跟「稍後補充」是同一則訊息裡兩個不同的時間線索，不要因為訊息裡出現「明天」字樣
就把 `next_check_hint` 誤判成 `"next_day"`**——這正是這輪修正的真實案例（見下方克靈固消毒劑
案例）。

### `follow-up-watch`：真正的到期檢查（不是用固定時間取代追蹤）

`morning-brief` / `daily-close` 只是**摘要層**,一天各發一次;真正「這筆追蹤到期了,該去
問一下」的判斷,由 `follow-up-watch` 負責,設計成每小時被 Windows「工作排程器」呼叫一次:

- 查詢 `next_check_at <= now` 且狀態仍是 open/in_progress 的項目
- 每次觸發送出一則提醒,並記錄 `last_checked_at` / `reminder_count`
- **內建防洗版機制**：同一筆最多自動提醒 3 次(每次間隔至少 6 小時,不會被每小時的排程
  逼著連環發),超過上限後自動停止追蹤(`next_check_at` 清空),改標記
  `waiting_on = "henry"`,交給 Daily Close 的「需要你補資訊/決定」區塊繼續曝光,而不是
  無限期每小時打擾
- 沒有到期項目時**完全不發送任何訊息**(靜默才是正確行為)

### 多階段追蹤（例如：克靈固消毒劑案例）

像「廠商回覆經理明日會親送,確切上午/下午稍後補充,幫我持續追蹤」這種需要分階段確認的
follow-up,不會硬塞進單一 `due_at`/`next_check_at`,而是用既有的 `related_interaction_id`
欄位串接**多筆 Interaction 記錄**,一筆代表一個階段:

```bash
.venv/Scripts/python.exe -m pilot_agent.main follow-up-advance \
  --id <目前這筆追蹤的 id> \
  --outcome "廠商確認今天下午 3 點送達" \
  --next-input "確認克靈固消毒劑是否已送達" \
  --next-due-at 2026-09-08T15:00:00+08:00 \
  --next-check-at 2026-09-08T15:30:00+08:00 \
  --next-waiting-on external
```

這個指令會把「目前這筆」標記為已解決(`task_status` 依 `--close-parent-status`,預設
`done`)並清空它的 `next_check_at`(停止 watch 對它繼續觸發),然後(如果有給
`--next-input`)建立一筆新的、`related_interaction_id` 指向原記錄的子記錄,代表下一個
階段,擁有自己獨立的 `due_at`/`next_check_at`/`waiting_on`。`follow-up-watch` 完全不需要
知道「階段」這個概念——它只看到另一筆有自己 `next_check_at` 的開啟中記錄。

需要的設定：
- `discord_token.env` 裡除了 `DISCORD_BOT_TOKEN`,多一行 `DISCORD_PILOT_CHANNEL_ID`
  (`#assistant-pilot` 的 Discord 頻道數字 ID)。
- 三支指令都支援 `--no-send`(`follow-up-watch` 會印出原本要發的提醒文字,不是靜默略過),
  方便手動測試：
  `.venv/Scripts/python.exe -m pilot_agent.main follow-up-watch --no-send`

**排程本身要用 Windows「工作排程器」手動設定**(見 repo 根目錄
`run-morning-brief.bat` / `run-daily-close.bat` / `run-follow-up-watch.bat`,工作排程器
設每天 08:15 / 22:00 / **每小時**分別執行這三個 `.bat`)——這步驟目前沒有辦法從這個對話
自動幫 Henry 完成,Task Scheduler 是否已經設定、有沒有正常觸發,都需要 Henry 自己確認。

### 已知簡化（誠實記錄）

- Morning Brief／Daily Close／follow-up-watch 依賴新加的 `due_at` 欄位（規格書 SS4.1 的
  Planned Date／Deadline／Calendar Time／Reminder Time 四個獨立日期概念，
  v1 先合併成單一 `due_at`，不做四欄分開——這是刻意的簡化，不是遺漏）。
  `due_at` 只有在 Henry 訊息裡有明確或可合理推算的日期時才會被模型填入，
  沒有日期就是 null，不會被瞎猜。
- 22:00 Daily Close(2026-09-07 這輪起)已經會依 `waiting_on` 區分「等外部」跟「卡住你」,
  只有真的有事項落在「需要你補資訊/決定」或「尚未分類」桶時才會實際送 Discord;純粹
  「等外部」的項目只會出現在訊息內容裡,不會單獨觸發發送。但這仍然不是規格書 SS5.3 原本
  設計的完整 Open Loop 物件類型(沒有獨立 schema object,`waiting_on` 只是 null/henry/external
  三值,不是完整的封鎖原因分類)——Pilot v1 先用這個最小可行版本,`daily-close` 指令本身的
  輸出也會誠實註明這個簡化。
- 多階段追蹤（`follow-up-advance`）目前**不會自動判斷**哪個 Discord 回覆對應到哪一筆開著
  的追蹤——這個判斷交給即時 Channels session 用自身的自然語言理解去決定要不要呼叫
  `follow-up-advance`（見 `CLAUDE.md`），Pilot 本身沒有對話串（thread）追蹤機制。如果
  session 判斷錯誤或漏判，這筆追蹤就會停在原本的階段，需要 Henry 自己發現並手動用
  `correct` / `follow-up-advance` 修正。
- `follow-up-watch` 的重試間隔（6 小時）跟自動提醒上限（3 次）目前是寫死的常數
  （`pilot_agent/follow_up_watch.py` 的 `RETRY_INTERVAL` / `MAX_AUTO_REMINDERS`），還沒有
  依 domain/緊急程度做差異化，也還沒有真實使用數據可以校準這兩個數字合不合理。
- `next_check_hint` 的「同日待補資訊」判斷（`pilot_agent/followup_timing.py`）目前只認
  中文口語裡幾種常見講法（稍後/晚點/待會/稍晚/馬上/等一下/今天再/今天會/晚一點/稍後補充），
  也還沒有真實使用數據驗證這份線索詞清單夠不夠涵蓋 Henry 實際會用的講法；`SAME_DAY_INTERVAL_HOURS`
  （2.5 小時）、`SAME_DAY_CUTOFF_HOUR`（21:00）、`NEXT_DAY_CHECK_HOUR`（09:00）這幾個常數
  也是先訂的合理預設值，還沒被真實數據校準過。
