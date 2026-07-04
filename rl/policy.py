import torch
import torch.nn as nn
from torch.distributions import Categorical

from rl.actions import Action, BROADCAST_ACTIONS
from rl.state import MAX_HOSTS, NUM_FEATURES

NUM_ACTIONS = len(Action)
NUM_HOST_SLOTS = MAX_HOSTS + 2  # slot 0: no_host, slot 1: all_hosts, slots 2..: discovered hosts

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
    """

    def __init__(
        self,
        hidden_dim: int = 128,
        num_layers: int = 2,
        conditioned_action_head: bool = False,
    ):
        super().__init__()
        self._conditioned = conditioned_action_head

        layers: list[nn.Module] = []
        in_dim = MAX_HOSTS * NUM_FEATURES
        for _ in range(num_layers):
            layers.extend([nn.Linear(in_dim, hidden_dim), nn.ReLU()])
            in_dim = hidden_dim
        self.trunk = nn.Sequential(*layers)

        self.host_head = nn.Linear(hidden_dim, NUM_HOST_SLOTS)
        action_in_dim = hidden_dim + (NUM_FEATURES if conditioned_action_head else 0)
        self.action_head = nn.Linear(action_in_dim, NUM_ACTIONS)

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
        self, action_input: torch.Tensor, host_slot: int
    ) -> torch.Tensor:
        logits = self.action_head(action_input)
        soft_mask = _SOFT_MASK[host_slot].to(logits.device)
        return logits.masked_fill(soft_mask, _SOFT_MASK_VALUE)

    def sample(
        self, state: torch.Tensor, known_host_count: int
    ) -> tuple[Action, int, torch.Tensor, torch.Tensor]:
        """Sample (action, host_slot, log_prob, entropy) for use in the REINFORCE training loop.

        log_prob = log p(host | state) + log p(action | host, state)

        Entropy is the sum of host and action head entropies — a proxy for overall policy
        uncertainty. Collapse toward zero signals the policy has stopped exploring.
        """
        hidden = self.trunk(state.flatten())

        host_logits = self._masked_host_logits(hidden, known_host_count)
        host_dist = Categorical(logits=host_logits)
        host_slot = host_dist.sample()
        host_slot_int = host_slot.item()

        action_input = self._action_input(hidden, host_slot_int, state)
        action_logits = self._masked_action_logits(action_input, host_slot_int)
        action_dist = Categorical(logits=action_logits)
        action_idx = action_dist.sample()

        log_prob = host_dist.log_prob(host_slot) + action_dist.log_prob(action_idx)
        entropy = host_dist.entropy() + action_dist.entropy()
        return Action(action_idx.item()), host_slot_int, log_prob, entropy

    def predict(self, state: torch.Tensor, known_host_count: int) -> tuple[Action, int]:
        """Argmax selection for evaluation; no gradient computation."""
        with torch.no_grad():
            hidden = self.trunk(state.flatten())
            host_slot = self._masked_host_logits(hidden, known_host_count).argmax().item()
            action_input = self._action_input(hidden, host_slot, state)
            action_idx = self._masked_action_logits(action_input, host_slot).argmax().item()
        return Action(action_idx), host_slot
