"""Lái CapCut STT: upload audio -> stt-new -> poll stt-query. In JSON ra stdout.

CHẠY BẰNG INTERPRETER CỦA capcut-tts-api, không phải venv của reup.
Venv đó là **Python 3.9** nên file này giữ cú pháp 3.9: không `X | Y`, không
`match`, không generic builtin trong annotation.

CLI của họ có mode `stt-file` nhưng nó dừng ngay sau khi gửi `stt-new` — chỉ in
response rồi thoát, không hỏi kết quả. Ở đây ta đi hết vòng: upload lấy `vid` và
`md5`, gửi `stt-new`, rồi poll `stt-query` tới khi task xong.

Kết quả nằm trong `data.tasks[0].payload` — lại là một JSON string. Bên trong có
`utterances[]` với `start_time`/`end_time` theo ms và `words[]` mốc từng từ.
`Segment` của reup chỉ mang biên câu nên `words` bị bỏ ở đây, không mang về.
"""
import argparse
import json
import os
import sys
import time
from copy import deepcopy

import requests


def build_args_class(device_json):
    class CLIArgs(object):
        def __init__(self, **kw):
            self.mode = kw.get("mode")
            self.device_json = device_json
            self.text = None
            self.text_file = None
            self.voice = "BV074_streaming"
            self.resource_id = "7102355709945188865"
            self.rate = "1.0"
            self.audio_vid = kw.get("audio_vid")
            self.audio_md5 = kw.get("audio_md5")
            self.audio_file = kw.get("audio_file")
            self.duration_ms = kw.get("duration_ms")
            self.language = kw.get("language", "zh-CN")
            self.translation_language = kw.get("translation_language", "vi-VN")
            self.use_translation = False
            self.task_id = kw.get("task_id")
            self.token = kw.get("token")
            self.bind_id = ""
            self.dry_run = False
            self.out = None

    return CLIArgs


def post(capcut, args_obj):
    url, headers, body = capcut.build_request(args_obj)
    resp = requests.post(url, headers=headers, data=body.encode("utf-8"), timeout=60)
    return resp


def utterance_ms(raw, key):
    value = raw.get(key)
    if value is None:
        return 0
    return int(round(float(value)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capcut-dir", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--language", default="zh-CN")
    ap.add_argument("--max-polls", type=int, default=60)
    ap.add_argument("--poll-interval", type=float, default=2.0)
    args = ap.parse_args()

    sys.path.insert(0, args.capcut_dir)
    import capcut_common_task_client as capcut

    device_json = os.path.join(args.capcut_dir, "device_test.json")
    CLIArgs = build_args_class(device_json)

    device = deepcopy(capcut.DEFAULT_DEVICE)
    device.update(capcut.load_json(device_json, {}))
    upload = capcut.upload_audio_file(args.audio, device)
    if not upload.get("vid") or not upload.get("md5"):
        raise SystemExit("upload không trả vid/md5: %s" % json.dumps(upload)[:400])

    new_args = CLIArgs(
        mode="stt-new",
        audio_vid=upload["vid"],
        audio_md5=upload["md5"],
        duration_ms=upload.get("duration_ms") or 10000,
        language=args.language,
    )
    resp = post(capcut, new_args)
    if resp.status_code != 200:
        raise SystemExit("stt-new HTTP %s: %s" % (resp.status_code, resp.text[:400]))
    tasks = (resp.json().get("data") or {}).get("tasks") or []
    if not tasks:
        raise SystemExit("stt-new không trả task nào: %s" % resp.text[:400])
    task_id = tasks[0]["id"]
    token = tasks[0]["token"]

    for _ in range(args.max_polls):
        time.sleep(args.poll_interval)
        q_resp = post(capcut, CLIArgs(mode="stt-query", task_id=task_id, token=token))
        if q_resp.status_code != 200:
            continue
        q_tasks = (q_resp.json().get("data") or {}).get("tasks") or []
        if not q_tasks:
            # Thân rỗng khi bị giới hạn tốc độ — chờ lượt sau
            continue
        task = q_tasks[0]
        status = task.get("status")
        if status == "succeed":
            payload = json.loads(task["payload"])
            utterances = []
            for raw in payload.get("utterances") or []:
                text = (raw.get("text") or "").strip()
                if not text:
                    continue
                utterances.append({
                    "text": text,
                    "start_ms": utterance_ms(raw, "start_time"),
                    "end_ms": utterance_ms(raw, "end_time"),
                })
            print(json.dumps(
                {
                    "language": payload.get("language") or args.language,
                    "duration_ms": int(upload.get("duration_ms") or 0),
                    "utterances": utterances,
                },
                ensure_ascii=False,
            ))
            return
        if status == "failed":
            raise SystemExit("CapCut báo lỗi: %s" % task.get("err_msg"))

    raise SystemExit("hết %d lượt hỏi mà task chưa xong" % args.max_polls)


if __name__ == "__main__":
    main()
