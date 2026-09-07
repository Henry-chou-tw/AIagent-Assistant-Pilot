# AIagent Assistant Pilot — session instructions

這個 repository 是 Henry 的 AIagent Assistant Pilot：一隻範圍受限、每天
真的會被使用的助理，目的是累積真實使用資料，之後回頭驗證正式 AIagent
Platform（獨立在 `C:\Claude專案\AIanger`，與這裡完全無關，不要 import、
不要修改）的架構判斷。這**不是**一個可以自由發揮的一般 coding assistant
session。

## 當這個 session 是被 Discord Channels 注入訊息時

**唯一該做的事**：把收到的訊息，原封不動當作 `--input` 參數，執行：

```
python -m pilot_agent.main handle --source discord --channel-ref <Discord 頻道/DM 的識別> --input "<Henry 的原始訊息>"
```

這個指令會自己完成：呼叫 Claude API 做分類與產生回覆、把完整互動記錄
寫進 `data/pilot.db`（Step 5 資料所有權要求的全部欄位）、印出結構化結果。

把指令印出的 `---` 之後那段文字，原樣送回 Discord（透過現有的
`plugin:discord:discord` 工具）。**不要自己重新想一個回覆**，也不要用
這個 session 自己的一般工具能力（改檔案、跑其他 shell 指令、瀏覽器操作
等）去處理 Discord 訊息本身的內容——那些能力保留給你（Henry）平常在這
個 session 裡做開發用的其他對話，不要跟 Pilot 的訊息處理邏輯混在一起。

## 如果 Henry 更正了分類

如果 Henry 明確表示「這個分類錯了」、「這應該是 XXX」之類的更正，執行：

```
python -m pilot_agent.main correct --id <剛剛那筆的 id> --correction "<Henry 說的更正內容原文>" [--final-action-type <9種之一>] [--final-domain "<領域>"]
```

## 如果 Henry 說某件事完成了/取消了

```
python -m pilot_agent.main close --id <id> --status done|cancelled|in_progress --outcome "<結果原文>"
```

## 一般規則

- 不確定 `--channel-ref` 該填什麼時，填能取得的最好識別值（頻道 ID、
  使用者名稱皆可），不要留白也不要虛構。
- `python -m pilot_agent.main handle` 執行失敗（例如 API 呼叫出錯）時，
  誠實告訴 Henry 系統暫時無法處理，不要假裝分類/回覆成功。
- 不要修改 `schema/interaction.schema.json`、`pilot_agent/models.py`
  或任何 storage 相關檔案來讓某次呼叫「看起來成功」。
- ANTHROPIC_API_KEY 從環境變數讀取；不要把任何 API key 寫進這個
  repository 的任何檔案。
