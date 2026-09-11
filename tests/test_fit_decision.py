import pytest

from reup.fit import decide_fit


def test_much_shorter_than_slot_pads():
    d = decide_fit(actual_ms=1000, slot_ms=3000, revision=0)
    assert d.action == "pad"
    assert d.tempo == 1.0


def test_slightly_long_compresses_with_tempo():
    d = decide_fit(actual_ms=3300, slot_ms=3000, revision=0)
    assert d.action == "tempo"
    assert d.tempo == pytest.approx(1.1)


def test_exact_fit_uses_tempo_one():
    d = decide_fit(actual_ms=3000, slot_ms=3000, revision=0)
    assert d.action == "tempo"
    assert d.tempo == 1.0


def test_slightly_short_but_within_band_does_not_slow_down():
    d = decide_fit(actual_ms=2700, slot_ms=3000, revision=0)
    assert d.action == "tempo"
    assert d.tempo == 1.0  # không kéo chậm giọng — nghe lè nhè


def test_too_long_asks_for_rewrite_while_budget_remains():
    d = decide_fit(actual_ms=4200, slot_ms=3000, revision=0)
    assert d.action == "rewrite"


def test_rewrite_budget_is_exhausted_at_max():
    d = decide_fit(actual_ms=4200, slot_ms=3000, revision=2)
    assert d.action == "tempo_capped"
    assert d.tempo == 1.25


def test_way_too_long_after_budget_is_overflow():
    d = decide_fit(actual_ms=9000, slot_ms=3000, revision=2)
    assert d.action == "overflow"
    assert d.tempo == 1.25


def test_way_too_long_still_tries_rewrite_first():
    assert decide_fit(actual_ms=9000, slot_ms=3000, revision=1).action == "rewrite"


@pytest.mark.parametrize("revision", [0, 1, 2, 5])
def test_ratio_is_always_reported(revision):
    d = decide_fit(actual_ms=6000, slot_ms=3000, revision=revision)
    assert d.ratio == pytest.approx(2.0)


def test_zero_slot_raises():
    with pytest.raises(ValueError, match="slot_ms"):
        decide_fit(actual_ms=1000, slot_ms=0, revision=0)


def test_boundary_115_is_tempo_not_rewrite():
    assert decide_fit(actual_ms=3450, slot_ms=3000, revision=0).action == "tempo"


def test_boundary_150_is_not_overflow():
    assert decide_fit(actual_ms=4500, slot_ms=3000, revision=2).action == "tempo_capped"
