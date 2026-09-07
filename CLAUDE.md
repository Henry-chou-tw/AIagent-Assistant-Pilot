# AIagent Assistant Pilot — session instructions

這個 repository 是 Henry 的 AIagent Assistant Pilot：一隻範圍受限、每天
真的會被使用的助理，目的是累積真實使用資料，之後回頭驗證正式 AIagent
Platform（獨立在 `C:\Claude專案\AIanger`，與這裡完全無關，不要 import、
不要修改）的架構判斷。這**不是**一個可以自由發揮的一般 coding assistant
session。

## 當這個 session 是被 Discord Channels 注入訊息時

**唯一該做的事**：把收到的訊息，原封不動當作 `--input` 參數，執行
（務必用 `.venv/Scripts/python.exe`，不要用系統的 `python`，這個 repo
所有相依套件都已經裝在 `.venv` 裡，用系統 python 會缺套件失敗）：

```
.venv/Scripts/python.exe -m pilot_agent.main handle --source discord --channel-ref <Discord 頻道/DM 的識別> --input "<Henry 的原始訊息>"
```

這個指令會自己完成：透過 claude CLI 做分類與產生回覆、把完整互動記錄
寫進 `data/pilot.db`（Step 5 資料所有權要求的全部欄位）、印出結構化結果。

把指令印出的 `---` 之後那段文字，原樣送回 Discord（透過現有的
`plugin:discord:discord` 工具）。**不要自己重新想一個回覆**，也不要用
這個 session 自己的一般工具能力（改檔案、跑其他 shell 指令、瀏覽器操作
等）去處理 Discord 訊息本身的內容——那些能力保留給你（Henry）平常在這
個 session 裡做開發用的其他對話，不要跟 Pilot 的訊息處理邏輯混在一起。

## 如果 Henry 更正了分類

如果 Henry 明確表示「這個分類錯了」、「這應該是 XXX」之類的更正，執行：

```
.venv/Scripts/python.exe -m pilot_agent.main correct --id <剛剛那筆的 id> --correction "<Henry 說的更正內容原文>" [--final-action-type <9種之一>] [--final-domain "<領域>"]
```

## 如果 Henry 說某件事完成了/取消了

```
.venv/Scripts/python.exe -m pilot_agent.main close --id <id> --status done|cancelled|in_progress --outcome "<結果原文>"
```

## 如果 Henry 的訊息是在回覆一筆還開著的多階段追蹤（2026-09-07 新增）

有些 follow_up 是分階段的（例如：今天先追蹤「明天到底上午還是下午送」，
等 Henry 提供了這個資訊，才輪到追蹤「實際是否送達」）。如果你判斷 Henry
這則訊息明顯是在回覆一筆**最近的、還開著的 follow_up/reminder/todo**（可以
先用 `list-open --action-type follow_up` 之類的指令確認有沒有這種還開著
的項目、內容是否對得上),才執行：

```
.venv/Scripts/python.exe -m pilot_agent.main follow-up-advance --id <那筆還開著的 id> --outcome "<Henry 提供的新資訊，原文>" [--next-input "<下一階段要追蹤的內容>" --next-check-at <ISO 8601，只有 Henry 這次真的給了具體時間才填> --next-waiting-on henry|external]
```

這一步**判斷要謹慎，不確定就不要用**——不確定 Henry 這則訊息是不是在回覆
某筆舊的追蹤時，直接照平常流程跑 `handle` 當作一則新訊息即可，不要為了
硬要串起來而亂猜。`--next-check-at` 只有在 Henry/對方這次真的給了具體時間
才能填，沒有具體時間就留空，交給之後的 `follow-up-watch` 或 Daily Close
去處理，不要自己編。

## 主動通知 / 主動追蹤（2026-09-07 新增，都不是這個即時 session 的工作）

三支各自獨立、都是給 Windows「工作排程器」在固定時間呼叫的指令，直接查
資料庫、透過 Discord REST API 主動發送，**不經過**這個即時 Channels
session：

- `morning-brief`（08:15）/ `daily-close`（22:00）：**摘要層**，一天發一次。
- `follow-up-watch`（每小時）：**真正的追蹤到期檢查**，查 `next_check_at`
  到期的項目並主動提醒，有內建防洗版機制（連續提醒到上限後會自動停止，
  轉成 Daily Close 裡「需要你補資訊/決定」的項目）。不要把這支的責任跟
  前兩支混在一起——Morning Brief/Daily Close 不做到期檢查，到期檢查是這支
  的工作。

如果 Henry 在對話裡問「Morning Brief 怎麼還沒來」「這筆怎麼都沒提醒我」
之類的問題，那是排程本身（Windows 工作排程器有沒有設定好、有沒有正常
觸發這三個工作）的問題，不是這個 session 該處理的事，如實告訴 Henry 去確認
工作排程器狀態即可，不要自己嘗試在這個互動 session 裡「補發」一次充當
已經自動發生。

## 一般規則

- 不確定 `--channel-ref` 該填什麼時，填能取得的最好識別值（頻道 ID、
  使用者名稱皆可），不要留白也不要虛構。
- `.venv/Scripts/python.exe -m pilot_agent.main handle` 執行失敗（例如
  claude CLI 呼叫出錯）時，誠實告訴 Henry 系統暫時無法處理，不要假裝
  分類/回覆成功。如果失敗訊息是缺套件（ModuleNotFoundError），先確認
  是不是不小心用了系統 `python` 而不是 `.venv/Scripts/python.exe`，
  而不是急著重新安裝套件。
- 不要修改 `schema/interaction.schema.json`、`pilot_agent/models.py`
  或任何 storage 相關檔案來讓某次呼叫「看起來成功」。
- 分類呼叫透過已登入的 `claude` CLI 執行（2026-09-07 起，Henry 決定不用
  另外申請 ANTHROPIC_API_KEY，改直接借用 Claude Pro 的 `claude` 指令，
  見 pilot_agent/providers/claude_provider.py 開頭註解）；不要把任何
  API key 或密碼寫進這個 repository 的任何檔案。
- 分類/回覆用的 system prompt（2026-09-07 起）已經從 Henry 原始的
  Functional Specification v1.0（`C:\Claude專案\AIanger\1.GPT討論建置內容\...`，
  Pilot 不 import 那個 repo 的程式碼，但這段 prompt 內容是手動搬過來的）
  帶入回覆深度 R1/R2/R3、不擅自結案、due_at 不得瞎猜、誠實面對這個 Pilot
  系統自身能力邊界（例如目前收不到圖片/附件）這幾條規則——如果 Henry
  之後又覺得回覆「沒有 AI 感」，先去看這段 system prompt 有沒有被改動，
  不要假設問題出在分類邏輯。
- （2026-09-07 Follow-up Runtime Closure 這輪新增）`due_at` 跟
  `next_check_at` 是兩個不同的東西：`due_at` 是事情本身的時間（沒給就是
  null），`next_check_at` 是 Pilot 自己決定「下次該回頭確認」的時間。看到
  程式或資料裡這兩個欄位時不要混著改，也不要假設其中一個可以取代另一個。
  `waiting_on`（`"henry"` / `"external"` / null）標記卡在誰身上，是
  Daily Close 判斷「要不要真的發送」的依據，不要在不確定的情況下手動塞值
  進去讓它看起來已經分類過。
- （2026-09-07 Temporal Follow-up Reasoning Correction 這輪修正）分類模型
  不會直接輸出 `next_check_at` 的精確時間，只會輸出 `next_check_hint`
  （`"same_day"` / `"next_day"` / `"later"` / null），實際時間由
  `pilot_agent/followup_timing.py` 的固定策略算出來。看到「今天稍晚會
  補充」這類詞（稍後/晚點/待會/稍晚/馬上/等一下/今天再/今天會）要判斷成
  `"same_day"`，不能因為訊息裡同時出現「明天」字樣就誤判成 `"next_day"`
  ——這是這輪修正的真實案例（克靈固消毒劑），改壞這條規則等於重新引入
  原本的錯誤。
