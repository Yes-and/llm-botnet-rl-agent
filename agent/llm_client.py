import json
import os
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
    ):
        self.model = model
        self._api_timeout = api_timeout
        self._reasoning_effort = reasoning_effort
        self._client = openai.OpenAI(
            api_key=os.environ[api_key_env],
            base_url=base_url,
        )

    def complete(self, messages: list[dict]) -> CommandRequest:
        extra = {"reasoning_effort": self._reasoning_effort} if self._reasoning_effort is not None else {}
        response = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=TOOLS,
            tool_choice="required",  # always force a tool call; never allow a plain-text response
            timeout=self._api_timeout,
            extra_body=extra,
        )
        message = response.choices[0].message
        if not message.tool_calls:
            raise ValueError(
                f"Model returned no tool call. Text response: {message.content!r}"
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
            )

        return CommandRequest(
            command=args["command"],
            tool_call_id=tool_call.id,
            reasoning=reasoning,
            assistant_message=assistant_message,
        )
