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
    ABANDON = 11  # ends the current engagement; available from any host state


# Actions with no "tried against this host" meaning — excluded from state.py's
# per-host coverage features (see COVERAGE_FEATURES).
NO_COVERAGE_ACTIONS = {Action.ABANDON}


def is_valid(action: Action, host_features: dict) -> bool:
    """Return True if action is valid given current knowledge of the active host."""
    match action:
        case Action.ABANDON:
            return True
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
            return host_features.get("creds_found", False) and host_features.get("service_ftp", False)
        case Action.CONNECT_TELNET:
            return host_features.get("creds_found", False) and host_features.get("service_telnet", False)
        case Action.PROBE_HTTP:
            return host_features.get("port_80_open", False) or host_features.get("port_443_open", False)
        case Action.PROBE_REDIS:
            return host_features.get("port_6379_open", False)
        case Action.PROBE_MONGO:
            return host_features.get("port_27017_open", False)
    return False