import pytest
from rl.actions import Action, is_valid


def test_abandon_always_valid():
    assert is_valid(Action.ABANDON, {})


def test_scan_ports_requires_alive():
    assert not is_valid(Action.SCAN_PORTS, {})
    assert is_valid(Action.SCAN_PORTS, {"is_alive": True})


def test_probe_port_requires_alive():
    assert not is_valid(Action.PROBE_PORT, {})
    assert is_valid(Action.PROBE_PORT, {"is_alive": True})


def test_brute_force_requires_open_port():
    assert not is_valid(Action.BRUTE_FORCE_SSH, {})
    assert is_valid(Action.BRUTE_FORCE_SSH, {"port_22_open": True})

    assert not is_valid(Action.BRUTE_FORCE_FTP, {})
    assert is_valid(Action.BRUTE_FORCE_FTP, {"port_21_open": True})

    assert not is_valid(Action.BRUTE_FORCE_TELNET, {})
    assert is_valid(Action.BRUTE_FORCE_TELNET, {"port_23_open": True})


def test_connect_requires_creds_and_service():
    assert not is_valid(Action.CONNECT_SSH, {"creds_found": True})
    assert not is_valid(Action.CONNECT_SSH, {"service_ssh": True})
    assert is_valid(Action.CONNECT_SSH, {"creds_found": True, "service_ssh": True})

    assert not is_valid(Action.CONNECT_FTP, {"creds_found": True})
    assert is_valid(Action.CONNECT_FTP, {"creds_found": True, "service_ftp": True})

    assert not is_valid(Action.CONNECT_TELNET, {"creds_found": True})
    assert is_valid(Action.CONNECT_TELNET, {"creds_found": True, "service_telnet": True})


def test_probe_http_requires_port_80_or_443():
    assert not is_valid(Action.PROBE_HTTP, {})
    assert is_valid(Action.PROBE_HTTP, {"port_80_open": True})
    assert is_valid(Action.PROBE_HTTP, {"port_443_open": True})
    assert is_valid(Action.PROBE_HTTP, {"port_80_open": True, "port_443_open": True})


def test_probe_redis_requires_port():
    assert not is_valid(Action.PROBE_REDIS, {})
    assert is_valid(Action.PROBE_REDIS, {"port_6379_open": True})


def test_probe_mongo_requires_port():
    assert not is_valid(Action.PROBE_MONGO, {})
    assert is_valid(Action.PROBE_MONGO, {"port_27017_open": True})


def test_all_actions_covered():
    """Every action must have a matching is_valid() case. BRUTE_FORCE_* and
    CONNECT_* are mutually exclusive on creds_found (see
    test_creds_found_masks_brute_force_actions / test_connect_requires_creds_and_service),
    so they can't share one "everything true" dict — CONNECT_* gets its own."""
    host = {
        "is_alive": True,
        "port_21_open": True, "port_22_open": True, "port_23_open": True,
        "port_80_open": True, "port_443_open": True,
        "port_6379_open": True, "port_27017_open": True,
        "service_ssh": True, "service_ftp": True, "service_telnet": True,
    }
    connect_actions = {Action.CONNECT_SSH, Action.CONNECT_FTP, Action.CONNECT_TELNET}
    for action in Action:
        if action in connect_actions:
            continue
        assert is_valid(action, host)

    host_with_creds = {**host, "creds_found": True}
    for action in connect_actions:
        assert is_valid(action, host_with_creds)