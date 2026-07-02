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


def build_initial_messages(task: str) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]


class LLMClient:
    def __init__(self, model: str = "moonshotai/Kimi-K2.6", api_timeout: int = 60, reasoning_effort: str | None = None):
        self.model = model
        self._api_timeout = api_timeout
        self._reasoning_effort = reasoning_effort
        self._client = openai.OpenAI(
            api_key=os.environ["DEEPINFRA_API_KEY"],
            base_url="https://api.deepinfra.com/v1/openai",
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
        args = json.loads(tool_call.function.arguments)
        if not isinstance(args, dict) or "command" not in args:
            raise ValueError(f"Malformed tool call arguments: {tool_call.function.arguments!r}")
        return CommandRequest(
            command=args["command"],
            tool_call_id=tool_call.id,
            assistant_message={
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
            },
        )
