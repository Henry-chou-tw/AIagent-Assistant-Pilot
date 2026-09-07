# Pilot Discord 整合 — 設定完成紀錄（交接用）

給另一個 AI 助理（例如 ChatGPT/GPT）或未來的自己看的交接文件：說明
2026-09-07 這一輪對話裡，Henry 的 AIagent Assistant Pilot 專案（位於
`C:\Claude專案\AIagent-Assistant-Pilot`）完成了哪些設定、目前狀態、以及
還沒做的事，避免重複問已經確認過的問題。

## 背景架構（沒有改變的部分）

- 這個 repo **不**自己寫 Discord client。Discord 收發訊息全部透過官方
  Claude Code plugin `plugin:discord@claude-plugins-official` + Claude
  Code 的 `--channels` 實驗性功能：Discord 訊息直接注入一個用
  `claude --channels ...` 啟動的即時 CLI session。
- 那個即時 session 收到訊息後，依照 repo 根目錄 `CLAUDE.md` 的指示，唯一
  該做的事是呼叫 `.venv/Scripts/python.exe -m pilot_agent.main handle
  --source discord --channel-ref <id> --input "<原文>"`，把印出結果的
  `---` 之後那段文字原樣回傳到 Discord，不自己重新生成回覆、不用其他
  工具處理訊息內容本身。
- `pilot_agent.main` 這個 Python CLI 才是真正做分類、呼叫模型、寫入
  `data/pilot.db` 的地方（`pilot_agent/intake_classifier.py` →
  `pilot_agent/providers/claude_provider.py`）。

## 這輪對話完成的設定

### 1. 獨立的 Discord Bot（跟 Henry 平常開發用的 Bot 完全分開）

- 新建立 Discord Application/Bot，名稱「亨利的首席秘書」（App ID
  `1546400290809315379`，Bot username `亨利的首席秘書#9199`）。
- 已開啟 **Message Content Intent**（Presence Intent / Server Members
  Intent 維持關閉，不需要）。
- Bot 已透過 OAuth2 URL Generator 加入伺服器 **Henry AI Agent**，只勾選
  最小權限：檢視頻道、傳送訊息、讀取訊息歷史記錄（沒有管理類權限）。

### 2. 頻道隔離

- `#assistant-pilot` 頻道已設為「私人頻道」，存取名單只有：
  - 身分組「亨利的首席秘書」（= 這個 Bot 專屬身分組）
  - 成員 Henry（伺服器擁有者）
- 伺服器其他頻道、其他成員都看不到、碰不到這個頻道，也看不到這個 Bot
  （因為 Bot 本身在其他頻道也沒有被加入視圖）。

### 3. Token 存放方式（刻意不用明文 txt）

- Discord bot token 存在 `discord_token.env`（repo 根目錄），格式
  `DISCORD_BOT_TOKEN=...`。這個檔名符合 `.gitignore` 的 `*.env` 規則，
  **不會被 git 追蹤或不小心 push 上去**。
- 不用 Claude Code 的 `/discord:configure`（會寫到全電腦共用的
  `~/.claude/channels/discord/.env`），避免跟 Henry 平常開發用的那個
  Bot 的設定互相覆蓋。

### 4. 一鍵啟動腳本 `start-pilot.bat`

位於 repo 根目錄，雙擊即可：

1. 從 `discord_token.env` 讀取 `DISCORD_BOT_TOKEN` 並 set 成環境變數
2. 設定 `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8`（見下方編碼修正）
3. `chcp 65001`（讓 batch 自己的中文 echo 訊息在主控台正確顯示）
4. 執行 `claude --channels plugin:discord@claude-plugins-official`

### 5. 模型呼叫方式改成 CLI，不用另外的 API Key

- 原本 `pilot_agent/providers/claude_provider.py` 是直接呼叫 Anthropic
  API，需要 `ANTHROPIC_API_KEY`（另外的計費帳號）。
- **Henry 明確決定**改成透過已登入的 `claude` CLI（Claude Pro 訂閱）用
  `subprocess` 呼叫 `claude -p ... --output-format json`，不用另外申請
  付費的 API key。
- 取捨：不再有 Anthropic API 原始 usage 回傳的精確 token 數/成本數字
  （`--output-format json` 解不到就留 `None`，不瞎猜），但 `latency_ms`
  仍然是本機量測、準確。
- `requirements.txt` 已移除 `anthropic` 套件依賴，只剩 `jsonschema`。
- `CLAUDE.md`、`README.md` 已同步更新說明，不再提 ANTHROPIC_API_KEY。

### 6. 兩個已修好的編碼 bug（Windows + 中文的典型陷阱）

1. `subprocess.run()` 讀 `claude` CLI 的 stdout 時，沒指定 encoding，
   在 Henry 的 Windows 中文語系下用系統預設 `cp950` 解碼 UTF-8 輸出失敗。
   → 已在 `claude_provider.py` 明確加上 `encoding="utf-8",
   errors="replace"`。
2. 透過主控台傳遞含中文的 `--input` 參數時，若沒有 `PYTHONUTF8=1` /
   `PYTHONIOENCODING=utf-8`，Windows 主控台編碼可能造成後續處理不穩定。
   → 已寫進 `start-pilot.bat`，每次啟動都固定帶這兩個環境變數，不再
   靠運氣。

（註：曾經懷疑某一筆資料庫紀錄整個是亂碼，實際用 Python 直接讀
`data/pilot.db` 逐字檢查後**確認內容其實正常、沒有亂碼**，只是主控台
畫面顯示曾經看起來像亂碼；這點已經跟 Henry 澄清過，記錄在此避免之後
又被誤會成資料損毀。）

### 7. `.venv` 固定路徑，避免「這次有裝下次沒裝」

- 第一次執行時，即時 session 曾自建 `.venv` 並安裝 `jsonschema`，但因為
  `CLAUDE.md` 原本寫的是模糊的 `python`，重開一個新 session 後又用回
  系統 python、找不到套件。
- 已把 `CLAUDE.md` 三處指令都改成明確指定
  `.venv/Scripts/python.exe -m pilot_agent.main ...`，確保每次都用同一個
  已經裝好套件的虛擬環境。

### 8. 資料清理

- 測試階段產生的兩筆重複測試紀錄（`interaction-dee5373f68ab` /
  `interaction-d271d2c7b680`，都是同一句「測試:這是一則待辦事項…」）已
  依 Henry 指示從 `data/pilot.db` 刪除。
- 刪除過程中一度因為這台裝置的檔案刪除權限限制，SQLite commit 失敗、
  留下 `pilot.db-journal`；已取得刪除權限、清掉殘留 journal、確認乾淨
  commit 成功，資料庫沒有損毀。
- 目前 `data/pilot.db` 只剩真實使用資料，其中已經有兩筆是 Henry 實際
  透過 Discord 傳的真實訊息（供應商追蹤、以及對 Bot 回覆的追問），
  分類與回覆品質看起來正常。

## 資料留在哪裡（隱私相關，Henry 有明確問過）

- `data/pilot.db` 只存在 Henry 本機（`C:\Claude專案\AIagent-Assistant-Pilot\data\`），
  被 `.gitignore` 排除，不會隨 git push 外流。
- 訊息內容會經過 Discord 伺服器（傳輸）與 Anthropic API（呼叫模型分類/
  回覆時），但結構化的互動記錄、狀態追蹤只存在本機資料庫。

## 還沒完成 / 待確認的事項

- **開機自動啟動**：因為 Windows 的「開機啟動」資料夾
  （`shell:startup`）不在這個 session 能存取的授權資料夾內，沒辦法
  自動幫 Henry 建立捷徑，已經口頭教學 Henry 自己拖曳
  `start-pilot.bat` 到開機啟動資料夾建立捷徑——**這一步尚未收到 Henry
  確認已完成**，之後接手的人/AI 應該先跟 Henry 確認這件事有沒有做完，
  不要假設已經設定好。
- 目前沒有做「資料匯出/備份成 Excel、CSV」，Henry 有被告知需要時可以
  再另外做。

## 給接手者的建議

- 這份文件本身**不要被當成程式邏輯的來源**，程式碼實際行為以
  `CLAUDE.md`、`pilot_agent/` 底下的原始檔案為準；這份文件只是這一輪
  對話做了什麼事的摘要說明。
- 如果 Henry 提到「Pilot 又壞了」、「又要重新設定 API key」之類的話，
  先確認他是不是又用回舊的直接呼叫 Anthropic API 的認知（已經改掉了），
  或是不是 `.venv` 路徑 / PYTHONUTF8 環境變數又出了什麼新狀況，而不是
  照本輪對話之前的舊假設處理。
