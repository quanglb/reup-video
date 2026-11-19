#!/bin/bash
# Thử CapCut mỗi 3 phút; khi hết chặn thì chạy tiếp series và các job lỗi.
cd "$(dirname "$0")/.." || exit 1
D=/Applications/_QuangLB/Workspace/Agent/capcut-tts-api
while true; do
  out=$(.venv/bin/python - <<'PY' 2>&1
import sys,os,requests
sys.path.insert(0,'src/reup/adapters'); d="/Applications/_QuangLB/Workspace/Agent/capcut-tts-api"; sys.path.insert(0,d)
import capcut_driver as cd, capcut_common_task_client as capcut
A=cd.build_args_class(os.path.join(d,"device_test.json"))
u,h,b=capcut.build_request(A(mode="tts-new",text=["Xin chào"],voice="BV074_streaming",resource_id="7102355709945188865",rate="1.0"))
r=requests.post(u,headers=h,data=b.encode(),timeout=10)
print("OK" if r.json().get("data",{}).get("tasks") else "BLOCKED "+r.text[:80])
PY
)
  echo "$(date +%T) probe: $(echo "$out" | tail -1)"
  if echo "$out" | tail -1 | grep -q '^OK'; then
    curl -s -m 10 -o /dev/null -X POST http://localhost:8766/run-all
    nohup bash scripts/watch_series.sh run_series_batch3.log 46 >/dev/null 2>&1 &
    echo "$(date +%T) RESUMED"; exit 0
  fi
  sleep 180
done
