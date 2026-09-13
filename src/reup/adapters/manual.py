"""Nguồn thủ công: người dùng dán link, yt-dlp tải về.

Đây là adapter luôn sống. Ba crawler trending ở phase 6 có gãy thì
đường này vẫn chạy, nên pipeline không bao giờ tắc hoàn toàn.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from reup.adapters.crawl import cookie_args
from reup.adapters.source import Candidate, FetchResult

# Mặc định: lấy bản mp4 nhỏ nhất, kể cả AV1.
#
# Đo thật trên M4 với một Shorts 18 giây 1080x1920:
#     AV1   nguồn 3.68MB → final 6.17MB, compose 4906ms
#     h264  nguồn 7.90MB → final 12.74MB, compose 4951ms
# Hardware AV1 decode làm khâu decode gần như miễn phí (chênh 45ms, trong sai
# số), trong khi bản h264 của YouTube nặng gấp đôi nên tải lâu hơn và — vì
# compose chọn bitrate theo nguồn — file ra cũng phình gấp đôi theo.
FORMAT_SELECTOR = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b"

# Chỉ dùng cho máy KHÔNG có hardware AV1 decode (Intel, M1, M2), nơi decode AV1
# rơi xuống CPU. Bật bằng `prefer_h264 = true` trong profile.
FORMAT_SELECTOR_H264 = (
    "bv*[vcodec^=avc1]+ba[ext=m4a]"
    "/b[vcodec^=avc1]"
    "/bv*[ext=mp4]+ba[ext=m4a]"
    "/b[ext=mp4]"
    "/b"
)


def format_selector(prefer_h264: bool) -> str:
    return FORMAT_SELECTOR_H264 if prefer_h264 else FORMAT_SELECTOR


# YouTube giờ bắt giải một "JS challenge" mới trả link tải. yt-dlp cần hai thứ
# cho việc đó: một JS runtime, và script giải challenge (EJS).
#
# Thiếu runtime thì yt-dlp tụt xuống player visionos và YouTube trả thẳng
# "This video is not available" — thông báo nghe như video bị gỡ, nhưng mọi
# video đều hỏng như nhau. Có runtime mà thiếu EJS thì báo "n challenge solving
# failed" rồi vẫn hỏng.
#
# deno là runtime duy nhất yt-dlp tự bật; node và bun phải khai bằng tên.
JS_RUNTIMES = ("deno", "node", "bun")

# EJS tải script giải challenge từ GitHub lúc chạy. Đây là mã ngoài chạy trên
# máy mình — yt-dlp khuyến nghị bật, nhưng ai không muốn thì đặt
# `fetch.remote_components = ""` trong config.toml và chấp nhận YouTube hỏng.
DEFAULT_REMOTE_COMPONENTS = "ejs:github"


def js_runtime_args(runtime: str = "") -> list[str]:
    """Khai JS runtime cho yt-dlp. Rỗng khi máy không có cái nào — để yt-dlp tự
    than phiền bằng lời của nó thay vì mình đoán hộ."""
    if runtime:
        return ["--js-runtimes", runtime]
    for name in JS_RUNTIMES:
        if shutil.which(name):
            return ["--js-runtimes", name]
    return []


def youtube_args(runtime: str = "", remote_components: str = DEFAULT_REMOTE_COMPONENTS) -> list[str]:
    args = js_runtime_args(runtime)
    if remote_components:
        args += ["--remote-components", remote_components]
    return args


class ManualSource:
    name = "manual"

    def __init__(
        self,
        prefer_h264: bool = False,
        js_runtime: str = "",
        remote_components: str = DEFAULT_REMOTE_COMPONENTS,
        cookies_from_browser: str = "",
        cookie_file: str = "",
    ) -> None:
        self.prefer_h264 = prefer_h264
        self.js_runtime = js_runtime
        self.remote_components = remote_components
        # Douyin/TikTok đòi cookie mới trả video ("Fresh cookies are needed").
        self.cookies_from_browser = cookies_from_browser
        self.cookie_file = cookie_file

    def list_trending(self, region: str, limit: int) -> list[Candidate]:
        raise NotImplementedError(
            "nguồn manual không quét trending — dán link bằng `reup add <url>`"
        )

    def fetch(self, url: str, dest: Path) -> FetchResult:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        info_path = dest.parent / "source.info.json"

        cmd = [
            "yt-dlp",
            "--no-playlist",
            "--write-info-json",
            "--merge-output-format", "mp4",
            "-f", format_selector(self.prefer_h264),
            *youtube_args(self.js_runtime, self.remote_components),
            *cookie_args(self.cookies_from_browser, self.cookie_file),
            "-o", str(dest),
            url,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"yt-dlp thoát với mã {proc.returncode} khi tải {url}\n"
                f"stderr:\n{proc.stderr}"
            )

        produced = dest.with_suffix(".info.json")
        if produced.exists() and produced != info_path:
            produced.replace(info_path)
        if not info_path.exists():
            info_path.write_text("{}", encoding="utf-8")

        raw = json.loads(info_path.read_text(encoding="utf-8"))
        return FetchResult(
            video_path=dest,
            info_path=info_path,
            duration_ms=round(float(raw.get("duration", 0)) * 1000),
            width=int(raw.get("width", 0)),
            height=int(raw.get("height", 0)),
        )
