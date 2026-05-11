import logging
import re
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[mGKH]")

ALLOWED_BINARIES = frozenset({
    "nmap", "hydra", "netcat", "nc", "curl", "ssh", "sshpass", "python3",
})

_DANGEROUS_PATTERNS = [
    re.compile(r"\brm\b"),
    re.compile(r"\bdd\b"),
    re.compile(r"\bmkfs\b"),
    re.compile(r":\(\)\s*\{"),   # fork bomb
    re.compile(r">\s*/dev/"),    # write to /dev/
]


@dataclass
class CommandResult:
    command: str
    output: str
    exit_code: int
    truncated: bool
    dry_run: bool


def format_tool_result(result: CommandResult) -> str:
    return f"exit_code: {result.exit_code}\n{result.output}"


class Executor:
    def __init__(
        self,
        container_name: str,
        *,
        dry_run: bool = False,
        timeout: int = 60,
        max_output_chars: int = 4000,
    ):
        self.container_name = container_name
        self.dry_run = dry_run
        self.timeout = timeout
        self.max_output_chars = max_output_chars

    def execute(self, command: str) -> CommandResult:
        binary = command.strip().split()[0] if command.strip() else ""

        if binary not in ALLOWED_BINARIES:
            rejection = (
                f"[REJECTED] '{binary}' is not in the allowed binary set: "
                f"{sorted(ALLOWED_BINARIES)}"
            )
            logger.warning("REJECTED (allowlist): %s", command)
            return CommandResult(
                command=command,
                output=rejection,
                exit_code=-1,
                truncated=False,
                dry_run=self.dry_run,
            )

        for pattern in _DANGEROUS_PATTERNS:
            if pattern.search(command):
                rejection = f"[REJECTED] Command matches dangerous pattern: {pattern.pattern!r}"
                logger.warning("REJECTED (blocklist): %s", command)
                return CommandResult(
                    command=command,
                    output=rejection,
                    exit_code=-1,
                    truncated=False,
                    dry_run=self.dry_run,
                )

        logger.info("COMMAND: %s", command)

        if self.dry_run:
            return CommandResult(
                command=command,
                output="[DRY RUN] Command not executed.",
                exit_code=0,
                truncated=False,
                dry_run=True,
            )

        try:
            proc = subprocess.run(
                ["docker", "exec", self.container_name, "/bin/bash", "-c", command],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            output = proc.stdout + proc.stderr
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            output = f"[TIMEOUT] Command exceeded {self.timeout}s and was killed."
            exit_code = -1

        output = _ANSI_ESCAPE.sub("", output)

        truncated = False
        if len(output) > self.max_output_chars:
            half = self.max_output_chars // 2
            output = output[:half] + "\n[... output truncated ...]\n" + output[-half:]
            truncated = True

        logger.info("EXIT_CODE=%d TRUNCATED=%s", exit_code, truncated)
        return CommandResult(
            command=command,
            output=output,
            exit_code=exit_code,
            truncated=truncated,
            dry_run=False,
        )