import torch
import torch.nn as nn
from torch.distributions import Categorical

from rl.actions import Action, is_valid
from rl.state import FEATURE_INDEX, KNOWLEDGE_FEATURES, MAX_HOSTS, NUM_FEATURES

NUM_ACTIONS = len(Action)

_SOFT_MASK_VALUE = -1e9


def _host_feature_dict(host_row: torch.Tensor) -> dict[str, bool]:
    return {feat: bool(host_row[FEATURE_INDEX[feat]]) for feat in KNOWLEDGE_FEATURES}


def _build_action_mask(
    host_row: torch.Tensor, engagement_step: int, full_action_space: bool
) -> torch.Tensor:
    """Boolean mask of shape [NUM_ACTIONS], True = invalid, via rl.actions.is_valid().

    Recomputed from the active host's current features on every call — as the
    engagement discovers open ports/services/creds, the mask tightens or loosens
    accordingly, so the policy only ever chooses among actions that make sense
    given what's currently known about this one host.
    """
    features = _host_feature_dict(host_row)
    return torch.tensor(
        [not is_valid(a, features, engagement_step, full_action_space) for a in Action],
        dtype=torch.bool, device=host_row.device,
    )


class Policy(nn.Module):
    """
    REINFORCE policy network for ADR 014 Phase 1 (single-host engagement, worker only).

    One action head, conditioned on the active host's feature vector (reuses ADR
    010's conditioning mechanism — now mandatory rather than a toggle, since every
    decision is host-scoped). Softmax over NUM_ACTIONS with is_valid()-based
    masking: the mask tightens as the active host's state fills in over the
    engagement, from "only recon actions valid" right after discovery to whichever
    exploit paths the discovered ports/services/creds actually support.
    ABANDON is separately gated on engagement_step (see MIN_STEPS_BEFORE_ABANDON).

    full_action_space=True (2026-07-16 experimental toggle) disables every
    precondition above except ABANDON's — every other action is valid from step
    one, and it's on the LLM's own judgment (plus the -0.1 step cost) to recognize
    when it doesn't have enough context yet. See rl.actions.is_valid() docstring.

    Host selection is not learned yet — that's ADR 014 Phase 2. The caller (the
    training loop, in Phase 1) picks which host to engage and passes its row
    index into the state tensor; already-compromised hosts are never passed in
    because the environment removes them from the pool on success.
    """

    def __init__(self, hidden_dim: int = 128, num_layers: int = 2, full_action_space: bool = False):
        super().__init__()
        self.full_action_space = full_action_space
        layers: list[nn.Module] = []
        in_dim = MAX_HOSTS * NUM_FEATURES
        for _ in range(num_layers):
            layers.extend([nn.Linear(in_dim, hidden_dim), nn.ReLU()])
            in_dim = hidden_dim
        self.trunk = nn.Sequential(*layers)
        self.action_head = nn.Linear(hidden_dim + NUM_FEATURES, NUM_ACTIONS)

    def _action_logits(self, state: torch.Tensor, host_idx: int, engagement_step: int = 0) -> torch.Tensor:
        hidden = self.trunk(state.flatten())
        host_row = state[host_idx]
        logits = self.action_head(torch.cat([hidden, host_row]))
        mask = _build_action_mask(host_row, engagement_step, self.full_action_space)
        return logits.masked_fill(mask, _SOFT_MASK_VALUE)

    def sample(
        self, state: torch.Tensor, host_idx: int, engagement_step: int = 0
    ) -> tuple[Action, torch.Tensor, torch.Tensor]:
        """Sample (action, log_prob, entropy) for the active host at host_idx.

        engagement_step: steps already completed this engagement (env.engagement_step_count)
        — needed for the ABANDON mask, see rl.actions.is_valid().

        Entropy is a proxy for policy uncertainty on this decision — collapse
        toward zero signals the policy has stopped exploring.
        """
        dist = Categorical(logits=self._action_logits(state, host_idx, engagement_step))
        action_idx = dist.sample()
        return Action(action_idx.item()), dist.log_prob(action_idx), dist.entropy()

    def predict(self, state: torch.Tensor, host_idx: int, engagement_step: int = 0) -> Action:
        """Argmax selection for evaluation; no gradient computation."""
        with torch.no_grad():
            action_idx = self._action_logits(state, host_idx, engagement_step).argmax().item()
        return Action(action_idx)
