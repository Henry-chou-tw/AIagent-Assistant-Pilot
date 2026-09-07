"""First ModelInterface implementation: a real, direct Anthropic API call
-- deliberately NOT piggybacking on whatever live Claude Code / Discord
Channels session relayed the message in. This is what makes Step 5/6's
token/cost/latency numbers real measurements of one API call, rather than
an estimate of a live conversational turn we don't fully control, and
it's what makes the Discord transport <-> model invocation separation in
Step 7 actually true in code, not just true on paper.

Requires ANTHROPIC_API_KEY in the environment -- never read from a
plaintext file in this repository or logged anywhere. See README.md for
setup and the security note about C:\\Claude專案\\discord.txt (a separate,
pre-existing issue, unrelated to this Pilot's own credential handling).
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Optional

from ..model_interface import ModelInterface, ModelResult
from ..models import ACTION_TYPES

_MODEL = os.environ.get("PILOT_CLAUDE_MODEL", "claude-sonnet-4-5")

# Best-effort published pricing (USD per million tokens). Kept as a
# small, clearly-labeled table so it is obviously an estimate, never
# presented as an authoritative invoice figure. Update when pricing
# changes; leave estimated_cost_usd = None if the model isn't listed
# rather than guessing.
_PRICING_PER_MILLION_USD = {
    "claude-sonnet-4-5": {"input": 3.0, "output": 15.0},
}

_SYSTEM_PROMPT = f"""你是 Henry 的 Assistant Pilot。你的唯一工作：對 Henry 的輸入做兩件事——
(1) 分類為以下九種 Action Type 之一：{", ".join(ACTION_TYPES)}；
(2) 如果內容看得出具體業務／專案領域（domain，例如「食品研發」「客戶專案」），另外標註，看不出來就是 null，不要猜。
Action Type 與 Domain 是兩個獨立維度，不要混在一起判斷。
然後給 Henry 一個簡短、有幫助的直接回覆。

輸出格式：先給 Henry 看的回覆文字，最後另起一行，用單獨一個 ```json fenced block 包住結構化結果，格式必須恰好是：
{{"action_type": "...", "domain": "..." 或 null}}
不要在 json block 以外再重複這個結構化資訊。"""

_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


class ClaudeProvider(ModelInterface):
    def __init__(self, api_key: Optional[str] = None, model: str = _MODEL):
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self._api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. Set it as an environment variable -- "
                "never hardcode it in a file in this repository."
            )
        self._model = model
        import anthropic  # local import: keeps the SDK dependency optional for code that doesn't need this provider

        self._client = anthropic.Anthropic(api_key=self._api_key)

    def classify_and_respond(self, *, raw_input: str, purpose: str = "intake_classification") -> ModelResult:
        start = time.monotonic()
        message = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": raw_input}],
        )
        latency_ms = (time.monotonic() - start) * 1000

        full_text = "".join(block.text for block in message.content if getattr(block, "type", None) == "text")

        action_type = "unknown"
        domain = None
        match = _JSON_BLOCK_RE.search(full_text)
        response_text = full_text
        if match:
            response_text = full_text[: match.start()].rstrip()
            try:
                parsed = json.loads(match.group(1))
                action_type = parsed.get("action_type", "unknown") or "unknown"
                domain = parsed.get("domain")
            except (json.JSONDecodeError, AttributeError):
                pass  # honest fallback: unknown/null, never fabricated

        input_tokens = getattr(message.usage, "input_tokens", None)
        output_tokens = getattr(message.usage, "output_tokens", None)

        estimated_cost_usd = None
        pricing = _PRICING_PER_MILLION_USD.get(self._model)
        if pricing is not None and input_tokens is not None and output_tokens is not None:
            estimated_cost_usd = (
                input_tokens * pricing["input"] + output_tokens * pricing["output"]
            ) / 1_000_000

        return ModelResult(
            action_type=action_type,
            domain=domain,
            response_text=response_text or "(no response text)",
            model_used=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            estimated_cost_usd=estimated_cost_usd,
        )
