from enum import IntEnum


class Action(IntEnum):
    DO_NOTHING = 0
    SCAN_NETWORK = 1
    SCAN_PORTS = 2
    PROBE_PORT = 3
    BRUTE_FORCE_SSH = 4
    BRUTE_FORCE_FTP = 5
    BRUTE_FORCE_TELNET = 6
    CONNECT_SSH = 7
    CONNECT_FTP = 8
    CONNECT_TELNET = 9
    PROBE_HTTP = 10
    PROBE_REDIS = 11
    PROBE_MONGO = 12


BROADCAST_ACTIONS = {Action.DO_NOTHING, Action.SCAN_NETWORK}


def is_valid(action: Action, host_features: dict) -> bool:
    """Return True if action is valid given current knowledge of a host."""
    match action:
        case Action.DO_NOTHING | Action.SCAN_NETWORK:
            return True
        case Action.SCAN_PORTS | Action.PROBE_PORT:
            return host_features.get("is_alive", False)
        case Action.BRUTE_FORCE_SSH:
            return host_features.get("port_22_open", False)
        case Action.BRUTE_FORCE_FTP:
            return host_features.get("port_21_open", False)
        case Action.BRUTE_FORCE_TELNET:
            return host_features.get("port_23_open", False)
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