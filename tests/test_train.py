import pytest
from scripts.train import _compute_returns, _visitation_bonus


def test_visitation_bonus_first_occurrence_is_one():
    assert _visitation_bonus(1) == pytest.approx(1.0)


def test_visitation_bonus_decays_with_count():
    assert _visitation_bonus(4) == pytest.approx(0.5)
    assert _visitation_bonus(9) == pytest.approx(1.0 / 3.0)


def test_visitation_bonus_monotonically_decreasing():
    counts = [1, 2, 3, 5, 10, 50]
    bonuses = [_visitation_bonus(c) for c in counts]
    assert bonuses == sorted(bonuses, reverse=True)


def test_compute_returns_discounting():
    # rewards [1, 1, 1] with gamma=0.5:
    # G2 = 1, G1 = 1 + 0.5*1 = 1.5, G0 = 1 + 0.5*1.5 = 1.75
    returns = _compute_returns([1.0, 1.0, 1.0], gamma=0.5)
    assert returns.tolist() == pytest.approx([1.75, 1.5, 1.0])
