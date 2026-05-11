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
    def __init__(self, model: str = "moonshotai/Kimi-K2.6"):
        self.model = model
        self._client = openai.OpenAI(
            api_key=os.environ["DEEPINFRA_API_KEY"],
            base_url="https://api.deepinfra.com/v1/openai",
        )

    def complete(self, messages: list[dict]) -> CommandRequest:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=TOOLS,
            tool_choice="required",  # always force a tool call; never allow a plain-text response
        )
        message = response.choices[0].message
        tool_call = message.tool_calls[0]
        args = json.loads(tool_call.function.arguments)
        return CommandRequest(
            command=args["command"],
            tool_call_id=tool_call.id,
            assistant_message=message.model_dump(),  # stored so the loop can append it to message history
        )
