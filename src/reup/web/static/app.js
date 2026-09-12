// JS thuần, không build. Sáu việc: đếm âm tiết khi gõ, nghe thử, chọn giọng,
// lưu sửa, nạp iframe xem thử ở tab quét nguồn, và tự làm mới lúc có job chạy.

// Job chạy vài phút trong luồng nền của server; không tự làm mới thì người dùng
// ngồi nhìn một trang đứng im và tưởng là treo.
(function () {
  if (!document.querySelector('.spin')) return;
  setInterval(() => {
    // Đang gõ dở (ô dán link, ô hashtag) mà reload là mất chữ.
    const el = document.activeElement;
    if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA')) return;
    location.reload();
  }, 5000);
})();

// Tab quét: chỉ nạp iframe khi bấm. Nạp sẵn cả chục player của YouTube hay
// TikTok làm trang ì và mỗi lần quét lại là một mớ request ngoài.
(function () {
  document.querySelectorAll('.card .play-embed').forEach((btn) => {
    btn.addEventListener('click', () => {
      const frame = btn.parentElement;
      const src = frame.dataset.embed;
      if (!src) return;
      const iframe = document.createElement('iframe');
      iframe.src = src;
      iframe.allow = 'autoplay; encrypted-media; picture-in-picture';
      iframe.allowFullscreen = true;
      iframe.referrerPolicy = 'strict-origin-when-cross-origin';
      frame.replaceChildren(iframe);
    });
  });
})();

(function () {
  const editor = document.querySelector('.editor');
  if (!editor) return;
  const jobId = editor.dataset.job;

  // Đếm như reup.text.count_syllables cho tiếng Việt: tách theo khoảng trắng,
  // bỏ token không có chữ cái.
  function countSyllables(text) {
    return text.split(/\s+/).filter((t) => /\p{L}/u.test(t)).length;
  }

  editor.querySelectorAll('textarea[data-seg]').forEach((area) => {
    area.addEventListener('input', () => {
      const id = area.dataset.seg;
      const out = editor.querySelector(`[data-count="${id}"]`);
      if (!out) return;
      out.textContent = countSyllables(area.value);
      const cell = out.parentElement;
      const budget = parseInt(cell.textContent.split('/')[1], 10);
      cell.classList.toggle('over', countSyllables(area.value) > budget);
    });
  });

  // Câu chưa có file wav thì server tổng hợp ngay lúc bấm — mất vài giây, nên
  // phải báo là đang chờ chứ không để nút im lặng.
  editor.querySelectorAll('button.play').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const msg = document.getElementById('msg');
      btn.disabled = true;
      msg.textContent = 'Đang tổng hợp câu này…';
      try {
        const res = await fetch(`/jobs/${jobId}/audio/${btn.dataset.seg}`);
        if (!res.ok) throw new Error((await res.json()).detail || res.status);
        const url = URL.createObjectURL(await res.blob());
        await new Audio(url).play();
        msg.textContent = '';
      } catch (e) {
        msg.textContent = 'Nghe thử hỏng: ' + e.message;
      } finally {
        btn.disabled = false;
      }
    });
  });

  const voice = document.getElementById('voice');
  if (voice) {
    voice.addEventListener('change', async () => {
      const msg = document.getElementById('msg');
      msg.textContent = 'Đang đổi giọng…';
      try {
        const body = new FormData();
        body.append('voice', voice.value);
        const res = await fetch(`/jobs/${jobId}/voice`, { method: 'POST', body });
        if (!res.ok) throw new Error((await res.json()).detail || res.status);
        const data = await res.json();
        msg.textContent = data.changed
          ? 'Đã đổi giọng. Cả loạt sẽ được tổng hợp lại lần chạy sau.'
          : 'Giọng không đổi.';
      } catch (e) {
        msg.textContent = 'Đổi giọng hỏng: ' + e.message;
      }
    });
  }

  const save = document.getElementById('save');
  if (save) {
    save.addEventListener('click', async () => {
      const segments = {};
      editor.querySelectorAll('textarea[data-seg]').forEach((a) => {
        segments[a.dataset.seg] = a.value;
      });
      const msg = document.getElementById('msg');
      msg.textContent = 'Đang lưu…';
      try {
        const res = await fetch(`/jobs/${jobId}/segments`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ segments }),
        });
        const data = await res.json();
        msg.textContent = data.changed
          ? `Đã lưu ${data.changed} câu. Giọng đọc của chúng sẽ được tổng hợp lại.`
          : 'Không có gì thay đổi.';
      } catch (e) {
        msg.textContent = 'Lưu hỏng: ' + e;
      }
    });
  }
})();
