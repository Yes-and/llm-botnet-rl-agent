import pytest
from rl.reward import RewardCalculator, ExploitEvent, EXPLOIT_REWARD, STEP_PENALTY


def test_step_penalty_with_no_exploit():
    calc = RewardCalculator()
    assert calc.step() == pytest.approx(STEP_PENALTY)


def test_exploit_reward_plus_step_penalty():
    calc = RewardCalculator()
    event = ExploitEvent(host="10.0.0.1", vulnerability="ssh_weak_credentials")
    assert calc.step(exploit=event) == pytest.approx(EXPLOIT_REWARD + STEP_PENALTY)


def test_exploit_count_increments():
    calc = RewardCalculator()
    event = ExploitEvent(host="10.0.0.1", vulnerability="ssh_weak_credentials")
    calc.step(exploit=event)
    assert calc.exploit_counts[("10.0.0.1", "ssh_weak_credentials")] == 1


def test_farming_not_detected_on_first_exploit():
    calc = RewardCalculator()
    calc.step(exploit=ExploitEvent(host="10.0.0.1", vulnerability="ssh_weak_credentials"))
    assert not calc.farming_detected()


def test_farming_detected_on_duplicate_exploit():
    calc = RewardCalculator()
    event = ExploitEvent(host="10.0.0.1", vulnerability="ssh_weak_credentials")
    calc.step(exploit=event)
    calc.step(exploit=event)
    assert calc.farming_detected()


def test_farming_not_detected_across_different_hosts():
    calc = RewardCalculator()
    calc.step(exploit=ExploitEvent(host="10.0.0.1", vulnerability="ssh_weak_credentials"))
    calc.step(exploit=ExploitEvent(host="10.0.0.2", vulnerability="ssh_weak_credentials"))
    assert not calc.farming_detected()


def test_farming_not_detected_across_different_vulnerabilities():
    calc = RewardCalculator()
    calc.step(exploit=ExploitEvent(host="10.0.0.1", vulnerability="ssh_weak_credentials"))
    calc.step(exploit=ExploitEvent(host="10.0.0.1", vulnerability="ftp_anonymous"))
    assert not calc.farming_detected()


def test_reset_clears_counts():
    calc = RewardCalculator()
    calc.step(exploit=ExploitEvent(host="10.0.0.1", vulnerability="ssh_weak_credentials"))
    calc.reset()
    assert calc.exploit_counts == {}
    assert not calc.farming_detected()
