// JS thuần, không build. Ba việc: đếm âm tiết khi gõ, nghe thử, lưu sửa.
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

  editor.querySelectorAll('button.play').forEach((btn) => {
    btn.addEventListener('click', () => {
      const audio = new Audio(`/jobs/${jobId}/audio/${btn.dataset.seg}`);
      audio.play().catch(() => {
        document.getElementById('msg').textContent =
          'Chưa có giọng đọc cho câu này — chạy stage tts trước.';
      });
    });
  });

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
