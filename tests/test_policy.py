import torch
import torch.nn.functional as F
import pytest

from rl.actions import Action, BROADCAST_ACTIONS
from rl.policy import Policy, NUM_HOST_SLOTS, NUM_ACTIONS, _SOFT_MASK
from rl.state import MAX_HOSTS, NUM_FEATURES


@pytest.fixture
def policy():
    torch.manual_seed(0)
    return Policy(hidden_dim=64, num_layers=2)


@pytest.fixture
def zero_state():
    return torch.zeros(MAX_HOSTS, NUM_FEATURES)


# --- Return types ---

def test_sample_return_types(policy, zero_state):
    action, host_slot, log_prob = policy.sample(zero_state, known_host_count=2)
    assert isinstance(action, Action)
    assert isinstance(host_slot, int)
    assert isinstance(log_prob, torch.Tensor)
    assert log_prob.shape == ()  # scalar


def test_predict_return_types(policy, zero_state):
    action, host_slot = policy.predict(zero_state, known_host_count=2)
    assert isinstance(action, Action)
    assert isinstance(host_slot, int)


# --- Hard masking ---

def test_hard_mask_empty_slots(policy, zero_state):
    known_host_count = 2
    hidden = policy.trunk(zero_state.flatten())
    probs = F.softmax(policy._masked_host_logits(hidden, known_host_count), dim=-1)
    assert probs[2 + known_host_count:].sum().item() == pytest.approx(0.0)
    assert probs[: 2 + known_host_count].sum().item() == pytest.approx(1.0)


def test_hard_mask_no_hosts(policy, zero_state):
    hidden = policy.trunk(zero_state.flatten())
    probs = F.softmax(policy._masked_host_logits(hidden, known_host_count=0), dim=-1)
    assert probs[2:].sum().item() == pytest.approx(0.0)
    assert probs[:2].sum().item() == pytest.approx(1.0)


def test_hard_mask_full_hosts(policy):
    state = torch.rand(MAX_HOSTS, NUM_FEATURES)
    hidden = policy.trunk(state.flatten())
    logits = policy._masked_host_logits(hidden, known_host_count=MAX_HOSTS)
    assert not torch.isinf(logits).any()


# --- Soft masking ---

def test_soft_mask_no_host_slot(policy, zero_state):
    hidden = policy.trunk(zero_state.flatten())
    probs = F.softmax(policy._masked_action_logits(hidden, host_slot=0), dim=-1)
    for a in Action:
        if a != Action.DO_NOTHING:
            assert probs[int(a)].item() < 1e-6, f"{a.name} should be near-zero for no_host"


def test_soft_mask_all_hosts_slot(policy, zero_state):
    hidden = policy.trunk(zero_state.flatten())
    probs = F.softmax(policy._masked_action_logits(hidden, host_slot=1), dim=-1)
    for a in Action:
        if a != Action.SCAN_NETWORK:
            assert probs[int(a)].item() < 1e-6, f"{a.name} should be near-zero for all_hosts"


def test_soft_mask_specific_host_slot(policy, zero_state):
    hidden = policy.trunk(zero_state.flatten())
    probs = F.softmax(policy._masked_action_logits(hidden, host_slot=2), dim=-1)
    for a in BROADCAST_ACTIONS:
        assert probs[int(a)].item() < 1e-6, f"{a.name} should be near-zero for specific host"


# --- log_prob validity ---

def test_log_prob_is_scalar(policy, zero_state):
    _, _, log_prob = policy.sample(zero_state, known_host_count=3)
    assert log_prob.ndim == 0


def test_log_prob_is_negative(policy, zero_state):
    _, _, log_prob = policy.sample(zero_state, known_host_count=3)
    assert log_prob.item() < 0.0  # log of probability < 1


# --- Determinism ---

def test_predict_is_deterministic(policy, zero_state):
    assert policy.predict(zero_state, known_host_count=3) == policy.predict(zero_state, known_host_count=3)


# --- Sampled values in range ---

def test_sample_host_slot_in_range(policy, zero_state):
    known_host_count = 3
    for _ in range(20):
        _, host_slot, _ = policy.sample(zero_state, known_host_count=known_host_count)
        assert 0 <= host_slot < 2 + known_host_count


# --- Soft mask tensor shape ---

def test_soft_mask_shape():
    assert _SOFT_MASK.shape == (NUM_HOST_SLOTS, NUM_ACTIONS)
