import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.distributions import Categorical

from rl.actions import Action, BROADCAST_ACTIONS
from rl.state import FEATURE_INDEX, MAX_HOSTS, NUM_FEATURES

NUM_ACTIONS = len(Action)
NUM_HOST_SLOTS = MAX_HOSTS + 2  # slot 0: no_host, slot 1: all_hosts, slots 2..: discovered hosts

# How many consecutive tries the duration head may commit to for one (host, action)
# selection. Kept small and discrete rather than a continuous output — easier to train
# reliably. Must not exceed the environment's context_window (see ADR 011): a block
# longer than the conversation window would lose visibility into its own earlier tries.
DEFAULT_DURATION_OPTIONS: tuple[int, ...] = (1, 2, 3, 5)

_SOFT_MASK_VALUE = -1e9


def _build_soft_mask() -> torch.Tensor:
    """Precompute structural validity mask of shape [NUM_HOST_SLOTS, NUM_ACTIONS]. True = invalid."""
    mask = torch.zeros(NUM_HOST_SLOTS, NUM_ACTIONS, dtype=torch.bool)
    for h in range(NUM_HOST_SLOTS):
        for a in Action:
            if h == 0:  # no_host: only DO_NOTHING is valid
                if a != Action.DO_NOTHING:
                    mask[h, int(a)] = True
            elif h == 1:  # all_hosts: only SCAN_NETWORK is valid
                if a != Action.SCAN_NETWORK:
                    mask[h, int(a)] = True
            else:  # specific host: broadcast actions are invalid
                if a in BROADCAST_ACTIONS:
                    mask[h, int(a)] = True
    return mask


_SOFT_MASK = _build_soft_mask()

_SHELL_ACCESS_IDX = FEATURE_INDEX["shell_access"]
_CONNECT_ACTION_INDICES = [int(a) for a in (Action.CONNECT_SSH, Action.CONNECT_FTP, Action.CONNECT_TELNET)]


class Policy(nn.Module):
    """
    REINFORCE policy network with host-first factored heads.

    Host head: softmax over NUM_HOST_SLOTS (no_host, all_hosts, host_0..host_{MAX_HOSTS-1}).
    Action head: softmax over NUM_ACTIONS, with soft masking for structurally invalid
    (host_slot, action) combinations.

    Both heads share a single MLP trunk over the flattened state tensor.

    When conditioned_action_head=False (default), the heads are parallel: the action head
    receives only the trunk output and cannot condition on the selected host's features.

    When conditioned_action_head=True, the selected host's feature vector is concatenated
    to the trunk output before the action head, enabling per-host action preferences.
    This allows the policy to learn to skip a host whose tried_* features indicate prior
    failure, rather than learning only global action preferences.

    Duration head: softmax over duration_options (default (1, 2, 3, 5)), conditioned on
    the same input the action head received plus a one-hot of the sampled action — so the
    policy can learn different try-budgets per action (e.g. 1 for PROBE_REDIS, 5 for
    BRUTE_FORCE_SSH) rather than one global value. See ADR 011.
    """

    def __init__(
        self,
        hidden_dim: int = 128,
        num_layers: int = 2,
        conditioned_action_head: bool = False,
        duration_options: tuple[int, ...] | None = None,
    ):
        super().__init__()
        self._conditioned = conditioned_action_head
        self.duration_options = tuple(duration_options) if duration_options else DEFAULT_DURATION_OPTIONS

        layers: list[nn.Module] = []
        in_dim = MAX_HOSTS * NUM_FEATURES
        for _ in range(num_layers):
            layers.extend([nn.Linear(in_dim, hidden_dim), nn.ReLU()])
            in_dim = hidden_dim
        self.trunk = nn.Sequential(*layers)

        self.host_head = nn.Linear(hidden_dim, NUM_HOST_SLOTS)
        action_in_dim = hidden_dim + (NUM_FEATURES if conditioned_action_head else 0)
        self.action_head = nn.Linear(action_in_dim, NUM_ACTIONS)
        self.duration_head = nn.Linear(action_in_dim + NUM_ACTIONS, len(self.duration_options))

    def _masked_host_logits(self, hidden: torch.Tensor, known_host_count: int) -> torch.Tensor:
        logits = self.host_head(hidden)
        if known_host_count < MAX_HOSTS:
            empty = torch.zeros(NUM_HOST_SLOTS, dtype=torch.bool, device=logits.device)
            empty[2 + known_host_count:] = True
            logits = logits.masked_fill(empty, float("-inf"))
        return logits

    def _action_input(
        self, hidden: torch.Tensor, host_slot: int, state: torch.Tensor
    ) -> torch.Tensor:
        """Return the input vector for the action head.

        In parallel mode, this is just the trunk output. In conditioned mode, the selected
        host's feature vector is appended. Broadcast slots (no_host, all_hosts) get a zero
        vector in place of host features so the action head input dimension stays constant.
        """
        if not self._conditioned:
            return hidden
        if host_slot >= 2:
            host_features = state[host_slot - 2]
        else:
            host_features = torch.zeros(NUM_FEATURES, device=hidden.device)
        return torch.cat([hidden, host_features])

    def _masked_action_logits(
        self, action_input: torch.Tensor, host_slot: int, state: torch.Tensor
    ) -> torch.Tensor:
        logits = self.action_head(action_input)
        soft_mask = _SOFT_MASK[host_slot].clone().to(logits.device)
        if host_slot >= 2 and state[host_slot - 2, _SHELL_ACCESS_IDX].bool():
            soft_mask[_CONNECT_ACTION_INDICES] = True
        return logits.masked_fill(soft_mask, _SOFT_MASK_VALUE)

    def _duration_input(self, action_input: torch.Tensor, action_idx: int) -> torch.Tensor:
        """Return the input vector for the duration head: action_input plus a one-hot of
        the sampled action, so the try-budget can depend on which action was picked."""
        action_one_hot = F.one_hot(torch.tensor(action_idx, device=action_input.device), NUM_ACTIONS).float()
        return torch.cat([action_input, action_one_hot])

    def sample(
        self, state: torch.Tensor, known_host_count: int
    ) -> tuple[Action, int, int, torch.Tensor, torch.Tensor]:
        """Sample (action, host_slot, duration, log_prob, entropy) for the REINFORCE training loop.

        log_prob = log p(host | state) + log p(action | host, state) + log p(duration | host, action, state)

        Entropy is the sum of all three heads' entropies — a proxy for overall policy
        uncertainty. Collapse toward zero signals the policy has stopped exploring.
        """
        hidden = self.trunk(state.flatten())

        host_logits = self._masked_host_logits(hidden, known_host_count)
        host_dist = Categorical(logits=host_logits)
        host_slot = host_dist.sample()
        host_slot_int = host_slot.item()

        action_input = self._action_input(hidden, host_slot_int, state)
        action_logits = self._masked_action_logits(action_input, host_slot_int, state)
        action_dist = Categorical(logits=action_logits)
        action_idx = action_dist.sample()

        duration_input = self._duration_input(action_input, action_idx.item())
        duration_logits = self.duration_head(duration_input)
        duration_dist = Categorical(logits=duration_logits)
        duration_idx = duration_dist.sample()
        duration = self.duration_options[duration_idx.item()]

        log_prob = (
            host_dist.log_prob(host_slot)
            + action_dist.log_prob(action_idx)
            + duration_dist.log_prob(duration_idx)
        )
        entropy = host_dist.entropy() + action_dist.entropy() + duration_dist.entropy()
        return Action(action_idx.item()), host_slot_int, duration, log_prob, entropy

    def predict(self, state: torch.Tensor, known_host_count: int) -> tuple[Action, int, int]:
        """Argmax selection for evaluation; no gradient computation."""
        with torch.no_grad():
            hidden = self.trunk(state.flatten())
            host_slot = self._masked_host_logits(hidden, known_host_count).argmax().item()
            action_input = self._action_input(hidden, host_slot, state)
            action_idx = self._masked_action_logits(action_input, host_slot, state).argmax().item()
            duration_input = self._duration_input(action_input, action_idx)
            duration_idx = self.duration_head(duration_input).argmax().item()
            duration = self.duration_options[duration_idx]
        return Action(action_idx), host_slot, duration
