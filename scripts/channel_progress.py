"""Cho biết mỗi kênh Douyin (từ douyin-exports/) đã làm bao nhiêu video.

Dùng: uv run python scripts/channel_progress.py [tên|sec_user_id một phần]
Đối chiếu aweme_id trong file export với bảng jobs (url), seen, saved_videos của reup.db.
"""
import glob, json, re, sqlite3, sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
db = sqlite3.connect(root / "reup.db")
jobs = {}  # aweme_id -> (status, job_id)
for jid, url, status in db.execute("select id,url,status from jobs"):
    m = re.search(r"douyin\.com/video/(\d+)", url)
    if m:
        jobs[m.group(1)] = (status, jid)
seen = {v for (v,) in db.execute("select video_id from seen where platform='douyin'")}
saved = {v for (v,) in db.execute("select video_id from saved_videos where platform='douyin'")}

latest = {}  # kênh -> file export mới nhất
for f in sorted(glob.glob(str(root / "douyin-exports/douyin_*.json"))):
    d = json.load(open(f))
    latest[d["sec_user_id"]] = d
flt = sys.argv[1].lower() if len(sys.argv) > 1 else ""
for sid, d in latest.items():
    if flt and flt not in d["author"].lower() and flt not in sid.lower():
        continue
    ids = [v["aweme_id"] for v in d["videos"]]
    done = [i for i in ids if jobs.get(i, ("",))[0] == "done"]
    other = [i for i in ids if i in jobs and i not in done]
    print(f"{d['author']:<14} {len(ids):>3} video | job xong {len(done):>3} | job dở/lỗi {len(other):>2} "
          f"| đã thấy {len(set(ids) & seen):>3} | đã lưu {len(set(ids) & saved):>3} | còn chưa làm {len(ids) - len(set(ids) & (set(jobs) | seen)):>3}  [{sid[:20]}]")
