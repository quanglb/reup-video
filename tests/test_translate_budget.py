import pytest

from reup.translate import DEFAULT_SYLLABLE_RATE, fits_budget, syllable_budget


def test_budget_scales_with_slot_length():
    # 4.5 âm tiết/giây: khe 2 giây chứa được 9 âm tiết
    assert syllable_budget(2000) == 9
    assert syllable_budget(4000) == 18


def test_budget_rounds_down_never_up():
    """Làm tròn lên là cấp thừa chỗ, dẫn tới đoạn dài quá khe."""
    # 2.2s x 4.5 = 9.9 -> 9, không phải 10
    assert syllable_budget(2200) == 9


def test_very_short_slot_still_allows_one_syllable():
    """Khe 100ms không đủ cho âm tiết nào, nhưng trần 0 làm dịch thành rỗng."""
    assert syllable_budget(100) == 1
    assert syllable_budget(1) == 1


def test_zero_or_negative_slot_rejected():
    with pytest.raises(ValueError, match="slot_ms"):
        syllable_budget(0)
    with pytest.raises(ValueError, match="slot_ms"):
        syllable_budget(-500)


def test_rate_is_overridable():
    assert syllable_budget(2000, rate=5.0) == 10


def test_default_rate_matches_measured_capcut_speed():
    """CapCut đo được ~225 ms/âm tiết = 4.44 âm tiết/giây. Spec chốt 4.5."""
    assert DEFAULT_SYLLABLE_RATE == 4.5


@pytest.mark.parametrize(
    "text,budget,expected",
    [
        ("Hôm nay trời đẹp", 9, True),
        ("Hôm nay trời đẹp", 4, True),
        ("Hôm nay trời đẹp", 3, False),
        ("", 1, True),
    ],
)
def test_fits_budget_counts_vietnamese_syllables(text, budget, expected):
    assert fits_budget(text, budget) is expected
