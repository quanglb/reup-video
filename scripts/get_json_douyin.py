#!/usr/bin/env python3
"""Script trích xuất toàn bộ video của một kênh Douyin ra file JSON chuẩn reup-douyin/1.

Sử dụng tab Chrome đã đăng nhập để vượt qua các lớp bảo mật (ArgusSecurity / bd_ticket_guard).
Tự động cuộn trang và thu thập toàn bộ video.

Cách dùng:
    uv run python scripts/get_json_douyin.py "https://www.douyin.com/user/MS4wLjABAAAA..."
    hoặc:
    uv run python scripts/get_json_douyin.py MS4wLjABAAAA...
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from reup.adapters.douyin_chrome import CHUNK, NO_TAB, osascript, open_in_chrome
from reup.adapters.douyin_export import parse_export
from reup.web.douyin_channels import store_export

HARVESTER_TEMPLATE = r"""
(() => {
    const TARGET_UID = "__TARGET_UID__";
    const view = (s) => JSON.stringify({
        status: s.status,
        count: s.count,
        error: s.error,
        size: s.json ? s.json.length : 0
    });

    const st = window.__reupHarvester;
    if (st && (st.status === 'running' || st.status === 'done')) {
        return view(st);
    }

    let secUserId = (location.pathname.split('/user/')[1] || '').split(/[/?#]/)[0];
    if (!secUserId) secUserId = TARGET_UID;

    const state = (window.__reupHarvester = {
        status: 'running',
        count: 0,
        error: '',
        json: ''
    });

    const scroller = document.querySelector('.route-scroll-container') || document.body;
    let author = document.title.replace('的抖音 - 抖音', '').replace(' - 抖音', '').trim();
    if (!author || author === '抖音' || author.includes('发现更多精彩')) {
        const h1 = document.querySelector('h1') || document.querySelector('.dJ821QoN') || document.querySelector('.VaW6TeYk');
        if (h1 && h1.innerText) author = h1.innerText.trim();
        else author = 'Douyin User';
    }

    const seen = new Map();
    let noNewRounds = 0;

    function harvest() {
        const links = document.querySelectorAll('a[href*="/video/"]');
        for (let a of links) {
            const m = a.href.match(/\/video\/(\d+)/);
            if (!m) continue;
            const aweme_id = m[1];
            if (!seen.has(aweme_id)) {
                const img = a.querySelector('img');
                const cover = img ? (img.src || img.srcset || '') : '';
                const rawText = (a.innerText || '').replace(/\s+/g, ' ').trim();

                let digg_count = 0;
                const likesMatch = rawText.match(/(\d+(?:\.\d+)?)\s*(万|w)?/i);
                if (likesMatch) {
                    let num = parseFloat(likesMatch[1]);
                    if (likesMatch[2]) num *= 10000;
                    digg_count = Math.round(num);
                }

                let cleanDesc = rawText
                    .replace(/^置顶\s*/, '')
                    .replace(/^\d+(?:\.\d+)?[万w]?\s*/i, '')
                    .trim();

                seen.set(aweme_id, {
                    position: seen.size + 1,
                    aweme_id: aweme_id,
                    desc: cleanDesc,
                    digg_count: digg_count,
                    share_count: 0,
                    comment_count: 0,
                    play_count: 0,
                    duration_ms: 60000,
                    create_time: Math.floor(Date.now() / 1000),
                    author: author,
                    cover: cover,
                    play_url: 'https://www.douyin.com/video/' + aweme_id
                });
            }
        }
        state.count = seen.size;
    }

    (async () => {
        try {
            harvest();
            let prevCount = seen.size;

            for (let round = 0; round < 400; round++) {
                if (scroller) {
                    scroller.scrollTop = scroller.scrollHeight;
                    scroller.dispatchEvent(new Event('scroll', { bubbles: true }));
                }
                window.scrollTo(0, document.body.scrollHeight);
                await new Promise(r => setTimeout(r, 450));

                harvest();

                if (seen.size === prevCount) {
                    noNewRounds++;
                    if (noNewRounds >= 10) {
                        if (scroller) scroller.scrollTop -= 350;
                        await new Promise(r => setTimeout(r, 350));
                        if (scroller) scroller.scrollTop = scroller.scrollHeight;
                        await new Promise(r => setTimeout(r, 700));
                        harvest();
                        if (seen.size === prevCount && noNewRounds >= 14) {
                            break;
                        }
                    }
                } else {
                    noNewRounds = 0;
                    prevCount = seen.size;
                }
            }

            if (!seen.size) {
                throw new Error('Không lấy được video nào từ kênh này. Hãy chắc chắn tab đã tải xong.');
            }

            const exportData = {
                format: 'reup-douyin/1',
                sec_user_id: secUserId,
                author: author,
                exported_at: new Date().toISOString(),
                videos: Array.from(seen.values())
            };

            state.json = JSON.stringify(exportData);
            state.status = 'done';
        } catch (e) {
            state.status = 'error';
            state.error = String(e.message || e);
        }
    })();

    return view(state);
})()
"""

POLL_JS = """
(() => {
    const h = window.__reupHarvester;
    if (!h) return JSON.stringify({ status: 'not_found' });
    return JSON.stringify({
        status: h.status,
        count: h.count,
        error: h.error,
        size: h.json ? h.json.length : 0
    });
})()
"""


def extract_sec_user_id(url_or_uid: str) -> str:
    s = (url_or_uid or "").strip()
    if "douyin.com/user/" in s:
        part = s.split("douyin.com/user/")[1]
        return re.split(r"[/?#]", part)[0]
    return re.split(r"[/?#]", s)[0]


def run_export(uid_or_url: str, exports_folder: Path = Path("douyin-exports"), timeout: float = 600) -> Path:
    uid = extract_sec_user_id(uid_or_url)
    if not uid:
        raise ValueError("Vui lòng cung cấp link kênh Douyin hợp lệ hoặc sec_user_id")

    print(f"👉 Kênh Douyin mục tiêu: {uid}")
    print("🔍 Đang kiểm tra tab Chrome...")

    # Kiểm tra xem tab đã có sẵn trên Chrome chưa
    check_js = "location.href"
    current_tab = osascript(uid, check_js)
    if current_tab == NO_TAB:
        print("🌐 Mở tab mới trên Google Chrome...")
        open_in_chrome(f"https://www.douyin.com/user/{uid}")
        start_wait = time.monotonic()
        while True:
            ready = osascript(uid, "document.readyState")
            if ready in ("interactive", "complete"):
                break
            if time.monotonic() - start_wait > 30:
                raise TimeoutError("Quá thời gian chờ mở tab Douyin trên Chrome (30s)")
            time.sleep(1)

    print("🚀 Bắt đầu chạy Harvester thu thập dữ liệu video...")
    osascript(uid, "delete window.__reupHarvester; 'reset';")
    harvester_code = HARVESTER_TEMPLATE.replace("__TARGET_UID__", uid)
    osascript(uid, harvester_code)

    deadline = time.monotonic() + timeout
    last_count = -1

    while True:
        raw_poll = osascript(uid, POLL_JS)
        if raw_poll == NO_TAB:
            raise RuntimeError("Tab Douyin bị đóng giữa chừng!")
        try:
            st = json.loads(raw_poll)
        except Exception:
            st = {"status": "error", "error": f"Dữ liệu trạng thái lạ: {raw_poll}"}

        status = st.get("status")
        count = st.get("count", 0)

        if count != last_count or status in ("done", "error"):
            print(f"⏳ Tiến độ: [{status.upper()}] - Đã thu thập: {count} video...", flush=True)
            last_count = count

        if status == "done":
            break
        if status == "error":
            raise RuntimeError(f"Lỗi Harvester: {st.get('error')}")
        if time.monotonic() > deadline:
            raise TimeoutError("Quá thời gian thu thập dữ liệu (Timeout)")

        time.sleep(2)

    print("📥 Đang đọc dữ liệu JSON hoàn chỉnh từ tab Chrome...")
    total_size = int(st.get("size") or 0)
    chunks = []
    for i in range(0, total_size, CHUNK):
        chunk_js = f"window.__reupHarvester.json.slice({i}, {i + CHUNK})"
        chunks.append(osascript(uid, chunk_js))

    full_body = "".join(chunks)
    raw_data = json.loads(full_body)
    parsed_candidates = parse_export(raw_data)

    out_file = store_export(exports_folder, full_body.encode("utf-8"), raw_data)

    print("\n" + "=" * 60)
    print("✅ TRÍCH XUẤT JSON DOUYIN THÀNH CÔNG!")
    print(f"👤 Kênh tác giả: {raw_data.get('author')}")
    print(f"🆔 sec_user_id: {raw_data.get('sec_user_id')}")
    print(f"🎬 Tổng số video thu thập: {len(raw_data.get('videos', []))}")
    print(f"🎯 Số video hợp lệ pipeline: {len(parsed_candidates)}")
    print(f"📁 Đường dẫn file: {out_file.resolve()}")
    print("=" * 60)
    return out_file


def main() -> int:
    parser = argparse.ArgumentParser(description="Trích xuất JSON kênh Douyin")
    parser.add_argument("url", help="Link kênh Douyin hoặc sec_user_id")
    parser.add_argument("--out", type=Path, default=Path("douyin-exports"), help="Thư mục lưu JSON")
    parser.add_argument("--timeout", type=int, default=600, help="Thời gian chờ tối đa (giây)")
    args = parser.parse_args()

    try:
        run_export(args.url, exports_folder=args.out, timeout=args.timeout)
        return 0
    except Exception as exc:
        print(f"❌ Thất bại: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
