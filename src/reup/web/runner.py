"""Chạy job từ Web UI, trong luồng nền của chính tiến trình web.

Vì sao luồng chứ không phải tiến trình con: một job chạy vài phút, còn request
HTTP thì phải trả lời ngay. Vì sao không dùng `ThreadPoolExecutor`: luồng của
nó không phải daemon, nên Ctrl-C lúc đang render sẽ treo uvicorn cho tới khi
job xong. Daemon thread cộng một semaphore giữ đúng `profile.concurrency` cho
cùng hành vi mà tắt được ngay.

Nặng nhất trong pipeline là Demucs, Whisper và ffmpeg — đều nhả GIL vì chạy
trong native code hoặc tiến trình con, nên web vẫn trả lời trong lúc job chạy.
"""
from __future__ import annotations

import threading
from pathlib import Path

from reup.config import load_config
from reup.core.job import load_job
from reup.core.runner import run_job
from reup.core.store import Store
from reup.stages import stages_for


class BackgroundRunner:
    def __init__(self, config_path: Path, jobs_dir: Path, db_path: Path) -> None:
        self.config_path = Path(config_path)
        self.jobs_dir = Path(jobs_dir)
        self.db_path = Path(db_path)
        self._live: set[str] = set()
        self._lock = threading.Lock()
        self._slots: threading.Semaphore | None = None

    def _semaphore(self) -> threading.Semaphore:
        """Dựng trễ, và dựng TRONG luồng nền.

        Dựng ở `start()` thì một `config.toml` hỏng ném thẳng vào request, và
        tệ hơn: job đã nằm trong `self._live` mà luồng không bao giờ khởi động,
        nên nó kẹt ở đó vĩnh viễn và không ai bấm chạy lại được nữa.

        Khoá vì hai job bấm cùng lúc sẽ cùng thấy `None` và dựng hai semaphore,
        mỗi cái đếm riêng — `profile.concurrency` khi đó vô nghĩa.
        """
        with self._lock:
            if self._slots is None:
                workers = max(1, load_config(self.config_path).profile.concurrency)
                self._slots = threading.Semaphore(workers)
            return self._slots

    def is_running(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._live

    def live(self) -> set[str]:
        with self._lock:
            return set(self._live)

    def start(self, job_id: str) -> bool:
        """Trả False khi job đã đang chạy.

        Chặn ở đây chứ không để hai luồng cùng chạy một job: stage ghi cùng một
        file artifact, chạy chồng là đua ghi và artifact ra dở dang.
        """
        with self._lock:
            if job_id in self._live:
                return False
            self._live.add(job_id)
        try:
            threading.Thread(
                target=self._run, args=(job_id,), daemon=True, name=f"reup-{job_id}"
            ).start()
        except Exception:
            # Hết luồng: nhả sổ ra, đừng để job kẹt ở trạng thái "đang chạy" mà
            # không có luồng nào chạy nó.
            with self._lock:
                self._live.discard(job_id)
            raise
        return True

    def _run(self, job_id: str) -> None:
        try:
            with self._semaphore():
                cfg = load_config(self.config_path)
                job = load_job(self.jobs_dir, job_id)
                store = Store(self.db_path)
                store.init_schema()
                try:
                    run_job(job, cfg, store, stages_for(cfg))
                finally:
                    store.close()
        except Exception as exc:  # config hỏng, job bị xoá tay, ...
            # `run_job` tự ghi "failed" cho lỗi trong stage. Lỗi ở đây là lỗi
            # TRƯỚC đó, nên không ai ghi — để im thì job đứng mãi ở "pending" mà
            # không ai biết vì sao.
            self._mark_failed(job_id, exc)
        finally:
            with self._lock:
                self._live.discard(job_id)

    def _mark_failed(self, job_id: str, exc: Exception) -> None:
        try:
            store = Store(self.db_path)
            store.init_schema()
            try:
                row = store.get_job(job_id)
                store.upsert_job(
                    job_id,
                    row["url"] if row else "",
                    "failed",
                    stage=row["stage"] if row else None,
                    error=f"không khởi động được: {exc}",
                )
            finally:
                store.close()
        except Exception:
            pass  # sổ cái cũng hỏng thì không còn chỗ nào để kể
