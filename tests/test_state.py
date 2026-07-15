import pytest
import torch
from rl.actions import Action, NO_COVERAGE_ACTIONS
from rl.state import (
    EpisodeState,
    MAX_HOSTS,
    NUM_FEATURES,
    KNOWLEDGE_FEATURES,
    COVERAGE_FEATURES,
    FEATURE_INDEX,
)


def test_feature_index_covers_all_features():
    assert len(FEATURE_INDEX) == NUM_FEATURES
    assert set(FEATURE_INDEX.keys()) == set(KNOWLEDGE_FEATURES + COVERAGE_FEATURES)


def test_coverage_features_cover_all_actions():
    assert len(COVERAGE_FEATURES) == len(Action) - len(NO_COVERAGE_ACTIONS)


def test_coverage_features_exclude_abandon():
    for action in NO_COVERAGE_ACTIONS:
        assert f"tried_{action.name.lower()}" not in COVERAGE_FEATURES


def test_set_and_get():
    state = EpisodeState()
    state.set("10.0.0.1", "is_alive", True)
    assert state.get("10.0.0.1", "is_alive") is True
    state.set("10.0.0.1", "is_alive", False)
    assert state.get("10.0.0.1", "is_alive") is False


def test_get_unknown_host_returns_false():
    state = EpisodeState()
    assert state.get("10.0.0.99", "is_alive") is False


def test_update_sets_multiple_features():
    state = EpisodeState()
    state.update("10.0.0.1", {"is_alive": True, "port_22_open": True})
    assert state.get("10.0.0.1", "is_alive") is True
    assert state.get("10.0.0.1", "port_22_open") is True
    assert state.get("10.0.0.1", "port_80_open") is False


def test_mark_tried():
    state = EpisodeState()
    state.set("10.0.0.1", "is_alive", True)
    state.mark_tried("10.0.0.1", Action.SCAN_PORTS)
    assert state.get("10.0.0.1", "tried_scan_ports") is True
    assert state.get("10.0.0.1", "tried_probe_port") is False


def test_mark_tried_counts_repeated_tries():
    """tried_* is a capped count, not a flag — distinguishes 'tried once' from
    'tried repeatedly, nothing new' (a signal for learning when to abandon)."""
    from rl.state import FEATURE_INDEX, MAX_TRIED_COUNT

    state = EpisodeState()
    state.set("10.0.0.1", "is_alive", True)
    for _ in range(MAX_TRIED_COUNT + 3):
        state.mark_tried("10.0.0.1", Action.SCAN_PORTS)
    raw_count = state._hosts["10.0.0.1"][FEATURE_INDEX["tried_scan_ports"]]
    assert raw_count == MAX_TRIED_COUNT  # caps rather than growing unbounded
    assert state.get("10.0.0.1", "tried_scan_ports") is True  # still truthy as "was tried"


def test_set_accepts_float_values():
    """set() isn't boolean-only — engagement_progress needs a real 0..1 float.
    Note: get() truncates through bool(), so it can't round-trip the raw value
    (0.4 reads back as True) — read _hosts directly, or via to_tensor(), for the
    actual float."""
    from rl.state import FEATURE_INDEX

    state = EpisodeState()
    state.set("10.0.0.1", "engagement_progress", 0.4)
    assert state._hosts["10.0.0.1"][FEATURE_INDEX["engagement_progress"]] == 0.4
    assert state.get("10.0.0.1", "engagement_progress") is True


def test_host_features_returns_knowledge_only():
    state = EpisodeState()
    state.update("10.0.0.1", {"is_alive": True, "port_22_open": True})
    state.mark_tried("10.0.0.1", Action.SCAN_PORTS)
    features = state.host_features("10.0.0.1")
    assert set(features.keys()) == set(KNOWLEDGE_FEATURES)
    assert features["is_alive"] is True
    assert features["port_22_open"] is True


def test_known_hosts_returns_all_discovered_hosts():
    state = EpisodeState()
    ips = {"10.0.0.10", "10.0.0.2", "10.0.0.1"}
    for ip in ips:
        state.set(ip, "is_alive", True)
    assert set(state.known_hosts()) == ips


def test_to_tensor_shape():
    state = EpisodeState()
    state.set("10.0.0.1", "is_alive", True)
    t = state.to_tensor()
    assert t.shape == (MAX_HOSTS, NUM_FEATURES)
    assert t.dtype == torch.float32


def test_to_tensor_slot_consistent_with_known_hosts():
    state = EpisodeState()
    state.update("10.0.0.2", {"is_alive": True, "port_22_open": True})
    state.update("10.0.0.1", {"is_alive": True})
    t = state.to_tensor()
    hosts = state.known_hosts()
    # known_hosts()[i] must appear in tensor row i with the correct features
    for i, ip in enumerate(hosts):
        assert t[i, FEATURE_INDEX["is_alive"]] == 1.0
    slot_of_2 = hosts.index("10.0.0.2")
    slot_of_1 = hosts.index("10.0.0.1")
    assert t[slot_of_2, FEATURE_INDEX["port_22_open"]] == 1.0
    assert t[slot_of_1, FEATURE_INDEX["port_22_open"]] == 0.0


def test_to_tensor_normalizes_tried_counts():
    """tried_* is a raw 0..MAX_TRIED_COUNT count in _hosts, but must be scaled to
    0..1 in the tensor — everything else the network sees is already that scale,
    and an unscaled count would dominate boolean features in the same dot product."""
    from rl.state import MAX_TRIED_COUNT

    state = EpisodeState()
    state.set("10.0.0.1", "is_alive", True)
    for _ in range(MAX_TRIED_COUNT):
        state.mark_tried("10.0.0.1", Action.SCAN_PORTS)
    t = state.to_tensor()
    assert t[0, FEATURE_INDEX["tried_scan_ports"]] == 1.0
    assert t[0, FEATURE_INDEX["is_alive"]] == 1.0


def test_to_tensor_unused_slots_are_zero():
    state = EpisodeState()
    state.set("10.0.0.1", "is_alive", True)
    t = state.to_tensor()
    # One host → exactly one non-zero row; all other slots remain zero-padded
    assert (t.sum(dim=1) != 0).sum().item() == 1


def test_max_hosts_cap():
    state = EpisodeState()
    for i in range(MAX_HOSTS + 5):
        state.set(f"10.0.0.{i + 1}", "is_alive", True)
    assert len(state.known_hosts()) == MAX_HOSTS


def test_remove_drops_host_from_pool():
    state = EpisodeState()
    state.update("10.0.0.1", {"is_alive": True, "shell_access": True})
    state.update("10.0.0.2", {"is_alive": True})
    state.remove("10.0.0.1")
    assert state.known_hosts() == ["10.0.0.2"]
    assert state.get("10.0.0.1", "is_alive") is False  # gone, not just flagged


def test_remove_unknown_host_is_a_noop():
    state = EpisodeState()
    state.set("10.0.0.1", "is_alive", True)
    state.remove("10.0.0.99")  # must not raise
    assert state.known_hosts() == ["10.0.0.1"]


def test_reset_clears_all_hosts():
    state = EpisodeState()
    state.set("10.0.0.1", "is_alive", True)
    state.reset()
    assert state.known_hosts() == []
    assert state.to_tensor().sum() == 0.0
