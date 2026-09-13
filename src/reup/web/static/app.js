// JS thuần, không build. Sáu việc: đếm âm tiết khi gõ, nghe thử, chọn giọng,
// lưu sửa, nạp iframe xem thử ở tab quét nguồn, và tự làm mới lúc có job chạy.

// Cập nhật tiến trình thời gian thực (Live Progress Polling)
(function () {
  // 1. Polling trang Hàng đợi (Queue)
  const rows = document.querySelectorAll('tr[data-job-id]');
  if (rows.length > 0) {
    const hasRunning = Array.from(rows).some(r => r.dataset.status === 'running' || r.querySelector('.spin') || r.querySelector('.spin-dot'));
    if (hasRunning) {
      const queueTimer = setInterval(async () => {
        try {
          const res = await fetch('/api/progress');
          if (!res.ok) return;
          const data = await res.json();
          let stateChanged = false;

          rows.forEach(row => {
            const id = row.dataset.jobId;
            const info = data[id];
            if (!info) return;

            // Kiểm tra đổi trạng thái (ví dụ từ running -> needs_review/done)
            if (row.dataset.status !== info.status) {
              stateChanged = true;
            }

            const fill = row.querySelector('.progress-fill');
            if (fill) {
              fill.style.width = info.percent + '%';
              fill.className = 'progress-fill ' + info.status;
            }
            const stageText = row.querySelector('.stage-text');
            if (stageText) stageText.textContent = info.stage_label;
            const pctText = row.querySelector('.pct-text');
            if (pctText) pctText.textContent = info.percent + '%';

            const pill = row.querySelector('.pill');
            if (pill) {
              pill.className = 'pill ' + info.status;
              pill.textContent = info.status;
            }
          });

          if (stateChanged) {
            const el = document.activeElement;
            if (!el || (el.tagName !== 'INPUT' && el.tagName !== 'TEXTAREA')) {
              location.reload();
            }
          }
        } catch (e) {
          // bỏ qua lỗi mạng tạm thời
        }
      }, 2000);
    }
  }

  // 2. Polling trang Duyệt & Chi tiết Job (Review)
  const progressCard = document.getElementById('jobProgressCard');
  if (progressCard) {
    const jobId = progressCard.dataset.jobId;
    const initialStatus = progressCard.dataset.status;

    if (initialStatus === 'running' || document.querySelector('.spin')) {
      const reviewTimer = setInterval(async () => {
        try {
          const res = await fetch(`/jobs/${jobId}/progress`);
          if (!res.ok) return;
          const data = await res.json();

          // Cập nhật % và thanh tiến trình
          const pctBadge = document.getElementById('progressPctBadge');
          if (pctBadge) pctBadge.textContent = data.percent + '%';

          const barFill = document.getElementById('progressBarFill');
          if (barFill) {
            barFill.style.width = data.percent + '%';
            barFill.className = 'progress-fill ' + data.status;
          }

          // Cập nhật text mô tả
          const statusText = document.getElementById('progressStatusText');
          if (statusText) {
            if (data.status === 'running') {
              statusText.innerHTML = `Đang thực hiện: <b>${data.stage_label}</b>`;
            } else if (data.status === 'needs_review') {
              statusText.innerHTML = `<b>${data.stage_label}</b>`;
            } else if (data.status === 'done') {
              statusText.innerHTML = `<b>Hoàn thành toàn bộ quy trình</b>`;
            } else if (data.status === 'failed') {
              statusText.innerHTML = `<b>Lỗi tại: ${data.stage_label}</b>`;
            } else {
              statusText.innerHTML = `<b>${data.stage_label}</b>`;
            }
          }

          // Cập nhật badges ở header
          const statusBadge = document.getElementById('jobStatusBadge');
          if (statusBadge) {
            statusBadge.className = 'pill ' + data.status;
            statusBadge.textContent = data.status;
          }
          const stageBadge = document.getElementById('jobStageBadge');
          if (stageBadge) stageBadge.textContent = data.stage;

          // Cập nhật Pipeline Stepper
          if (data.stages && data.stages.length > 0) {
            const stepper = document.getElementById('pipelineStepper');
            if (stepper) {
              stepper.innerHTML = data.stages.map(st => {
                let icon = st.step;
                if (st.status === 'done') icon = '✓';
                else if (st.status === 'running') icon = '⏳';
                else if (st.status === 'needs_review') icon = '⏸';
                else if (st.status === 'failed') icon = '✕';

                const timeHtml = st.duration_s ? `<div class="step-time">${st.duration_s}s</div>` : '';
                return `
                  <div class="step-item step-${st.status}" title="${st.label}${st.duration_s ? ` (${st.duration_s}s)` : ''}">
                    <div class="step-icon">${icon}</div>
                    <div class="step-name">${st.label}</div>
                    ${timeHtml}
                  </div>
                `;
              }).join('');
            }
          }

          // Cập nhật Nhật ký hoạt động (Logs Console)
          if (data.logs) {
            const countEl = document.getElementById('logsCount');
            if (countEl) countEl.textContent = data.logs.length;

            const consoleEl = document.getElementById('logsConsole');
            if (consoleEl) {
              if (data.logs.length === 0) {
                consoleEl.innerHTML = '<div class="log-empty">Chưa có nhật ký hoạt động nào.</div>';
              } else {
                consoleEl.innerHTML = data.logs.map(l => `
                  <div class="log-line ${l.ok ? 'log-ok' : 'log-err'}">
                    <span class="log-tag">[${l.stage}]</span>
                    <span class="log-desc">${l.label}</span>
                    <span class="log-status">${l.ok ? '✓ Xong' : '✕ Lỗi'}</span>
                    ${l.duration_s ? `<span class="log-dur">(${l.duration_s}s)</span>` : ''}
                    ${l.error ? `<pre class="log-err-detail">${l.error}</pre>` : ''}
                  </div>
                `).join('');
              }
            }
          }

          // Nếu trạng thái thay đổi từ running sang trạng thái dừng (chờ duyệt hoặc xong)
          if (data.status !== 'running') {
            clearInterval(reviewTimer);
            const el = document.activeElement;
            if (!el || (el.tagName !== 'INPUT' && el.tagName !== 'TEXTAREA')) {
              setTimeout(() => location.reload(), 1000);
            }
          }
        } catch (e) {
          // bỏ qua lỗi tạm thời
        }
      }, 1500);
    }
  }
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

  // Tự động điền và quét khi bấm vào chip gợi ý
  const scanForm = document.getElementById('scanForm');
  const scanInput = document.getElementById('scanQueryInput');
  if (scanForm && scanInput) {
    document.querySelectorAll('.suggest-chips .chip').forEach((chip) => {
      chip.addEventListener('click', () => {
        const q = chip.dataset.q;
        if (!q) return;
        scanInput.value = q;
        scanForm.submit();
      });
    });
  }

  // Chép script xuất JSON của tab Douyin
  const copyBtn = document.getElementById('copyDouyinScript');
  const script = document.getElementById('douyinScript');
  if (copyBtn && script) {
    copyBtn.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(script.value);
      } catch (e) {
        script.closest('details').open = true;
        script.select();
        document.execCommand('copy');
      }
      copyBtn.textContent = 'Đã chép ✓';
      setTimeout(() => { copyBtn.textContent = 'Chép script'; }, 2000);
    });
  }
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

  // Nút mở Finder xem file thành phẩm
  const btnReveal = document.getElementById('btnRevealFinder');
  if (btnReveal) {
    const card = document.getElementById('jobProgressCard');
    const targetJobId = card?.dataset?.jobId || jobId || location.pathname.split('/').filter(Boolean).pop();

    btnReveal.addEventListener('click', async () => {
      const originalText = btnReveal.textContent;
      btnReveal.disabled = true;
      btnReveal.textContent = '⏳ Đang mở Finder…';
      try {
        const res = await fetch(`/jobs/${targetJobId}/reveal`, { method: 'POST' });
        if (!res.ok) {
          const err = await res.json();
          throw new Error(err.detail || res.status);
        }
        btnReveal.textContent = '✓ Đã mở trong Finder!';
        setTimeout(() => {
          btnReveal.textContent = originalText;
          btnReveal.disabled = false;
        }, 2000);
      } catch (e) {
        alert('Không mở được Finder: ' + e.message);
        btnReveal.textContent = originalText;
        btnReveal.disabled = false;
      }
    });
  }
})();
