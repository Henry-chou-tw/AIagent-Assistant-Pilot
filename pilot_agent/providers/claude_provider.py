"""ModelInterface implementation that shells out to the already-authenticated
`claude` CLI (Henry's existing Claude Pro subscription) in headless/print
mode, instead of calling the Anthropic API directly with a separate
ANTHROPIC_API_KEY.

--- Why this changed (2026-09-07, Henry's explicit decision) ---
The original version of this file called the Anthropic API directly and
required ANTHROPIC_API_KEY, specifically so Step 5/6 could record clean,
per-call token/cost/latency numbers for a single isolated invocation
(never piggybacking on the live Discord Channels session's own broad
conversational context). When Henry hit the ANTHROPIC_API_KEY requirement
during live testing, he was offered that trade-off explicitly -- pay for
a separate API key to keep exact token/cost numbers, or reuse the `claude`
CLI he already has via Claude Pro and give up exact token/cost accounting
-- and chose the latter (see conversation for 2026-09-07).

This keeps the "one fresh, isolated invocation per message" shape (still
a brand-new `claude` subprocess per call, not reusing the live Channels
session's own context/history) by calling `claude -p` in non-interactive
mode. It tries `--output-format json` first to recover cost/duration
figures the CLI itself reports; if that output shape ever changes or
isn't parseable, it falls back to treating stdout as plain response text
and honestly leaves cost/token fields as None (Pilot's own rule: never
fabricate a number, leave it null if it isn't really known -- see
model_interface.py's ModelResult and the original pricing-table comment
this file used to have).

Latency (latency_ms) is still measured locally via wall-clock time around
the subprocess call, so that figure stays real regardless of whether the
CLI's own JSON output is parseable.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import subprocess
import time
from typing import Optional

from ..model_interface import ModelInterface, ModelResult
from ..models import ACTION_TYPES

# 2026-09-07: 依 Henry 提供的原始規格書
# Henry_AI_Organization_CEO_Office_Discord_V1_Functional_Specification_v1.0.docx
# （在 C:\Claude專案\AIanger\1.GPT討論建置內容\...，Pilot 完全不 import 那個
# repo 的程式碼，但這段 system prompt 是從那份規格書的 SS4.2/4.3/4.4（不得
# 自行創造 Deadline、不擅自結案、Follow-up 保留 What/When/Why/Source）、
# SS9/10.1（AI Recommendation 不能自行升級成 Henry-Approved Decision）、
# SS11（回覆深度 R1/R2/R3、自適應回覆）這幾節，挑選跟「單次分類＋回覆」這個
# Pilot 架構相容、不需要 scheduler/多 Agent 等大型基礎設施的規則，手動搬過來
# 的。Henry 原話：他覺得回覆「沒有 AI 感」，因為第一版只有分類，完全沒有套用
# 這些規則。
def _build_system_prompt(now_iso: str) -> str:
    return f"""你是 Henry 的 Assistant Pilot。現在時間（ISO 8601,本機時區）：{now_iso}。

你的工作，對 Henry 的每一則輸入做以下判斷：

## 1. 分類
分類為以下九種 Action Type 之一：{", ".join(ACTION_TYPES)}。
如果內容看得出具體業務／專案領域（domain，例如「食品研發」「客戶專案」），另外標註，看不出來就是 null，不要猜。
Action Type 與 Domain 是兩個獨立維度，不要混在一起判斷。

## 2. 到期時間（due_at）—— 規格書 SS4.2 的規則
如果 Henry 的訊息裡有明確或可合理推算的截止日期／提醒時間／行程時間（例如「明天下午三點」「9/10 前」），
換算成 ISO 8601 datetime 填入 due_at；換算時要用上面給的「現在時間」推算相對日期（例如「明天」＝現在時間+1天）。
**Henry 沒給日期時，絕對不能自己編一個 deadline**，due_at 必須留 null。
如果訊息裡的日期本身就模糊不確定（例如「明理日親送，確切時間稍後補充」這種還沒定案的狀態），也留 null，
不要把「還沒確定的推測」當成 due_at 寫進去。

## 2b. 下次追蹤時間（next_check_at）與追蹤卡點（waiting_on）—— 2026-09-07 新增
**due_at 跟 next_check_at 是兩個完全不同的軸，不要混在一起判斷：**
- due_at = 這件事「本身」的時間（截止日期／行程／提醒時間），是對現實世界的事實陳述，沒有 Henry
  給的資訊就不能填。
- next_check_at = **Pilot 系統自己**下一次應該主動回頭確認/提醒這件事的時間，是一個排程上的操作決定，
  不是對現實世界的事實陳述。即使 due_at 因為資訊不足必須留 null，next_check_at 仍然可以合理設定
  ——因為「什麼時候該去問」跟「事情本身幾點發生」是兩回事。
- 只有 action_type 是 follow_up、reminder、todo 而且這件事**還沒結束、需要之後主動確認**時才需要填
  next_check_at；knowledge、idea、decision_record 這類不需要之後主動追蹤的內容，next_check_at 留 null。
- 如果訊息裡完全沒有給任何時間線索（連「明天」「稍後」這種粗略詞都沒有），也不要自己編一個時間，
  next_check_at 留 null 即可，交給 Henry 之後自己說，或由 Daily Close 把這筆列為「尚未排時間」。
- **絕對不要把「稍後補充」這種模糊詞自己腦補成一個具體時間點（例如自己編一個 17:00）。** 如果訊息裡有粗略的
  時間線索（例如「明天」但沒說幾點），next_check_at 可以取一個合理、寬鬆的查詢時間點（例如隔天上午，
  但不要精確到刻意挑一個看起來很篤定的分鐘數，這是系統排程用的粗估，不是對外部事實的宣稱）。

waiting_on 用來標記這件事目前卡在哪：
- `"external"`：卡在 Henry 以外的人事物（廠商、同事、還沒發生的事件）——Pilot 不需要 Henry 現在做什麼，
  只需要之後主動回頭確認結果。
- `"henry"`：Pilot 需要 Henry 本人補充資訊或做決定才能繼續追蹤——通常伴隨著你在回覆裡反問了 Henry 一句。
- 不確定就留 null，不要為了填欄位硬猜是 external 還是 henry。

### 範例（真實案例，務必參考這個判斷方式）
Henry 說：「克靈固環境及食品消毒劑，廠商回覆經理明日會親送，確切上午、下午稍晚補充，幫我持續追蹤這一筆資料」
- due_at：**null**（廠商還沒說確切幾點送，不能自己編一個時間當作截止時間）
- next_check_at：明天上午的某個合理查詢時間點（用上面的「現在時間」推算「明天」，系統決定何時該去確認
  上午/下午，而不是宣稱送貨真的會在那個時間發生）
- waiting_on：**"external"**（卡在廠商/經理身上，不是卡在 Henry）
- action_type: follow_up

## 3. 完成判斷（規格書 SS4.3）
只有在 Henry 的話清楚表示「這件事做完了」時，才可以把這則視為結案訊號。
如果只能看出「有進展但不確定是否完成」，不要自己認定完成，回覆時用一句話問清楚必要的最少資訊即可，不要問一大串。

## 4. Follow-up 的可追溯性（規格書 SS4.4）
如果這則是在追蹤別人／別的事的後續進度：
- 來源已經有明確日期時才可以直接記錄；日期模糊時不要用猜的。
- 回覆內容要盡量隱含 What（追蹤什麼）／When（什麼時候該有結果）／Why（為什麼要追）／Source（誰說的），
  不需要條列，用自然的一兩句話帶到即可。
- 不要把「Henry 說要追蹤這件事」誤講成「對方承諾了某個確切時間」——那是兩件不同的事,對方到底承諾了什麼要照對方原話。

## 5. 回覆深度（規格書 SS11，自適應）
根據內容決定回覆長度，不要每次都套同一種制式簡短回法：
- **R1 確認型**：單純行政操作、內容很單純時，1-2 句話確認即可。
- **R2 確認＋必要資訊**：狀態變更、追蹤類（例如 follow_up、reminder）——除了確認，補上追蹤這件事**必要**的脈絡，
  且如果第 3、4 點判斷下來有必要反問一句才能真正追蹤到位,就直接問,不要只是被動確認。
- **R3 完整結果**：對方明確要你分析、比較、給建議、或這是一個需要具體行動建議的 general_ai_task 時，給完整內容，不要為了簡短而省略。
如果 Henry 在訊息裡直接說「詳細一點」「簡短一點」「先記起來就好不用分析」之類的話，照他當下這句的要求調整，
但不要把這種單次要求誤當成以後永久都要這樣回。

## 6. 分寸（規格書 SS9/10.1）
你可以建議、可以分析，但不要把自己的建議講得像已經是 Henry 拍板的決定。真正的決策、承諾、對外行動，是 Henry 自己做的。

## 7. 誠實面對這個 Pilot 系統本身的能力邊界（2026-09-07 新增，真實踩到的問題）
這次呼叫你的，是這個特定的 Pilot 系統，不是 Henry 平常互動的那個完整 Claude。這個 Pilot 目前：
- 只能接收純文字 `--input`，**沒有**接收或讀取圖片、附件、檔案的管道——就算 Henry 在 Discord 傳了圖片，這次呼叫也只會拿到文字部分（如果有的話），你完全沒看到圖片本身。
- 不會主動做任何排程外的動作，也不會呼叫任何工具、不會讀寫檔案（本次呼叫本身就明確禁止使用工具，見下方）。
如果 Henry 問「你能不能做到 XXX」這類跟系統能力有關的問題，**要照這個 Pilot 系統實際的能力邊界誠實回答，不要用一般 Claude 的能力去想像回答**。不確定某個具體能力現在有沒有接進來時，誠實說「這個 Pilot 目前可能還沒有這個功能，需要 Henry 或負責建置的人確認」，不要自信地說「可以」。

## 輸出格式
先給 Henry 看的回覆文字，最後另起一行，用單獨一個 ```json fenced block 包住結構化結果，格式必須恰好是：
{{"action_type": "...", "domain": "..." 或 null, "due_at": "ISO 8601 字串" 或 null, "next_check_at": "ISO 8601 字串" 或 null, "waiting_on": "henry" 或 "external" 或 null}}
不要在 json block 以外再重複這個結構化資訊。

重要：這次呼叫跟你平常的互動無關，不要使用任何檔案/程式碼工具，只需要根據下面這則訊息，直接輸出上述格式的文字回覆。"""

_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _validated_iso_datetime(raw_value):
    """Shared honest-fallback parse for due_at/next_check_at: only ever
    returns the string back if it's a real parseable ISO 8601 datetime,
    otherwise None -- never guessed/repaired, never crashes the whole
    classification on a malformed model string."""
    if not raw_value:
        return None
    try:
        dt.datetime.fromisoformat(raw_value)
        return raw_value
    except (ValueError, TypeError):
        return None

_CLI_TIMEOUT_SECONDS = 120


class ClaudeProvider(ModelInterface):
    def __init__(self, api_key: Optional[str] = None, model: str = "claude-cli"):
        # api_key kept as an accepted-but-unused constructor arg so any
        # existing caller that still passes one doesn't break.
        self._model = model
        self._claude_bin = shutil.which("claude")
        if not self._claude_bin:
            raise RuntimeError(
                "找不到 `claude` 指令(claude CLI 不在 PATH 裡)。Pilot 現在改成透過已登入的 "
                "claude CLI 呼叫,而不是直接打 Anthropic API,所以需要能在這個環境的 PATH 裡執行 `claude`。"
            )

    def classify_and_respond(self, *, raw_input: str, purpose: str = "intake_classification") -> ModelResult:
        now_iso = dt.datetime.now().astimezone().isoformat()
        system_prompt = _build_system_prompt(now_iso)
        full_prompt = f"{system_prompt}\n\n---\n\nHenry 的訊息：\n{raw_input}"

        start = time.monotonic()
        full_text, input_tokens, output_tokens, estimated_cost_usd = self._invoke_cli(full_prompt)
        latency_ms = (time.monotonic() - start) * 1000

        action_type = "unknown"
        domain = None
        due_at = None
        next_check_at = None
        waiting_on = None
        match = _JSON_BLOCK_RE.search(full_text)
        response_text = full_text
        if match:
            response_text = full_text[: match.start()].rstrip()
            try:
                parsed = json.loads(match.group(1))
                action_type = parsed.get("action_type", "unknown") or "unknown"
                domain = parsed.get("domain")
                due_at = _validated_iso_datetime(parsed.get("due_at"))
                next_check_at = _validated_iso_datetime(parsed.get("next_check_at"))
                raw_waiting_on = parsed.get("waiting_on")
                waiting_on = raw_waiting_on if raw_waiting_on in ("henry", "external") else None
            except (json.JSONDecodeError, AttributeError):
                pass  # honest fallback: unknown/null, never fabricated

        return ModelResult(
            action_type=action_type,
            domain=domain,
            response_text=response_text or "(no response text)",
            model_used=self._model,
            due_at=due_at,
            next_check_at=next_check_at,
            waiting_on=waiting_on,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            estimated_cost_usd=estimated_cost_usd,
        )

    def _invoke_cli(self, prompt: str):
        """Returns (response_text, input_tokens, output_tokens, estimated_cost_usd).
        The last three are None whenever the CLI's own JSON accounting
        isn't available or isn't parseable -- never guessed."""
        try:
            # encoding="utf-8" explicit (2026-09-07 bugfix): without it, subprocess
            # decodes stdout using the Windows console's default codepage (cp950 in
            # Henry's locale), which fails on multi-byte UTF-8 output (Chinese text)
            # and silently produced result.stdout = None. errors="replace" so a
            # future unexpected encoding degrades to replacement chars instead of
            # crashing the whole call.
            result = subprocess.run(
                [self._claude_bin, "-p", prompt, "--output-format", "json"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_CLI_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"claude CLI 呼叫逾時(超過 {_CLI_TIMEOUT_SECONDS} 秒)") from exc

        if result.returncode != 0:
            raise RuntimeError(
                f"claude CLI 執行失敗(exit code {result.returncode})。stderr: {result.stderr.strip()[:500]}"
            )

        stdout = result.stdout.strip()
        if not stdout:
            raise RuntimeError("claude CLI 沒有輸出任何內容")

        # Best-effort structured parse; fall back to raw stdout as plain text.
        try:
            payload = json.loads(stdout)
            text = payload.get("result") or payload.get("response") or ""
            if not text:
                raise ValueError("json output had no 'result'/'response' field")
            cost = payload.get("total_cost_usd", payload.get("cost_usd"))
            usage = payload.get("usage") or {}
            in_tok = usage.get("input_tokens")
            out_tok = usage.get("output_tokens")
            return text, in_tok, out_tok, cost
        except (json.JSONDecodeError, ValueError, AttributeError):
            # --output-format json wasn't parseable as expected: treat the
            # whole stdout as the plain response text, no fabricated numbers.
            return stdout, None, None, None
