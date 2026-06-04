import pytest
import torch
from rl.actions import Action
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
    assert len(COVERAGE_FEATURES) == len(Action)


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
    assert state.get("10.0.0.1", "tried_scan_network") is False


def test_host_features_returns_knowledge_only():
    state = EpisodeState()
    state.update("10.0.0.1", {"is_alive": True, "port_22_open": True})
    state.mark_tried("10.0.0.1", Action.SCAN_PORTS)
    features = state.host_features("10.0.0.1")
    assert set(features.keys()) == set(KNOWLEDGE_FEATURES)
    assert features["is_alive"] is True
    assert features["port_22_open"] is True


def test_known_hosts_sorted_numerically():
    state = EpisodeState()
    for ip in ["10.0.0.10", "10.0.0.2", "10.0.0.1"]:
        state.set(ip, "is_alive", True)
    assert state.known_hosts() == ["10.0.0.1", "10.0.0.2", "10.0.0.10"]


def test_to_tensor_shape():
    state = EpisodeState()
    state.set("10.0.0.1", "is_alive", True)
    t = state.to_tensor()
    assert t.shape == (MAX_HOSTS, NUM_FEATURES)
    assert t.dtype == torch.float32


def test_to_tensor_host_in_correct_slot():
    state = EpisodeState()
    state.update("10.0.0.2", {"is_alive": True, "port_22_open": True})
    state.update("10.0.0.1", {"is_alive": True})
    t = state.to_tensor()
    # 10.0.0.1 sorts first → slot 0, 10.0.0.2 → slot 1
    assert t[0, FEATURE_INDEX["is_alive"]] == 1.0
    assert t[0, FEATURE_INDEX["port_22_open"]] == 0.0
    assert t[1, FEATURE_INDEX["port_22_open"]] == 1.0


def test_to_tensor_unused_slots_are_zero():
    state = EpisodeState()
    state.set("10.0.0.1", "is_alive", True)
    t = state.to_tensor()
    assert t[1:].sum() == 0.0


def test_max_hosts_cap():
    state = EpisodeState()
    for i in range(MAX_HOSTS + 5):
        state.set(f"10.0.0.{i + 1}", "is_alive", True)
    assert len(state.known_hosts()) == MAX_HOSTS


def test_reset_clears_all_hosts():
    state = EpisodeState()
    state.set("10.0.0.1", "is_alive", True)
    state.reset()
    assert state.known_hosts() == []
    assert state.to_tensor().sum() == 0.0
