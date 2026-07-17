from enum import IntEnum


class Action(IntEnum):
    """
    Every action is host-scoped (ADR 014 Phase 1): the environment always has an
    active host in engagement mode, so there is no more broadcast/no-host action.
    DO_NOTHING and SCAN_NETWORK are retired — the initial subnet scan is scripted
    at reset() instead of learned, and ABANDON already covers "not worth pursuing."
    """
    SCAN_PORTS = 0
    PROBE_PORT = 1
    BRUTE_FORCE_SSH = 2
    BRUTE_FORCE_FTP = 3
    BRUTE_FORCE_TELNET = 4
    CONNECT_SSH = 5
    CONNECT_FTP = 6
    CONNECT_TELNET = 7
    PROBE_HTTP = 8
    PROBE_REDIS = 9
    PROBE_MONGO = 10
    ABANDON = 11  # ends the current engagement; valid on any host once MIN_STEPS_BEFORE_ABANDON steps have passed


# Actions with no "tried against this host" meaning — excluded from state.py's
# per-host coverage features (see COVERAGE_FEATURES).
NO_COVERAGE_ACTIONS = {Action.ABANDON}

# ABANDON is withheld for the first few decisions of an engagement so the policy
# can't learn a degenerate "give up immediately, every time" local optimum before
# it's tried anything — found from a smoke test where ~30% of untrained-policy
# actions were ABANDON at engagement start (see docs/adr/014, Phase 1 notes).
MIN_STEPS_BEFORE_ABANDON = 3


def is_valid(
    action: Action,
    host_features: dict,
    engagement_step: int = 0,
    full_action_space: bool = False,
) -> bool:
    """Return True if action is valid given current knowledge of the active host.

    engagement_step: number of interaction steps already completed this engagement
    (0 at engagement start) — currently only consulted by the ABANDON gate.

    full_action_space: experimental toggle (2026-07-16) to test whether structural
    preconditions on exploit-path actions help or hurt learning, versus leaving it
    entirely to the LLM's own judgment (falling back to a no-op like `echo ok` when
    it lacks enough context) plus the -0.1 step cost as the only deterrent. Every
    precondition below is left intact — this only short-circuits them — so reverting
    is a one-line config flip, not a code change. ABANDON's gate is NOT affected by
    this flag; it's a separate, always-on rule.
    """
    if action == Action.ABANDON:
        return engagement_step >= MIN_STEPS_BEFORE_ABANDON
    if full_action_space:
        return True
    match action:
        case Action.SCAN_PORTS | Action.PROBE_PORT:
            return host_features.get("is_alive", False)
        case Action.BRUTE_FORCE_SSH:
            return host_features.get("port_22_open", False) and not host_features.get("creds_found", False)
        case Action.BRUTE_FORCE_FTP:
            return host_features.get("port_21_open", False) and not host_features.get("creds_found", False)
        case Action.BRUTE_FORCE_TELNET:
            return host_features.get("port_23_open", False) and not host_features.get("creds_found", False)
        case Action.CONNECT_SSH:
            return host_features.get("creds_found", False) and host_features.get("service_ssh", False)
        case Action.CONNECT_FTP:
            # Unlike SSH/Telnet, FTP's exploitable path in this scenario is
            # anonymous login (no credential to discover) — gating on
            # creds_found made host06 (soft FTP, anonymous:anonymous)
            # structurally unreachable, since hydra never sets creds_found
            # for a login that doesn't require one.
            return host_features.get("service_ftp", False)
        case Action.CONNECT_TELNET:
            return host_features.get("creds_found", False) and host_features.get("service_telnet", False)
        case Action.PROBE_HTTP:
            return host_features.get("port_80_open", False) or host_features.get("port_443_open", False)
        case Action.PROBE_REDIS:
            return host_features.get("port_6379_open", False)
        case Action.PROBE_MONGO:
            return host_features.get("port_27017_open", False)
    return False