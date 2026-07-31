import logging
import re
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[mGKH]")

ALLOWED_BINARIES = frozenset({
    "nmap", "hydra", "netcat", "nc", "curl", "ssh", "sshpass", "ssh-keygen", "python3",
    "ping", "ip", "ls", "cat", "find", "grep", "echo", "which",
    "telnet", "ftp", "redis-cli",
})

_DANGEROUS_PATTERNS = [
    (re.compile(r"\brm\b"), "deletes files"),
    (re.compile(r"\bdd\b"), "raw disk I/O"),
    (re.compile(r"\bmkfs\b"), "formats a filesystem"),
    (re.compile(r":\(\)\s*\{"), "fork bomb"),  # matches :(){ ... } shell function definition
    # (?<!2) exempts `2>/dev/null` (stderr suppression, benign — the model uses this
    # correctly for its own filesystem self-discovery, e.g. `find ... 2>/dev/null`)
    # while still blocking `>/dev/null`/`1>/dev/null`/`&>/dev/null` (stdout hiding).
    # Doesn't handle a stray space between the fd digit and `>` (e.g. `2 > /dev/null`)
    # — not seen in practice, not worth a variable-width lookbehind for.
    (re.compile(r"(?<!2)>\s*/dev/"), "redirects stdout to /dev/ — remove the redirect and retry; output must stay visible"),
    # python3 is allowed (required for pymongo/telnetlib — no other client exists for
    # those protocols in this image), but shelling out from inside it reaches any
    # binary in the container regardless of ALLOWED_BINARIES, defeating the curated
    # tool list entirely (confirmed 2026-07-31: os.system('ssh-keygen ...') worked
    # despite ssh-keygen not being allowed at the time). rm/dd/mkfs/fork-bomb text
    # still gets caught above regardless of python wrapping (plain regex over the
    # whole command string) — this closes the remaining "run anything" escape hatch.
    (re.compile(r"os\.system\(|subprocess\.\w+\(|os\.popen\("),
     "shells out to an arbitrary binary from Python, bypassing the tool allowlist — "
     "use an allowed binary directly, or a library (pymongo/telnetlib/ftplib) for network protocols"),
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

        for pattern, reason in _DANGEROUS_PATTERNS:
            if pattern.search(command):
                rejection = f"[REJECTED] Command blocked: {reason}"
                logger.warning("REJECTED (blocklist): %s", command)
                return CommandResult(
                    command=command,
                    output=rejection,
                    exit_code=-1,
                    truncated=False,
                    dry_run=self.dry_run,
                )

        logger.debug("COMMAND: %s", command)

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
                [
                    "docker", "exec", self.container_name,
                    "timeout", str(self.timeout),
                    "/bin/bash", "-c", command,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout + 10,  # grace period; in-container timeout fires first
            )
            output = proc.stdout + proc.stderr
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            output = f"[TIMEOUT] docker exec hung after {self.timeout + 10}s."
            exit_code = -1

        if exit_code == 124:  # exit code reserved by the timeout(1) command when it kills the process
            output += f"\n[TIMEOUT] Command exceeded {self.timeout}s and was killed."

        output = _ANSI_ESCAPE.sub("", output)

        truncated = False
        if len(output) > self.max_output_chars:
            half = self.max_output_chars // 2
            output = output[:half] + "\n[... output truncated ...]\n" + output[-half:]
            truncated = True

        logger.debug("EXIT_CODE=%d TRUNCATED=%s", exit_code, truncated)
        return CommandResult(
            command=command,
            output=output,
            exit_code=exit_code,
            truncated=truncated,
            dry_run=False,
        )