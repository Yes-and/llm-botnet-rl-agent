import random
from dataclasses import dataclass, field

import torch

from rl.actions import Action, NO_COVERAGE_ACTIONS

MAX_HOSTS = 16

KNOWLEDGE_FEATURES = [
    "is_alive",
    "port_21_open",
    "port_22_open",
    "port_23_open",
    "port_80_open",
    "port_443_open",
    "port_6379_open",
    "port_27017_open",
    "service_ssh",
    "service_telnet",
    "service_http",
    "service_ftp",
    "creds_found",
    "shell_access",
    "is_root",
]

COVERAGE_FEATURES = [
    f"tried_{action.name.lower()}" for action in Action if action not in NO_COVERAGE_ACTIONS
]

ALL_FEATURES = KNOWLEDGE_FEATURES + COVERAGE_FEATURES
NUM_FEATURES = len(ALL_FEATURES)
FEATURE_INDEX = {name: i for i, name in enumerate(ALL_FEATURES)}


@dataclass
class EpisodeState:
    """
    Attacker-side state for one episode.

    Hosts are stored by IP. Each host is assigned a random tensor slot on first
    discovery, so the policy cannot use slot position as a proxy for identity.
    At most MAX_HOSTS hosts are tracked; additional discovered hosts are ignored.
    """
    _hosts: dict[str, list[float]] = field(default_factory=dict)
    _host_slots: dict[str, int] = field(default_factory=dict)
    _available_slots: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.reset()

    def _get_or_add(self, ip: str) -> list[float] | None:
        """Return the feature vector for ip, adding it if capacity allows."""
        if ip not in self._hosts:
            if not self._available_slots:
                return None
            self._host_slots[ip] = self._available_slots.pop()
            self._hosts[ip] = [0.0] * NUM_FEATURES
        return self._hosts[ip]

    def set(self, ip: str, feature: str, value: bool) -> None:
        """Set a single feature for a host."""
        vec = self._get_or_add(ip)
        if vec is None:
            return
        vec[FEATURE_INDEX[feature]] = 1.0 if value else 0.0

    def update(self, ip: str, features: dict[str, bool]) -> None:
        """Set multiple features for a host at once."""
        for feature, value in features.items():
            self.set(ip, feature, value)

    def get(self, ip: str, feature: str) -> bool:
        """Return the current value of a feature for a host."""
        vec = self._hosts.get(ip)
        if vec is None:
            return False
        return bool(vec[FEATURE_INDEX[feature]])

    def mark_tried(self, ip: str, action: Action) -> None:
        """Record that an action was attempted against a host."""
        self.set(ip, f"tried_{action.name.lower()}", True)

    def host_features(self, ip: str) -> dict[str, bool]:
        """Return knowledge features for a host as a dict (for action masking)."""
        vec = self._hosts.get(ip)
        if vec is None:
            return {}
        return {
            feat: bool(vec[FEATURE_INDEX[feat]])
            for feat in KNOWLEDGE_FEATURES
        }

    def remove(self, ip: str) -> None:
        """Remove a solved host from the pool (ADR 014: hosts leave the pool on
        success rather than being renamed or reused — prevents farming the same
        EXPLOIT_REWARD by re-engaging an already-compromised host)."""
        if ip not in self._hosts:
            return
        slot = self._host_slots.pop(ip)
        del self._hosts[ip]
        self._available_slots.append(slot)

    def known_hosts(self) -> list[str]:
        """Return known host IPs sorted by their assigned tensor slot."""
        return sorted(self._hosts.keys(), key=lambda ip: self._host_slots[ip])

    def to_tensor(self) -> torch.Tensor:
        """
        Return state as a float tensor of shape [MAX_HOSTS, NUM_FEATURES].
        Hosts fill rows 0..n-1 in slot order; unused rows are zero-padded.
        Slot values are sort keys only — they are not used as absolute row indices.
        """
        matrix = torch.zeros(MAX_HOSTS, NUM_FEATURES)
        for i, ip in enumerate(self.known_hosts()):
            matrix[i] = torch.tensor(self._hosts[ip])
        return matrix

    def reset(self) -> None:
        self._hosts.clear()
        self._host_slots.clear()
        self._available_slots = list(range(MAX_HOSTS))
        random.shuffle(self._available_slots)
