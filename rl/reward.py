from dataclasses import dataclass, field


EXPLOIT_REWARD = 10.0
STEP_PENALTY = -0.1


@dataclass
class ExploitEvent:
    host: str        # IP address of the exploited host
    vulnerability: str  # e.g. "ssh_weak_credentials"


@dataclass
class RewardCalculator:
    exploit_counts: dict[tuple[str, str], int] = field(default_factory=dict)

    def step(self, exploit: ExploitEvent | None = None) -> float:
        """Return reward for one step. Pass an ExploitEvent if exploitation succeeded."""
        reward = STEP_PENALTY
        if exploit is not None:
            key = (exploit.host, exploit.vulnerability)
            self.exploit_counts[key] = self.exploit_counts.get(key, 0) + 1
            reward += EXPLOIT_REWARD
        return reward

    def reset(self) -> None:
        self.exploit_counts.clear()

    def farming_detected(self) -> bool:
        """True if any (host, vulnerability) pair has been exploited more than once."""
        return any(count > 1 for count in self.exploit_counts.values())
