import pytest

from reup.text import count_syllables


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Hôm nay mình dạy mọi người làm thịt kho tàu", 10),
        ("Xin chào!", 2),
        ("  nhiều   khoảng   trắng  ", 3),
        ("", 0),
        ("---", 0),
    ],
)
def test_vietnamese_counts_whitespace_tokens(text, expected):
    assert count_syllables(text, "vi") == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("今天教大家做红烧肉", 9),
        ("你好，世界", 4),
        ("", 0),
    ],
)
def test_chinese_counts_han_characters(text, expected):
    assert count_syllables(text, "zh") == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("hello world", 3),
        ("a", 1),
        ("", 0),
    ],
)
def test_english_counts_vowel_groups(text, expected):
    assert count_syllables(text, "en") == expected


def test_unknown_language_falls_back_to_whitespace():
    assert count_syllables("mot hai ba", "xx") == 3
