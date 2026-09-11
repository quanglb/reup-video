import os
from pathlib import Path

from reup.dotenv import load_dotenv, parse_env


def test_parses_simple_pairs():
    assert parse_env("A=1\nB=hai\n") == {"A": "1", "B": "hai"}


def test_ignores_comments_and_blank_lines():
    assert parse_env("# ghi chú\n\nA=1\n  # thụt lề\n") == {"A": "1"}


def test_strips_quotes():
    assert parse_env('A="có khoảng trắng"\nB=\'nháy đơn\'\n') == {
        "A": "có khoảng trắng",
        "B": "nháy đơn",
    }


def test_accepts_export_prefix():
    assert parse_env("export A=1\n") == {"A": "1"}


def test_value_may_contain_equals():
    """Khoá API hay có dấu = ở cuối; cắt ở dấu = đầu tiên mới đúng."""
    assert parse_env("KEY=abc=def==\n") == {"KEY": "abc=def=="}


def test_ignores_lines_without_equals():
    assert parse_env("linh tinh\nA=1\n") == {"A": "1"}


def test_load_sets_environment(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("REUP_TEST_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("REUP_TEST_KEY=abc123\n", encoding="utf-8")

    loaded = load_dotenv(env)

    assert loaded == ["REUP_TEST_KEY"]
    assert os.environ["REUP_TEST_KEY"] == "abc123"


def test_existing_environment_variable_wins(tmp_path: Path, monkeypatch):
    """`GEMINI_API_KEY=... reup run` tạm thời phải đè được file."""
    monkeypatch.setenv("REUP_TEST_KEY", "tu-moi-truong")
    env = tmp_path / ".env"
    env.write_text("REUP_TEST_KEY=tu-file\n", encoding="utf-8")

    loaded = load_dotenv(env)

    assert loaded == []
    assert os.environ["REUP_TEST_KEY"] == "tu-moi-truong"


def test_missing_file_is_not_an_error(tmp_path: Path):
    assert load_dotenv(tmp_path / "khong-co.env") == []


def test_returns_names_not_values(tmp_path: Path, monkeypatch):
    """Hàm này không bao giờ được trả về giá trị khoá — chỗ gọi hay in log."""
    monkeypatch.delenv("REUP_TEST_SECRET", raising=False)
    env = tmp_path / ".env"
    env.write_text("REUP_TEST_SECRET=sieu-bi-mat\n", encoding="utf-8")

    loaded = load_dotenv(env)

    assert loaded == ["REUP_TEST_SECRET"]
    assert "sieu-bi-mat" not in str(loaded)
