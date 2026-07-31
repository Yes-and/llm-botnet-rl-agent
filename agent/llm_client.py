import json
import os
import time
from dataclasses import dataclass
from typing import Any

import openai

from agent.tools import SYSTEM_PROMPT, TOOLS


@dataclass
class CommandRequest:
    command: str
    tool_call_id: str
    assistant_message: dict[str, Any]
    reasoning: str = ""  # model's thinking trace, if the model emits one (empty otherwise)
    error: str | None = None  # set when the tool call itself was malformed; command is not executable
    prompt_tokens: int = 0
    completion_tokens: int = 0


def build_initial_messages(task: str) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]


class LLMClient:
    def __init__(
        self,
        model: str = "moonshotai/Kimi-K2.6",
        api_timeout: int = 60,
        reasoning_effort: str | None = None,
        base_url: str = "https://api.deepinfra.com/v1/openai",
        api_key_env: str = "DEEPINFRA_API_KEY",
        max_retries: int = 5,
        no_choices_retries: int = 2,
    ):
        self.model = model
        self._api_timeout = api_timeout
        self._reasoning_effort = reasoning_effort
        self._no_choices_retries = no_choices_retries
        # SDK default (2) isn't enough to ride out a real rate-limit/overload burst
        # (seen from both DeepInfra "engine_overloaded" and OpenRouter provider 429s);
        # only retries retryable statuses (429/408/409/5xx + connection errors) with
        # its own backoff — a genuine 400 (malformed request) is never retried.
        self._client = openai.OpenAI(
            api_key=os.environ[api_key_env],
            base_url=base_url,
            max_retries=max_retries,
        )

    def complete(self, messages: list[dict]) -> CommandRequest:
        extra = {"reasoning_effort": self._reasoning_effort} if self._reasoning_effort is not None else {}
        response = None
        for attempt in range(self._no_choices_retries + 1):
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=TOOLS,
                tool_choice="required",  # always force a tool call; never allow a plain-text response
                # Executor/loop only ever execute and answer tool_calls[0] — a model that emits more
                # than one tool call in a turn leaves the rest unanswered in history, which the next
                # request's provider-side validation then rejects outright (seen as a 400 from
                # Moonshot AI via OpenRouter: "tool_call_ids did not have response messages").
                parallel_tool_calls=False,
                timeout=self._api_timeout,
                extra_body=extra,
            )
            if response.choices:
                break
            # Confirmed via raw response inspection (2026-07-28): OpenRouter's provider-fallback
            # routing sometimes returns this exact validation error as an HTTP-200-shaped response
            # (choices=None + an embedded error field) instead of raising, so the SDK's own
            # max_retries never sees it as retryable. Retry the whole request ourselves.
            if attempt < self._no_choices_retries:
                time.sleep(2)
        # Some OpenAI-compatible providers omit `usage` on certain responses; default to 0
        # rather than raising, since token counts are informational (cost tracking), not
        # required for the episode to proceed.
        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        if not response.choices:
            raise ValueError(f"Provider returned no choices after {self._no_choices_retries + 1} attempts: {response!r}")
        message = response.choices[0].message
        if not message.tool_calls:
            # `.refusal` is a separate field from `.content` on a real content-policy
            # refusal (confirmed via a raw-response diagnostic on Opus 5, 2026-07-28) —
            # without it, this error is indistinguishable from any other no-tool-call
            # case (content=None gives zero signal either way). Surface it when present.
            refusal = getattr(message, "refusal", None)
            detail = f" Refusal: {refusal!r}" if refusal else ""
            raise ValueError(
                f"Model returned no tool call. Text response: {message.content!r}{detail}"
            )
        tool_call = message.tool_calls[0]
        # Reasoning models surface the thinking trace in a separate field, not in
        # .content (Kimi-style models put their rationale in .content instead). Grab
        # whichever exists so the transcript log can show the model's reasoning.
        reasoning = getattr(message, "reasoning_content", None) or getattr(message, "reasoning", None) or ""
        assistant_message = {
            "role": "assistant",
            "content": message.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ],
        }

        try:
            args = json.loads(tool_call.function.arguments)
            if not isinstance(args, dict) or not isinstance(args.get("command"), str):
                raise ValueError(f"expected a JSON object with a string \"command\" key, got: {tool_call.function.arguments!r}")
        except (json.JSONDecodeError, ValueError) as e:
            # Malformed args are the model's mistake, not a system failure — recoverable,
            # fed back as an error tool result (see agent/loop.py) instead of ending the episode.
            return CommandRequest(
                command="",
                tool_call_id=tool_call.id,
                reasoning=reasoning,
                assistant_message=assistant_message,
                error=f"Malformed tool call: {e}",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )

        return CommandRequest(
            command=args["command"],
            tool_call_id=tool_call.id,
            reasoning=reasoning,
            assistant_message=assistant_message,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
