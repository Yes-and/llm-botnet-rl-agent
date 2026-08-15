import torch
import torch.nn.functional as F
import pytest
from torch.distributions import Categorical

from rl.actions import Action, BROADCAST_ACTIONS
from rl.policy import (
    Policy,
    NUM_HOST_SLOTS,
    NUM_ACTIONS,
    DEFAULT_DURATION_OPTIONS,
    _SOFT_MASK,
    _SHELL_ACCESS_IDX,
    _CREDS_FOUND_IDX,
)
from rl.state import MAX_HOSTS, NUM_FEATURES


@pytest.fixture
def policy():
    torch.manual_seed(0)
    return Policy(hidden_dim=64, num_layers=2)


@pytest.fixture
def conditioned_policy():
    torch.manual_seed(0)
    return Policy(hidden_dim=64, num_layers=2, conditioned_action_head=True)


@pytest.fixture
def zero_state():
    return torch.zeros(MAX_HOSTS, NUM_FEATURES)


# --- Return types ---

def test_sample_return_types(policy, zero_state):
    action, host_slot, duration, log_prob, entropy = policy.sample(zero_state, known_host_count=2)
    assert isinstance(action, Action)
    assert isinstance(host_slot, int)
    assert isinstance(duration, int)
    assert duration in policy.duration_options
    assert isinstance(log_prob, torch.Tensor)
    assert log_prob.shape == ()
    assert isinstance(entropy, torch.Tensor)
    assert entropy.shape == ()


def test_predict_return_types(policy, zero_state):
    action, host_slot, duration = policy.predict(zero_state, known_host_count=2)
    assert isinstance(action, Action)
    assert isinstance(host_slot, int)
    assert isinstance(duration, int)
    assert duration in policy.duration_options


# --- Hard masking ---

def test_hard_mask_empty_slots(policy, zero_state):
    known_host_count = 2
    hidden = policy.trunk(zero_state.flatten())
    probs = F.softmax(policy._masked_host_logits(hidden, known_host_count, zero_state), dim=-1)
    assert probs[2 + known_host_count:].sum().item() == pytest.approx(0.0)
    assert probs[: 2 + known_host_count].sum().item() == pytest.approx(1.0)


def test_hard_mask_no_hosts(policy, zero_state):
    hidden = policy.trunk(zero_state.flatten())
    probs = F.softmax(policy._masked_host_logits(hidden, known_host_count=0, state=zero_state), dim=-1)
    assert probs[2:].sum().item() == pytest.approx(0.0)
    assert probs[:2].sum().item() == pytest.approx(1.0)


def test_hard_mask_full_hosts(policy):
    # shell_access forced to 0 so this test isolates the empty-slot mask, not the
    # separate shell_access-compromised-host mask exercised by test_shell_access_masks_host_slot.
    state = torch.rand(MAX_HOSTS, NUM_FEATURES)
    state[:, _SHELL_ACCESS_IDX] = 0.0
    hidden = policy.trunk(state.flatten())
    logits = policy._masked_host_logits(hidden, known_host_count=MAX_HOSTS, state=state)
    assert not torch.isinf(logits).any()


# --- Soft masking ---

def test_soft_mask_no_host_slot(policy, zero_state):
    hidden = policy.trunk(zero_state.flatten())
    probs = F.softmax(policy._masked_action_logits(hidden, host_slot=0, state=zero_state), dim=-1)
    for a in Action:
        if a != Action.DO_NOTHING:
            assert probs[int(a)].item() < 1e-6, f"{a.name} should be near-zero for no_host"


def test_soft_mask_all_hosts_slot(policy, zero_state):
    hidden = policy.trunk(zero_state.flatten())
    probs = F.softmax(policy._masked_action_logits(hidden, host_slot=1, state=zero_state), dim=-1)
    for a in Action:
        if a != Action.SCAN_NETWORK:
            assert probs[int(a)].item() < 1e-6, f"{a.name} should be near-zero for all_hosts"


def test_soft_mask_specific_host_slot(policy, zero_state):
    hidden = policy.trunk(zero_state.flatten())
    probs = F.softmax(policy._masked_action_logits(hidden, host_slot=2, state=zero_state), dim=-1)
    for a in BROADCAST_ACTIONS:
        assert probs[int(a)].item() < 1e-6, f"{a.name} should be near-zero for specific host"


def test_shell_access_masks_host_slot(policy, zero_state):
    """A host with shell_access=True must be unselectable by the host head — EXPLOIT_REWARD
    already fired once for it, so no action against it can score again. Enforced at the host
    head (not the action head): masking every action in a row would leave softmax nothing to
    contrast against, collapsing to uniform instead of near-zero (see ADR 012)."""
    known_host_count = 2
    state = zero_state.clone()
    state[0, _SHELL_ACCESS_IDX] = 1.0  # slot 2 maps to row 0 — compromised
    # row 1 (slot 3) left at shell_access=0 — must remain selectable
    hidden = policy.trunk(state.flatten())
    probs = F.softmax(policy._masked_host_logits(hidden, known_host_count, state), dim=-1)
    assert probs[2].item() < 1e-6, "compromised host's slot should be masked"
    assert probs[3].item() > 1e-6, "uncompromised host's slot should remain selectable"


def test_creds_found_masks_brute_force_actions(policy, zero_state):
    """BRUTE_FORCE_* must be near-zero for a host with creds_found=True."""
    state = zero_state.clone()
    state[0, _CREDS_FOUND_IDX] = 1.0  # slot 2 maps to row 0
    hidden = policy.trunk(state.flatten())
    action_input = policy._action_input(hidden, host_slot=2, state=state)
    probs = F.softmax(policy._masked_action_logits(action_input, host_slot=2, state=state), dim=-1)
    for a in (Action.BRUTE_FORCE_SSH, Action.BRUTE_FORCE_FTP, Action.BRUTE_FORCE_TELNET):
        assert probs[int(a)].item() < 1e-6, f"{a.name} should be masked when creds_found=True"


# --- log_prob validity ---

def test_log_prob_is_scalar(policy, zero_state):
    _, _, _, log_prob, _ = policy.sample(zero_state, known_host_count=3)
    assert log_prob.ndim == 0


def test_log_prob_is_negative(policy, zero_state):
    _, _, _, log_prob, _ = policy.sample(zero_state, known_host_count=3)
    assert log_prob.item() < 0.0


# --- Determinism ---

def test_predict_is_deterministic(policy, zero_state):
    assert policy.predict(zero_state, known_host_count=3) == policy.predict(zero_state, known_host_count=3)


# --- Sampled values in range ---

def test_sample_host_slot_in_range(policy, zero_state):
    known_host_count = 3
    for _ in range(20):
        _, host_slot, _, _, _ = policy.sample(zero_state, known_host_count=known_host_count)
        assert 0 <= host_slot < 2 + known_host_count


# --- Soft mask tensor shape ---

def test_soft_mask_shape():
    assert _SOFT_MASK.shape == (NUM_HOST_SLOTS, NUM_ACTIONS)


# --- Conditioned action head ---

def test_conditioned_action_head_dimensions(conditioned_policy):
    """Action head input should be hidden_dim + NUM_FEATURES when conditioned."""
    expected_in = 64 + NUM_FEATURES
    assert conditioned_policy.action_head.in_features == expected_in


def test_conditioned_action_input_specific_host(conditioned_policy):
    """For a specific host slot, _action_input concatenates that host's features."""
    state = torch.rand(MAX_HOSTS, NUM_FEATURES)
    hidden = conditioned_policy.trunk(state.flatten())
    action_input = conditioned_policy._action_input(hidden, host_slot=2, state=state)
    assert action_input.shape == (64 + NUM_FEATURES,)
    assert torch.allclose(action_input[64:], state[0])  # slot 2 → row 0


def test_conditioned_action_input_broadcast_slot_pads_zeros(conditioned_policy):
    """Broadcast slots (no_host, all_hosts) get zero host features."""
    state = torch.rand(MAX_HOSTS, NUM_FEATURES)
    hidden = conditioned_policy.trunk(state.flatten())
    for slot in (0, 1):
        action_input = conditioned_policy._action_input(hidden, host_slot=slot, state=state)
        assert action_input.shape == (64 + NUM_FEATURES,)
        assert torch.all(action_input[64:] == 0.0)


def test_conditioned_action_logits_vary_by_host(conditioned_policy):
    """With conditioning enabled, action logits should differ for different hosts."""
    state = torch.rand(MAX_HOSTS, NUM_FEATURES)
    hidden = conditioned_policy.trunk(state.flatten())
    input_0 = conditioned_policy._action_input(hidden, host_slot=2, state=state)
    input_1 = conditioned_policy._action_input(hidden, host_slot=3, state=state)
    logits_0 = conditioned_policy._masked_action_logits(input_0, host_slot=2, state=state)
    logits_1 = conditioned_policy._masked_action_logits(input_1, host_slot=3, state=state)
    assert not torch.allclose(logits_0, logits_1)


def test_conditioned_sample_returns_valid_types(conditioned_policy):
    state = torch.rand(MAX_HOSTS, NUM_FEATURES)
    action, host_slot, duration, log_prob, entropy = conditioned_policy.sample(state, known_host_count=4)
    assert isinstance(action, Action)
    assert isinstance(host_slot, int)
    assert duration in conditioned_policy.duration_options
    assert log_prob.ndim == 0
    assert log_prob.item() < 0.0


# --- Duration head ---

def test_duration_head_dimensions(policy):
    """Duration head input = action head input dim + one-hot(action); output = menu size."""
    expected_in = policy.action_head.in_features + NUM_ACTIONS
    assert policy.duration_head.in_features == expected_in
    assert policy.duration_head.out_features == len(DEFAULT_DURATION_OPTIONS)


def test_default_duration_options(policy):
    assert policy.duration_options == DEFAULT_DURATION_OPTIONS


def test_custom_duration_options():
    torch.manual_seed(0)
    custom = Policy(hidden_dim=64, num_layers=2, duration_options=(1, 4))
    assert custom.duration_options == (1, 4)
    assert custom.duration_head.out_features == 2
    for _ in range(20):
        _, _, duration, _, _ = custom.sample(torch.zeros(MAX_HOSTS, NUM_FEATURES), known_host_count=2)
        assert duration in (1, 4)


def test_predict_duration_is_deterministic(policy, zero_state):
    assert policy.predict(zero_state, known_host_count=3) == policy.predict(zero_state, known_host_count=3)


def test_duration_logits_vary_by_action(policy):
    """Duration head input includes a one-hot of the sampled action, so logits should differ per action."""
    state = torch.rand(MAX_HOSTS, NUM_FEATURES)
    hidden = policy.trunk(state.flatten())
    action_input = policy._action_input(hidden, host_slot=2, state=state)
    logits_for_action_0 = policy.duration_head(policy._duration_input(action_input, action_idx=0))
    logits_for_action_1 = policy.duration_head(policy._duration_input(action_input, action_idx=1))
    assert not torch.allclose(logits_for_action_0, logits_for_action_1)


def test_sample_log_prob_and_entropy_include_duration_term(policy, zero_state):
    """log_prob/entropy from sample() should exactly equal the manually composed
    host + action + duration terms — i.e. duration is really folded in, not dropped."""
    torch.manual_seed(7)
    action, host_slot, duration, log_prob, entropy = policy.sample(zero_state, known_host_count=3)

    torch.manual_seed(7)
    hidden = policy.trunk(zero_state.flatten())
    host_dist = Categorical(logits=policy._masked_host_logits(hidden, known_host_count=3, state=zero_state))
    sampled_host = host_dist.sample()

    action_input = policy._action_input(hidden, sampled_host.item(), zero_state)
    action_dist = Categorical(logits=policy._masked_action_logits(action_input, sampled_host.item(), zero_state))
    sampled_action = action_dist.sample()

    duration_input = policy._duration_input(action_input, sampled_action.item())
    duration_dist = Categorical(logits=policy.duration_head(duration_input))
    sampled_duration_idx = duration_dist.sample()

    expected_log_prob = (
        host_dist.log_prob(sampled_host)
        + action_dist.log_prob(sampled_action)
        + duration_dist.log_prob(sampled_duration_idx)
    )
    expected_entropy = host_dist.entropy() + action_dist.entropy() + duration_dist.entropy()

    assert torch.allclose(log_prob, expected_log_prob)
    assert torch.allclose(entropy, expected_entropy)
    assert action == Action(sampled_action.item())
    assert host_slot == sampled_host.item()
    assert duration == policy.duration_options[sampled_duration_idx.item()]
