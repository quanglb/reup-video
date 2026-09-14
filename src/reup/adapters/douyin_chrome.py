"""Quét lại kênh Douyin bằng Chrome thật của người dùng, qua AppleScript.

API web của Douyin trả body rỗng cho request không mang cookie đăng nhập, nên
reup không tự gọi mà mở kênh trong profile Chrome đã đăng nhập, chạy
`douyin_chrome.js` trong tab đó, rồi đọc JSON về từng đoạn.

Cần một lần trong Chrome: View → Developer → Allow JavaScript from Apple Events,
và cho phép ứng dụng chạy reup điều khiển Google Chrome (macOS tự hỏi).
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Callable

from reup.adapters.douyin_export import parse_export

JS = Path(__file__).with_name("douyin_chrome.js")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE = "Default"  # profile quangxa14@gmail.com, đã đăng nhập Douyin
CHUNK = 200_000
NO_TAB = "__no_tab__"
CLOSE = "__close__"

HINT_JS = (
    "Chrome đang tắt chạy JavaScript từ AppleScript. Bật một lần: trong Chrome, "
    "menu View → Developer → Allow JavaScript from Apple Events, rồi bấm Quét lại. "
    "Việc này phải làm trực tiếp trên máy server, không làm từ xa được."
)
HINT_AUTH = (
    "macOS chưa cho reup điều khiển Google Chrome. Mở System Settings → Privacy & "
    "Security → Automation, bật Google Chrome cho ứng dụng đang chạy reup. "
    "Việc này phải làm trực tiếp trên máy server, không làm từ xa được."
)

# Tìm tab theo một đoạn URL rồi chạy JS trong đó (hoặc đóng tab). Duyệt mọi cửa
# sổ vì AppleScript không cho biết cửa sổ thuộc profile nào; đoạn URL có dấu
# riêng của lần quét nên không đụng tab khác của người dùng.
_IN_TAB = """
on run argv
  set needle to item 1 of argv
  set js to item 2 of argv
  tell application "Google Chrome"
    repeat with w in windows
      repeat with t in tabs of w
        if (URL of t) contains needle then
          if js is "__close__" then
            close t
            return "closed"
          end if
          return execute t javascript js
        end if
      end repeat
    end repeat
  end tell
  return "__no_tab__"
end run
"""


class ChromeError(RuntimeError):
    pass


def osascript(needle: str, js: str) -> str:
    try:
        proc = subprocess.run(
            ["osascript", "-e", _IN_TAB, needle, js],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ChromeError(f"không gọi được Chrome qua AppleScript: {exc}") from exc
    if proc.returncode != 0:
        err = proc.stderr.strip()
        if "JavaScript" in err:
            raise ChromeError(HINT_JS)
        if "-1743" in err or "not allowed" in err.lower() or "not authorized" in err.lower():
            raise ChromeError(HINT_AUTH)
        raise ChromeError(f"AppleScript lỗi: {err}")
    return proc.stdout.rstrip("\n")


def open_in_chrome(url: str, profile: str = PROFILE) -> None:
    """Chrome đang chạy thì lệnh này chỉ chuyển URL cho đúng profile rồi thoát."""
    try:
        subprocess.run(
            [CHROME, f"--profile-directory={profile}", url],
            capture_output=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        pass  # Chrome chưa chạy: tiến trình này chính là Chrome, cứ để nó chạy
    except OSError as exc:
        raise ChromeError(f"không mở được Google Chrome: {exc}") from exc


def _state(text: str) -> dict:
    if text == NO_TAB:
        raise ChromeError("tab quét Douyin đã bị đóng giữa chừng.")
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ChromeError(f"tab Douyin trả trạng thái lạ: {text[:200]}") from exc


def export_channel(
    uid: str,
    *,
    profile: str = PROFILE,
    run: Callable[[str, str], str] = osascript,
    launch: Callable[[str, str], None] = open_in_chrome,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    timeout: float = 900,
    on_progress: Callable[[int], None] | None = None,
) -> dict:
    """Mở kênh, quét hết video, trả dict định dạng reup-douyin/1. Luôn đóng tab."""
    needle = f"reup_rescan={time.time_ns()}"
    launch(f"https://www.douyin.com/user/{uid}?{needle}", profile)
    try:
        deadline = clock() + 45
        while run(needle, "document.readyState") not in ("interactive", "complete"):
            if clock() > deadline:
                raise ChromeError(
                    f"Chrome không mở được tab kênh sau 45 giây (profile {profile})."
                )
            sleep(1)

        script = JS.read_text(encoding="utf-8")
        deadline = clock() + timeout
        while True:
            st = _state(run(needle, script))
            if on_progress:
                on_progress(int(st.get("videos") or 0))
            if st.get("status") == "done":
                break
            if st.get("status") == "error":
                raise ChromeError(f"Douyin: {st.get('error')}")
            if clock() > deadline:
                raise ChromeError(f"quét kênh quá {int(timeout)} giây, dừng.")
            sleep(2)

        body = "".join(
            run(needle, f"window.__reupExport.json.slice({i}, {i + CHUNK})")
            for i in range(0, int(st["size"]), CHUNK)
        )
    finally:
        try:
            run(needle, CLOSE)
        except ChromeError:
            pass

    try:
        raw = json.loads(body)
    except ValueError as exc:
        raise ChromeError(f"JSON đọc từ Chrome bị hỏng ({len(body)} ký tự)") from exc
    parse_export(raw)
    return raw
