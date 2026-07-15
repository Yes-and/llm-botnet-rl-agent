"""
Attacker-side output parser.

parse_step() dispatches to a per-tool sub-parser based on the command string.
Each sub-parser returns a ParseResult with state feature updates and an optional
ExploitEvent. The caller (environment) is responsible for deduplicating ExploitEvents
— the parser fires one whenever it detects success, regardless of prior state.

Add new patterns as table entries in the relevant sub-parser. Each pattern should
correspond to a concrete example from a real run log.
"""

import re
from dataclasses import dataclass, field

from rl.reward import ExploitEvent


@dataclass
class ParseResult:
    # List of (ip, {feature: value}) updates to apply to EpisodeState
    state_updates: list[tuple[str, dict[str, bool]]] = field(default_factory=list)
    exploit: ExploitEvent | None = None


# ── Port → (port_feature, optional_service_feature) ──────────────────────────

_PORT_MAP: dict[int, tuple[str, str | None]] = {
    21:    ("port_21_open",    "service_ftp"),
    22:    ("port_22_open",    "service_ssh"),
    23:    ("port_23_open",    "service_telnet"),
    80:    ("port_80_open",    "service_http"),
    443:   ("port_443_open",   "service_http"),
    6379:  ("port_6379_open",  None),
    27017: ("port_27017_open", None),
}

# ── Compiled regexes ──────────────────────────────────────────────────────────

# nmap grepable: "Host: 1.2.3.4 (hostname)  Status: Up" — hostname is captured (not just
# matched) so _parse_nmap can tell a real container (Docker Compose DNS name) apart from
# Docker network infrastructure (gateway, etc.), which never gets a reverse-DNS entry and
# shows empty parens instead.
_NMAP_HOST_UP = re.compile(
    r"^Host:\s+([\d.]+)\s+\((.*?)\)\s+Status:\s+Up", re.MULTILINE
)
# nmap grepable: "Host: 1.2.3.4 (hostname)  Ports: 22/open/tcp//ssh//version/ ..."
_NMAP_PORTS_LINE = re.compile(
    r"^Host:\s+([\d.]+)\s+\((.*?)\)\s+Ports:\s+(.+)", re.MULTILINE
)
# individual port entry within the Ports field: "22/open/tcp//ssh//version/"
_NMAP_PORT_ENTRY = re.compile(r"(\d+)/open/\w+//(\w*)")

# nmap human-readable (no -oG): "Nmap scan report for [hostname (]1.2.3.4[)]" + "Host is up"
# hostname (group 1, optional) captured for the same reverse-DNS-noise filtering as above.
_NMAP_REPORT_UP = re.compile(
    r"^Nmap scan report for (?:(\S.*?)\s*\()?([\d.]+)\)?\s*\nHost is up",
    re.MULTILINE,
)

# hydra credential found: "[22][ssh] host: 1.2.3.4   login: admin   password: pass"
_HYDRA_CRED = re.compile(
    r"\[(\d+)\]\[(\w+)\] host:\s+([\d.]+)\s+login:\s+(\S+)\s+password:\s+(\S+)"
)

# redis-cli -h <ip>
_REDIS_HOST = re.compile(r"redis-cli\s+.*?-h\s+([\d.]+)")

# MongoClient('<ip>', ...) or MongoClient('mongodb://<ip>:port/')
_MONGO_HOST = re.compile(r"MongoClient\(['\"]?(?:mongodb://)?([\d.]+)")

# FTP('<ip>', ...) — python3 ftplib
_FTP_PYLIB_HOST = re.compile(r"FTP\(['\"]?([\d.]+)")

# ftp binary: "ftp [flags] <ip>" — match IP on first line of command
_FTP_BIN_HOST = re.compile(r"^\s*ftp\b[^\n]*?([\d]+\.[\d]+\.[\d]+\.[\d]+)")

# sshpass / ssh: "user@<ip>"
_SSH_HOST = re.compile(r"@([\d.]+)")

# telnetlib: Telnet('ip', ...) or Telnet("ip", ...)
_TELNET_HOST = re.compile(r"Telnet\(['\"]?([\d.]+)")


# ── Public interface ──────────────────────────────────────────────────────────

def parse_step(command: str, output: str, exit_code: int) -> ParseResult:
    """Dispatch to the appropriate sub-parser based on the command string."""
    if "nmap" in command:
        return _parse_nmap(output)
    if "hydra" in command:
        return _parse_hydra(output)
    if "redis-cli" in command:
        return _parse_redis_cli(command, output, exit_code)
    if "MongoClient" in command:
        return _parse_mongo(command, output, exit_code)
    if "FTP(" in command or "ftplib" in command:
        return _parse_ftp_pylib(command, output, exit_code)
    if re.match(r"\s*ftp\b", command):
        return _parse_ftp_bin(command, output, exit_code)
    if "telnetlib" in command:
        return _parse_telnetlib(command, output, exit_code)
    if "sshpass" in command or re.search(r"\bsshpass\b|\bssh\b", command):
        return _parse_ssh(command, output, exit_code)
    return ParseResult()


# ── Sub-parsers ───────────────────────────────────────────────────────────────

def _parse_nmap(output: str) -> ParseResult:
    # Parse regardless of exit code — timed-out scans still produce partial output.
    updates: dict[str, dict[str, bool]] = {}

    # Greppable format (-oG -). A host with no reverse-DNS hostname (empty parens) is
    # Docker network infrastructure (the bridge gateway, typically the subnet's .1
    # address) rather than a real scenario container — Compose containers always
    # resolve to their service DNS name. Skip it: it can never be exploited, and under
    # ADR 014's single-host engagement it would otherwise get repeatedly re-engaged
    # (nothing ever removes it from the pool) burning real steps for nothing.
    for m in _NMAP_HOST_UP.finditer(output):
        ip, hostname = m.group(1), m.group(2)
        if not hostname:
            continue
        updates.setdefault(ip, {})["is_alive"] = True

    for m in _NMAP_PORTS_LINE.finditer(output):
        ip, hostname, ports = m.group(1), m.group(2), m.group(3)
        if not hostname:
            continue
        feats = updates.setdefault(ip, {})
        feats["is_alive"] = True
        for pm in _NMAP_PORT_ENTRY.finditer(ports):
            port = int(pm.group(1))
            if port in _PORT_MAP:
                port_feat, svc_feat = _PORT_MAP[port]
                feats[port_feat] = True
                if svc_feat:
                    feats[svc_feat] = True

    # Human-readable format (no -oG flag)
    for m in _NMAP_REPORT_UP.finditer(output):
        hostname, ip = m.group(1), m.group(2)
        if not hostname:
            continue
        updates.setdefault(ip, {})["is_alive"] = True

    return ParseResult(state_updates=list(updates.items()))


def _parse_hydra(output: str) -> ParseResult:
    updates: dict[str, dict[str, bool]] = {}
    for m in _HYDRA_CRED.finditer(output):
        ip = m.group(3)
        proto = m.group(2).lower()
        feats = updates.setdefault(ip, {})
        feats["creds_found"] = True
        svc_map = {"ssh": "service_ssh", "ftp": "service_ftp", "telnet": "service_telnet"}
        if proto in svc_map:
            feats[svc_map[proto]] = True
    return ParseResult(state_updates=list(updates.items()))


def _parse_redis_cli(command: str, output: str, exit_code: int) -> ParseResult:
    if exit_code != 0:
        return ParseResult()
    m = _REDIS_HOST.search(command)
    if not m:
        return ParseResult()
    ip = m.group(1)
    # Require redis_version: in output — only present in INFO response on an open
    # (no-auth) server. PING returns "PONG" which proves connectivity, not auth bypass.
    # An auth-required server returns "NOAUTH Authentication required." for INFO.
    if "redis_version:" not in output:
        return ParseResult()
    return ParseResult(
        state_updates=[(ip, {"shell_access": True})],
        exploit=ExploitEvent(host=ip, vulnerability="redis_no_auth"),
    )


def _parse_mongo(command: str, output: str, exit_code: int) -> ParseResult:
    if exit_code != 0:
        return ParseResult()
    m = _MONGO_HOST.search(command)
    if not m:
        return ParseResult()
    ip = m.group(1)
    if "Traceback" in output or "ServerSelectionTimeoutError" in output:
        return ParseResult()
    return ParseResult(
        state_updates=[(ip, {"shell_access": True})],
        exploit=ExploitEvent(host=ip, vulnerability="mongodb_no_auth"),
    )


def _parse_ftp_pylib(command: str, output: str, exit_code: int) -> ParseResult:
    if exit_code != 0:
        return ParseResult()
    m = _FTP_PYLIB_HOST.search(command)
    if not m:
        return ParseResult()
    ip = m.group(1)
    if "Traceback" in output:
        return ParseResult()
    # Note: ftplib does not print the 230 response to stdout, so we cannot check for it.
    # Connection failures raise exceptions (ConnectionRefusedError, error_perm) which
    # produce a Traceback — that check above covers the common failure modes.
    return ParseResult(
        state_updates=[(ip, {"shell_access": True})],
        exploit=ExploitEvent(host=ip, vulnerability="ftp_anonymous_login"),
    )


def _parse_ftp_bin(command: str, output: str, exit_code: int) -> ParseResult:
    if exit_code != 0:
        return ParseResult()
    m = _FTP_BIN_HOST.search(command)
    if not m:
        return ParseResult()
    ip = m.group(1)
    # ftp binary exits 0 even on refused/failed login — require the 230 success response
    if "230" not in output:
        return ParseResult()
    return ParseResult(
        state_updates=[(ip, {"shell_access": True})],
        exploit=ExploitEvent(host=ip, vulnerability="ftp_anonymous_login"),
    )


def _parse_telnetlib(command: str, output: str, exit_code: int) -> ParseResult:
    if exit_code != 0:
        return ParseResult()
    m = _TELNET_HOST.search(command)
    if not m:
        return ParseResult()
    ip = m.group(1)
    if "login:" not in output:
        return ParseResult()
    # Service detected — update state regardless of login outcome
    service_updates = {"service_telnet": True, "port_23_open": True, "is_alive": True}
    # Successful login confirmed by shell command output (LLM runs 'id' after login)
    if "uid=" in output:
        return ParseResult(
            state_updates=[(ip, {**service_updates, "shell_access": True})],
            exploit=ExploitEvent(host=ip, vulnerability="telnet_weak_credentials"),
        )
    return ParseResult(state_updates=[(ip, service_updates)])


def _parse_ssh(command: str, output: str, exit_code: int) -> ParseResult:
    if exit_code != 0:
        return ParseResult()
    m = _SSH_HOST.search(command)
    if not m:
        return ParseResult()
    ip = m.group(1)
    if "Permission denied" in output or "Connection refused" in output:
        return ParseResult()
    # Confirm shell access via output of 'id' command
    if "uid=" not in output:
        return ParseResult()
    return ParseResult(
        state_updates=[(ip, {"shell_access": True, "creds_found": True})],
        exploit=ExploitEvent(host=ip, vulnerability="ssh_weak_credentials"),
    )
