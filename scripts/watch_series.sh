#!/bin/bash
# Chạy nền độc lập với phiên Claude: dừng run_series khi đủ N video xong trong log,
# và chạy lại (làm tiếp job dở/lỗi) nếu runner thoát sớm. Dùng: watch_series.sh <log> <target_done>
cd "$(dirname "$0")/.." || exit 1
export PATH="$PWD/.venv/bin:$PATH"
LOG=$1; TARGET=$2; JOBS=${3:-2}; RESTARTS=0
touch "$LOG" 2>/dev/null || true
while true; do
  n=$(grep -c "→ \[.*DONE" "$LOG" 2>/dev/null || true)
  n=${n:-0}
  if [ "$n" -ge "$TARGET" ]; then pkill -f run_series.py; echo "$(date +%T) đủ $n video, đã dừng runner" >> watch_series.log; exit 0; fi
  if ! pgrep -f run_series.py >/dev/null; then
    if [ "$RESTARTS" -ge 3 ]; then echo "$(date +%T) runner thoát, đã thử lại 3 lần, bỏ cuộc (done=$n)" >> watch_series.log; exit 1; fi
    RESTARTS=$((RESTARTS+1)); echo "$(date +%T) runner thoát (done=$n), chạy lại lần $RESTARTS" >> watch_series.log
    nohup .venv/bin/python scripts/run_series.py --uid MS4wLjABAAAAVTZeBYm7A4vme9Kh_7LJ8PAkDAXNNAYMunZ1SFROSyg --series ao_thuat --name "Ảo thuật - 魔术师TonyWang" --count "$TARGET" --jobs "$JOBS" >> "$LOG" 2>&1 &
  fi
  sleep 30
done
