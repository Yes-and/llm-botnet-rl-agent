from collections.abc import Callable
from dataclasses import dataclass, field

from agent.executor import CommandResult, Executor, format_tool_result
from agent.llm_client import CommandRequest, LLMClient, build_initial_messages


@dataclass
class EpisodeConfig:
    task: str
    container_name: str
    max_steps: int = 10
    dry_run: bool = False
    timeout: int = 60
    max_output_chars: int = 4000
    model: str = "moonshotai/Kimi-K2.6"


@dataclass
class StepRecord:
    step: int
    request: CommandRequest
    result: CommandResult


@dataclass
class EpisodeResult:
    task: str
    steps: list[StepRecord] = field(default_factory=list)
    stop_reason: str | None = None  # set when the episode ended early via a ValueError, e.g. the model returned no tool call at all


def run_episode(
    config: EpisodeConfig,
    on_step: Callable[[StepRecord], bool | None] | None = None,
) -> EpisodeResult:
    client = LLMClient(model=config.model)
    executor = Executor(
        config.container_name,
        dry_run=config.dry_run,
        timeout=config.timeout,
        max_output_chars=config.max_output_chars,
    )

    messages = build_initial_messages(config.task)
    episode = EpisodeResult(task=config.task)

    for step in range(config.max_steps):
        try:
            request = client.complete(messages)
        except ValueError as e:
            episode.stop_reason = str(e)
            break
        messages.append(request.assistant_message)

        if request.error:
            # Deliberately generic — don't echo the raw malformed content (may contain
            # leaked special tokens or garbled text) back into the model's own context.
            # The full detail is still on request.error for logging (see run_case_study.py).
            result = CommandResult(
                command="",
                output='[MALFORMED] Your previous tool call could not be parsed. '
                       'Reply with exactly one tool call: a JSON object with a single "command" string field.',
                exit_code=-1, truncated=False, dry_run=config.dry_run,
            )
        else:
            result = executor.execute(request.command)
        messages.append({
            "role": "tool",
            "tool_call_id": request.tool_call_id,
            "content": format_tool_result(result),
        })

        record = StepRecord(step=step, request=request, result=result)
        episode.steps.append(record)
        if on_step and on_step(record):
            break

    return episode
