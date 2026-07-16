import torch
import torch.nn.functional as F
import pytest

from rl.actions import Action
from rl.policy import Policy, NUM_ACTIONS, _build_action_mask
from rl.state import FEATURE_INDEX, MAX_HOSTS, NUM_FEATURES


@pytest.fixture
def policy():
    torch.manual_seed(0)
    return Policy(hidden_dim=64, num_layers=2)


@pytest.fixture
def zero_state():
    """All-zero except is_alive on host 0 — a host is never actively engaged
    without is_alive=True, and an all-invalid mask underflows softmax to NaN
    (every action masked), so a truly empty state isn't reachable in practice."""
    state = torch.zeros(MAX_HOSTS, NUM_FEATURES)
    state[0, FEATURE_INDEX["is_alive"]] = 1.0
    return state


def _set(state: torch.Tensor, row: int, feature: str, value: float = 1.0) -> torch.Tensor:
    state = state.clone()
    state[row, FEATURE_INDEX[feature]] = value
    return state


# --- Return types ---

def test_sample_return_types(policy, zero_state):
    action, log_prob, entropy = policy.sample(zero_state, host_idx=0)
    assert isinstance(action, Action)
    assert isinstance(log_prob, torch.Tensor)
    assert log_prob.shape == ()
    assert isinstance(entropy, torch.Tensor)
    assert entropy.shape == ()


def test_predict_return_type(policy, zero_state):
    assert isinstance(policy.predict(zero_state, host_idx=0), Action)


def test_predict_is_deterministic(policy, zero_state):
    assert policy.predict(zero_state, host_idx=0) == policy.predict(zero_state, host_idx=0)


def test_log_prob_is_scalar(policy, zero_state):
    _, log_prob, _ = policy.sample(zero_state, host_idx=0)
    assert log_prob.ndim == 0


def test_log_prob_is_negative(policy):
    """A state with only is_alive set (fresh discovery) leaves SCAN_PORTS and
    PROBE_PORT valid (ABANDON is masked until MIN_STEPS_BEFORE_ABANDON) — >=2
    valid actions, so the sampled log_prob reflects real uncertainty, not a
    certain (log_prob=0.0) single-option outcome."""
    state = _set(torch.zeros(MAX_HOSTS, NUM_FEATURES), 0, "is_alive")
    _, log_prob, _ = policy.sample(state, host_idx=0, engagement_step=0)
    assert log_prob.item() < 0.0


# --- is_valid()-based masking: dial-in behavior ---
# The mask is meant to tighten as the active host's state fills in over an
# engagement — these tests walk through that progression directly.

def test_nothing_known_only_recon_valid_before_abandon_unlocks(policy):
    """Right after the scripted discovery scan, only is_alive is set, and no
    engagement steps have happened yet. Only the recon actions (SCAN_PORTS/
    PROBE_PORT) should be selectable — every exploit-path action requires
    knowledge this host doesn't have yet, and ABANDON is masked until
    MIN_STEPS_BEFORE_ABANDON steps have passed (see test below)."""
    state = _set(torch.zeros(MAX_HOSTS, NUM_FEATURES), 0, "is_alive")
    logits = policy._action_logits(state, host_idx=0, engagement_step=0)
    probs = F.softmax(logits, dim=-1)
    valid = {Action.SCAN_PORTS, Action.PROBE_PORT}
    for a in Action:
        if a in valid:
            assert probs[int(a)].item() > 1e-6, f"{a.name} should remain selectable"
        else:
            assert probs[int(a)].item() < 1e-6, f"{a.name} should be masked with nothing known"


def test_abandon_unlocks_after_min_engagement_steps(policy):
    from rl.actions import MIN_STEPS_BEFORE_ABANDON

    state = _set(torch.zeros(MAX_HOSTS, NUM_FEATURES), 0, "is_alive")
    probs_early = F.softmax(policy._action_logits(state, host_idx=0, engagement_step=0), dim=-1)
    assert probs_early[int(Action.ABANDON)].item() < 1e-6

    probs_late = F.softmax(
        policy._action_logits(state, host_idx=0, engagement_step=MIN_STEPS_BEFORE_ABANDON), dim=-1
    )
    assert probs_late[int(Action.ABANDON)].item() > 1e-6


def test_port_discovery_unmasks_matching_brute_force(policy):
    """Once port_22_open is known, BRUTE_FORCE_SSH unmasks — but CONNECT_SSH stays
    masked until creds are actually found (still requires creds_found)."""
    state = _set(torch.zeros(MAX_HOSTS, NUM_FEATURES), 0, "is_alive")
    state = _set(state, 0, "port_22_open")
    probs = F.softmax(policy._action_logits(state, host_idx=0), dim=-1)
    assert probs[int(Action.BRUTE_FORCE_SSH)].item() > 1e-6
    assert probs[int(Action.CONNECT_SSH)].item() < 1e-6
    assert probs[int(Action.BRUTE_FORCE_FTP)].item() < 1e-6, "unrelated port stays masked"


def test_creds_found_masks_brute_force_and_unmasks_connect(policy):
    state = _set(torch.zeros(MAX_HOSTS, NUM_FEATURES), 0, "is_alive")
    state = _set(state, 0, "port_22_open")
    state = _set(state, 0, "service_ssh")
    state = _set(state, 0, "creds_found")
    probs = F.softmax(policy._action_logits(state, host_idx=0), dim=-1)
    assert probs[int(Action.BRUTE_FORCE_SSH)].item() < 1e-6, "no need to brute-force once creds are found"
    assert probs[int(Action.CONNECT_SSH)].item() > 1e-6


def test_mask_matches_is_valid_directly(policy):
    """_build_action_mask should be a pure reflection of rl.actions.is_valid() —
    no separate precondition logic duplicated in policy.py."""
    from rl.actions import is_valid
    state = torch.rand(MAX_HOSTS, NUM_FEATURES)
    host_row = state[0]
    engagement_step = 5
    for full_action_space in (False, True):
        mask = _build_action_mask(host_row, engagement_step, full_action_space)
        features = {feat: bool(host_row[idx]) for feat, idx in FEATURE_INDEX.items()}
        for a in Action:
            expected = is_valid(a, features, engagement_step, full_action_space)
            assert mask[int(a)].item() == (not expected)


def test_full_action_space_constructor_flag_unmasks_exploit_actions():
    """Policy(full_action_space=True) should unmask everything but ABANDON, even
    with nothing known about the host — the experimental toggle for testing
    whether structural preconditions help or hurt learning (2026-07-16)."""
    torch.manual_seed(0)
    policy = Policy(hidden_dim=64, num_layers=2, full_action_space=True)
    state = _set(torch.zeros(MAX_HOSTS, NUM_FEATURES), 0, "is_alive")
    probs = F.softmax(policy._action_logits(state, host_idx=0, engagement_step=0), dim=-1)
    assert probs[int(Action.BRUTE_FORCE_SSH)].item() > 1e-6, "no port known, but full_action_space allows it"
    assert probs[int(Action.CONNECT_SSH)].item() > 1e-6
    assert probs[int(Action.ABANDON)].item() < 1e-6, "ABANDON's gate is independent of full_action_space"


# --- Host conditioning ---

def test_action_head_input_dim(policy):
    assert policy.action_head.in_features == 64 + NUM_FEATURES


def test_logits_vary_by_active_host(policy):
    """Different hosts' feature vectors should produce different action logits —
    the action head must actually be conditioned on the active host, not just
    reading the shared trunk output."""
    state = torch.rand(MAX_HOSTS, NUM_FEATURES)
    logits_0 = policy._action_logits(state, host_idx=0)
    logits_1 = policy._action_logits(state, host_idx=1)
    assert not torch.allclose(logits_0, logits_1)


# --- Sanity ---

def test_num_actions_matches_enum():
    assert NUM_ACTIONS == len(Action)
