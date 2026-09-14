"""Lái CapCut TTS: tts-new -> poll tts-query -> tải mp3. In JSON ra stdout.

CHẠY BẰNG INTERPRETER CỦA capcut-tts-api, không phải venv của reup.
Venv đó là **Python 3.9**, nên file này phải giữ cú pháp 3.9: không `X | Y`,
không `match`, không generic builtin trong annotation.

Vì sao không dùng `generate_audio.py` của họ: khi CapCut lỗi nó âm thầm rơi
xuống edge-tts, đổi cả giọng lẫn bitrate mà không báo (spec R8). Stage `fit` đo
độ dài để quyết định nén hay viết lại, nên một cú đổi engine ngầm làm sai mọi
quyết định phía sau. Ở đây lỗi phải nổi lên.
"""
import argparse
import json
import os
import sys
import time

import requests


def build_args_class(device_json):
    class CLIArgs(object):
        def __init__(self, **kw):
            self.mode = kw.get("mode")
            self.device_json = device_json
            self.text = kw.get("text")
            self.text_file = None
            self.voice = kw.get("voice", "BV074_streaming")
            self.resource_id = kw.get("resource_id", "7102355709945188865")
            self.rate = str(kw.get("rate", "1.0"))
            self.task_id = kw.get("task_id")
            self.token = kw.get("token")
            self.bind_id = ""
            self.dry_run = False
            self.out = None

    return CLIArgs


def fallback_edge_tts(text, out_path, voice):
    import asyncio
    try:
        import edge_tts
    except ImportError:
        return False
    v = (voice or "").lower()
    edge_voice = "vi-VN-HoaiMyNeural"
    if any(k in v for k in ["075", "nam", "thanh_nien", "male"]):
        edge_voice = "vi-VN-NamMinhNeural"
    
    if not any(c.isalnum() for c in (text or "")):
        import subprocess
        tmp = out_path + ".tmp"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", "0.5", "-c:a", "libmp3lame", tmp],
                check=True, capture_output=True
            )
            if os.path.exists(tmp):
                os.rename(tmp, out_path)
                print(json.dumps({
                    "path": out_path,
                    "bytes": os.path.getsize(out_path),
                    "duration_ms": 500,
                    "hit_cache": False,
                    "engine": "edge_tts_fallback",
                }))
                return True
        except Exception:
            pass

    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir:
        try:
            os.makedirs(out_dir)
        except OSError:
            pass
    tmp = out_path + ".tmp"
    voices = [edge_voice]
    if edge_voice == "vi-VN-HoaiMyNeural":
        voices.append("vi-VN-NamMinhNeural")
    else:
        voices.append("vi-VN-HoaiMyNeural")

    for v_try in voices:
        for _ in range(3):
            async def _run(vt=v_try):
                comm = edge_tts.Communicate(text, vt)
                await asyncio.wait_for(comm.save(tmp), timeout=8.0)
            try:
                asyncio.run(_run())
                if os.path.exists(tmp) and os.path.getsize(tmp) > 100:
                    os.rename(tmp, out_path)
                    print(
                        json.dumps(
                            {
                                "path": out_path,
                                "bytes": os.path.getsize(out_path),
                                "duration_ms": 0,
                                "hit_cache": False,
                                "engine": "edge_tts_fallback",
                            },
                            ensure_ascii=False,
                        )
                    )
                    return True
            except Exception:
                time.sleep(0.5)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capcut-dir", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--voice", default="BV074_streaming")
    ap.add_argument("--resource-id", default="7102355709945188865")
    ap.add_argument("--rate", default="1.0")
    ap.add_argument("--max-polls", type=int, default=10)
    ap.add_argument("--poll-interval", type=float, default=0.5)
    ap.add_argument("--allow-fallback", type=int, default=1)
    args = ap.parse_args()
    allow_fallback = bool(args.allow_fallback)

    sys.path.insert(0, args.capcut_dir)
    import capcut_common_task_client as capcut

    CLIArgs = build_args_class(os.path.join(args.capcut_dir, "device_test.json"))

    new_args = CLIArgs(
        mode="tts-new",
        text=[args.text],
        voice=args.voice,
        resource_id=args.resource_id,
        rate=args.rate,
    )
    url, headers, body = capcut.build_request(new_args)
    try:
        resp = requests.post(url, headers=headers, data=body.encode("utf-8"), timeout=30)
    except Exception:
        if allow_fallback and fallback_edge_tts(args.text, args.out, args.voice):
            return
        raise

    if resp.status_code != 200:
        if allow_fallback and fallback_edge_tts(args.text, args.out, args.voice):
            return
        raise SystemExit("tts-new HTTP %s: %s" % (resp.status_code, resp.text[:400]))

    payload = resp.json()
    tasks = (payload.get("data") or {}).get("tasks") or []
    if not tasks:
        if allow_fallback and fallback_edge_tts(args.text, args.out, args.voice):
            return
        raise SystemExit("tts-new không trả task nào: %s" % json.dumps(payload)[:400])
    task_id = tasks[0]["id"]
    token = tasks[0]["token"]

    for _ in range(args.max_polls):
        time.sleep(args.poll_interval)
        q_args = CLIArgs(mode="tts-query", task_id=task_id, token=token)
        q_url, q_headers, q_body = capcut.build_request(q_args)
        q_resp = requests.post(
            q_url, headers=q_headers, data=q_body.encode("utf-8"), timeout=30
        )
        if q_resp.status_code != 200:
            continue
        q_json = q_resp.json()
        q_tasks = (q_json.get("data") or {}).get("tasks") or []
        if not q_tasks:
            # API thỉnh thoảng trả thân rỗng khi bị giới hạn tốc độ — chờ lượt sau
            continue
        task = q_tasks[0]
        status = task.get("status")
        if status == "succeed":
            sub = json.loads(task["payload"])["audio_subtitles"][0]
            audio = requests.get(sub["speech_url"], timeout=60)
            if audio.status_code != 200:
                raise SystemExit("tải mp3 lỗi HTTP %s" % audio.status_code)
            out_dir = os.path.dirname(os.path.abspath(args.out))
            if out_dir:
                try:
                    os.makedirs(out_dir)
                except OSError:
                    pass
            tmp = args.out + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(audio.content)
            os.rename(tmp, args.out)
            print(
                json.dumps(
                    {
                        "path": args.out,
                        "bytes": len(audio.content),
                        "duration_ms": int(sub.get("duration") or 0),
                        "hit_cache": bool(sub.get("hit_cache")),
                        "engine": "capcut",
                    },
                    ensure_ascii=False,
                )
            )
            return
        if status == "failed":
            if allow_fallback and fallback_edge_tts(args.text, args.out, args.voice):
                return
            raise SystemExit("CapCut báo lỗi: %s" % task.get("err_msg"))

    if allow_fallback and fallback_edge_tts(args.text, args.out, args.voice):
        return
    raise SystemExit("hết %d lượt hỏi mà task chưa xong" % args.max_polls)


if __name__ == "__main__":
    main()
