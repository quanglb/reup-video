// JS thuần, không build. Sáu việc: đếm âm tiết khi gõ, nghe thử, chọn giọng,
// lưu sửa, nạp iframe xem thử ở tab quét nguồn, và tự làm mới lúc có job chạy.

// Cập nhật tiến trình thời gian thực (Live Progress Polling)
(function () {
  // 1. Polling trang Hàng đợi (Queue)
  const STATUS_LABELS = {
    pending: 'Đang chờ', running: 'Đang chạy', needs_review: 'Chờ duyệt', done: 'Xong', failed: 'Lỗi',
  };
  const rows = document.querySelectorAll('.job-card[data-job-id]');
  if (rows.length > 0) {
    const hasRunning = Array.from(rows).some(r => r.dataset.status === 'running' || r.querySelector('.spin') || r.querySelector('.dot'));
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

            const badge = row.querySelector('.status-badge');
            if (badge && row.dataset.status !== info.status) {
              badge.className = 'badge status-badge ' + info.status;
              badge.textContent = STATUS_LABELS[info.status] || info.status;
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

          // Câu nào vừa tổng hợp xong giọng thì cập nhật ngay, nghe được luôn
          if (data.tts && window.reupApplyTts) window.reupApplyTts(data.tts);

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

  // Nút Quét lại: server mở kênh trong Chrome đã đăng nhập, xuất file mới ở nền.
  const rescanUrl = (uid) => '/discover/douyin/channels/rescan?uid=' + encodeURIComponent(uid);
  document.querySelectorAll('[data-rescan]').forEach((btn) => {
    const uid = btn.dataset.rescan;
    const msg = btn.closest('.dy-channel')?.querySelector('.dy-rescan-msg');
    const show = (text, bad) => {
      if (!msg) return;
      msg.hidden = !text;
      msg.textContent = text;
      msg.classList.toggle('dy-stale', Boolean(bad));
    };
    const reset = () => { btn.disabled = false; btn.textContent = 'Quét lại'; };
    const poll = async () => {
      let s;
      try {
        s = await (await fetch(rescanUrl(uid))).json();
      } catch (e) {
        setTimeout(poll, 3000);
        return;
      }
      if (s.status === 'running') {
        btn.disabled = true;
        btn.textContent = s.videos ? `Đang quét… ${s.videos}` : 'Đang mở Chrome…';
        setTimeout(poll, 2000);
      } else if (s.status === 'done') {
        btn.textContent = `Xong ✓ ${s.videos}`;
        location.reload();
      } else if (s.status === 'error') {
        reset();
        show(s.error, true);
      }
    };
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      btn.textContent = 'Đang mở Chrome…';
      show('');
      const r = await fetch('/discover/douyin/channels/rescan', {
        method: 'POST', body: new URLSearchParams({ uid }),
      });
      const s = await r.json().catch(() => ({}));
      if (!r.ok) {
        reset();
        show(s.detail || 'Không quét lại được', true);
        return;
      }
      poll();
    });
    // Tải lại trang giữa lúc đang quét thì theo dõi tiếp.
    fetch(rescanUrl(uid)).then((r) => r.json()).then((s) => {
      if (s.status === 'running') poll();
    }).catch(() => {});
  });

  // Nút chép lệnh /douyin-export cho Claude Code (kênh chưa lưu).
  document.querySelectorAll('[data-copy]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const label = btn.textContent;
      try {
        await navigator.clipboard.writeText(btn.dataset.copy);
        btn.textContent = 'Đã chép ✓';
      } catch (e) {
        window.prompt('Chép lệnh này vào Claude Code:', btn.dataset.copy);
      }
      setTimeout(() => { btn.textContent = label; }, 2000);
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
      const budget = parseInt(cell.dataset.budget, 10);
      cell.classList.toggle('over', countSyllables(area.value) > budget);
    });
  });

  // --- Trạng thái TTS từng câu ---------------------------------------------
  const cardOf = (id) => editor.querySelector(`.seg-card[data-seg-id="${id}"]`);

  function applyTts(tts) {
    if (!tts) return;
    editor.querySelectorAll('.seg-card').forEach((card) => {
      const info = tts.segments[card.dataset.segId];
      const state = info ? info.state : 'none';
      card.dataset.tts = state;
      card.dataset.v = info ? info.v : '';
      card.classList.remove('tts-tts', 'tts-preview', 'tts-none');
      card.classList.add('tts-' + state);
      const chip = card.querySelector('.tts-chip');
      if (chip) {
        chip.textContent = state === 'tts'
          ? '✓ Đã có giọng' + (info.ms ? ` · ${(info.ms / 1000).toFixed(1)}s` : '')
          : state === 'preview' ? '◐ Bản nghe thử' : '○ Chưa có giọng';
      }
    });
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    set('ttsReady', tts.ready);
    set('fReady', tts.ready);
    set('fMissing', tts.total - tts.ready);
    const fill = document.getElementById('ttsBarFill');
    if (fill) fill.style.width = (tts.total ? Math.floor(tts.ready * 100 / tts.total) : 0) + '%';
    applyFilter();
  }
  window.reupApplyTts = applyTts;

  async function refreshTts() {
    try {
      const res = await fetch(`/jobs/${jobId}/progress`);
      if (res.ok) applyTts((await res.json()).tts);
    } catch (e) { /* bỏ qua */ }
  }

  // --- Lọc câu -----------------------------------------------------------------
  let filter = 'all';
  function applyFilter() {
    editor.querySelectorAll('.seg-card').forEach((card) => {
      const t = card.dataset.tts;
      card.hidden = filter === 'ready' ? t !== 'tts'
        : filter === 'missing' ? t === 'tts'
        : filter === 'warn' ? !card.classList.contains('warn')
        : false;
    });
  }
  document.querySelectorAll('#segFilter [data-filter]').forEach((b) => {
    b.addEventListener('click', () => {
      filter = b.dataset.filter;
      document.querySelectorAll('#segFilter [data-filter]').forEach((x) => x.classList.toggle('on', x === b));
      applyFilter();
    });
  });

  // --- Phát âm thanh: một Audio dùng chung, bấm lại để dừng -----------------
  const player = new Audio();
  let playing = null; // thẻ đang phát
  let queue = [];     // hàng đợi khi "Phát lần lượt"
  const playAllBtn = document.getElementById('playAll');

  function setPlaying(card) {
    if (playing) {
      playing.classList.remove('is-playing', 'is-loading');
      playing.querySelector('.play-icon').textContent = '▶';
      playing.querySelector('.play-progress > div').style.width = '0';
    }
    playing = card;
    if (card) {
      card.classList.add('is-playing');
      card.querySelector('.play-icon').textContent = '■';
    }
  }

  function stopAll() {
    queue = [];
    player.pause();
    setPlaying(null);
    if (playAllBtn) playAllBtn.textContent = '▶ Phát lần lượt';
  }

  player.addEventListener('timeupdate', () => {
    if (!playing || !player.duration) return;
    playing.querySelector('.play-progress > div').style.width = (player.currentTime / player.duration * 100) + '%';
  });
  player.addEventListener('ended', () => {
    if (queue.length) playCard(queue.shift());
    else stopAll();
  });

  async function playCard(card) {
    const msg = document.getElementById('msg');
    const id = card.dataset.segId;
    setPlaying(card);
    if (queue.length || playAllBtn?.dataset.active) card.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    try {
      if (card.dataset.tts !== 'none') {
        // Đã có file: phát thẳng, ?v= đổi khi file được tổng hợp lại.
        player.src = `/jobs/${jobId}/audio/${id}?v=${card.dataset.v}`;
      } else {
        // Chưa có file thì server tổng hợp đúng câu này — mất vài giây.
        card.classList.add('is-loading');
        card.querySelector('.play-icon').textContent = '…';
        msg.textContent = `Đang tổng hợp câu #${id}…`;
        const res = await fetch(`/jobs/${jobId}/audio/${id}`);
        if (!res.ok) throw new Error((await res.json()).detail || res.status);
        const blob = await res.blob();
        if (playing !== card) return; // người dùng đã bấm câu khác
        card.classList.remove('is-loading');
        card.querySelector('.play-icon').textContent = '■';
        player.src = URL.createObjectURL(blob);
        msg.textContent = '';
        refreshTts();
      }
      await player.play();
    } catch (e) {
      msg.textContent = 'Nghe thử hỏng: ' + e.message;
      stopAll();
    }
  }

  editor.querySelectorAll('button.play').forEach((btn) => {
    btn.addEventListener('click', () => {
      const card = btn.closest('.seg-card');
      if (playing === card) { stopAll(); return; }
      queue = [];
      if (playAllBtn) playAllBtn.textContent = '▶ Phát lần lượt';
      playCard(card);
    });
  });

  if (playAllBtn) {
    playAllBtn.addEventListener('click', () => {
      if (queue.length || playing) { stopAll(); return; }
      const cards = [...editor.querySelectorAll('.seg-card')].filter((c) => !c.hidden && c.dataset.tts !== 'none');
      if (!cards.length) {
        document.getElementById('msg').textContent = 'Chưa có câu nào có giọng đọc để phát.';
        return;
      }
      queue = cards.slice(1);
      playAllBtn.textContent = '■ Dừng';
      playCard(cards[0]);
    });
  }

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
        if (data.changed) refreshTts();
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

// Trang Dự án & đầu trang chi tiết: xem thử khi rê chuột, đổi tên,
// lưu trữ, xoá. Thao tác nào cũng gọi API rồi sửa tại chỗ, không tải lại trang.
(function () {
  const toastEl = document.getElementById('toast');
  let toastTimer;
  function toast(text, bad) {
    if (!toastEl) return;
    toastEl.textContent = text;
    toastEl.classList.toggle('bad', !!bad);
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastEl.hidden = true; }, 3000);
  }

  async function post(url, fields) {
    const body = new FormData();
    Object.entries(fields || {}).forEach(([k, v]) => body.append(k, v));
    const res = await fetch(url, { method: 'POST', body });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.status);
    return data;
  }

  // Ảnh bìa: server cắt từ video; chưa cắt được thì dùng ảnh của nền tảng.
  document.querySelectorAll('.thumb img').forEach((img) => {
    img.addEventListener('error', () => {
      const fb = img.dataset.fallback;
      if (fb && img.src !== fb) {
        img.referrerPolicy = 'no-referrer';
        img.src = fb;
      } else {
        img.remove();
      }
    });
  });

  // Rê chuột lên ảnh là phát video câm; rời chuột thì gỡ hẳn để không giữ kết nối.
  document.querySelectorAll('.thumb[data-preview]').forEach((thumb) => {
    let video, timer;
    thumb.addEventListener('mouseenter', () => {
      timer = setTimeout(() => {
        video = document.createElement('video');
        video.src = thumb.dataset.preview;
        video.muted = true;
        video.loop = true;
        video.playsInline = true;
        video.autoplay = true;
        thumb.appendChild(video);
        thumb.classList.add('playing');
        video.play().catch(() => {});
      }, 250);
    });
    thumb.addEventListener('mouseleave', () => {
      clearTimeout(timer);
      if (video) {
        video.pause();
        video.removeAttribute('src');
        video.load();
        video.remove();
        video = null;
      }
      thumb.classList.remove('playing');
    });
  });

  function closeMenus(except) {
    document.querySelectorAll('.menu-pop').forEach((m) => { if (m !== except) m.hidden = true; });
  }
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.menu')) closeMenus();
  });

  function startRename(host) {
    const h = host.querySelector('.job-title');
    if (!h || host.querySelector('.title-input')) return;
    const input = document.createElement('input');
    input.className = 'title-input';
    input.value = host.dataset.name || host.dataset.title || '';
    input.placeholder = 'Để trống = dùng tiêu đề tự sinh';
    h.hidden = true;
    h.after(input);
    input.focus();
    input.select();

    let done = false;
    async function finish(save) {
      if (done) return;
      done = true;
      if (save && input.value.trim() !== (host.dataset.name || host.dataset.title || '')) {
        try {
          const data = await post(`/jobs/${host.dataset.jobId}/rename`, { name: input.value });
          host.dataset.name = data.name;
          host.dataset.title = data.title;
          h.textContent = data.title;
          h.title = data.title;
          if (host.dataset.search !== undefined) {
            host.dataset.search = (data.title + ' ' + host.dataset.search).toLowerCase();
          }
          toast(data.name ? 'Đã đổi tên ✓' : 'Đã bỏ tên, dùng tiêu đề tự sinh');
        } catch (err) {
          toast('Đổi tên hỏng: ' + err.message, true);
        }
      }
      input.remove();
      h.hidden = false;
    }
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); finish(true); }
      if (e.key === 'Escape') { e.preventDefault(); finish(false); }
    });
    input.addEventListener('blur', () => finish(true));
  }

  function removeCard(host) {
    if (!host.classList.contains('job-card')) return false;
    host.classList.add('removing');
    setTimeout(() => {
      const group = host.closest('.platform-group');
      host.remove();
      if (group && !group.querySelector('.job-card')) group.remove();
      if (!document.querySelector('.job-card')) location.reload();
    }, 250);
    return true;
  }

  async function setArchived(host, archived) {
    try {
      await post(`/jobs/${host.dataset.jobId}/archive`, { archived: archived ? 1 : 0 });
      if (!removeCard(host)) { location.reload(); return; }
      toast(archived ? 'Đã cất vào Lưu trữ 🗄' : 'Đã đưa về Đang làm ↩');
    } catch (err) {
      toast('Không lưu trữ được: ' + err.message, true);
    }
  }

  const dialog = document.getElementById('confirmDelete');
  function askDelete(host) {
    if (!dialog) return;
    document.getElementById('deleteName').textContent = host.dataset.title || host.dataset.jobId;
    const archiveBtn = dialog.querySelector('button[value="archive"]');
    if (archiveBtn) archiveBtn.hidden = !host.querySelector('[data-action="archive"]');
    dialog.returnValue = '';
    dialog.onclose = async () => {
      if (dialog.returnValue === 'archive') return setArchived(host, true);
      if (dialog.returnValue !== 'ok') return;
      try {
        await post(`/jobs/${host.dataset.jobId}/delete`);
        if (!removeCard(host)) { location.href = '/'; return; }
        toast('Đã xoá dự án');
      } catch (err) {
        toast('Xoá hỏng: ' + err.message, true);
      }
    };
    dialog.showModal();
  }

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-action]');
    if (!btn || btn.disabled) return;
    const host = btn.closest('.job-card, .job-head');
    if (!host) return;
    const action = btn.dataset.action;
    if (action === 'menu') {
      const pop = btn.nextElementSibling;
      closeMenus(pop);
      pop.hidden = !pop.hidden;
      return;
    }
    closeMenus();
    if (action === 'rename') startRename(host);
    else if (action === 'archive') setArchived(host, true);
    else if (action === 'unarchive') setArchived(host, false);
    else if (action === 'delete') askDelete(host);
  });

  // Nhấp đúp vào tên cũng là đổi tên.
  document.querySelectorAll('.job-card .job-title, .job-head .job-title').forEach((h) => {
    h.addEventListener('dblclick', () => startRename(h.closest('.job-card, .job-head')));
  });

  // Lưu trữ: chọn nhiều dự án để xoá, hoặc dọn sạch cả kho.
  const bulkBar = document.getElementById('bulkBar');
  const bulkDialog = document.getElementById('confirmBulk');
  if (bulkBar && bulkDialog) {
    const boxes = () => [...document.querySelectorAll('.job-card .pick input:not(:disabled)')];
    const picked = () => boxes().filter((b) => b.checked);
    const allBox = document.getElementById('bulkAll');
    const delBtn = document.getElementById('bulkDelete');

    function refresh() {
      const n = picked().length;
      document.getElementById('bulkCount').textContent = n;
      delBtn.disabled = !n;
      allBox.checked = n > 0 && n === boxes().length;
      document.querySelectorAll('.job-card').forEach((card) => {
        const b = card.querySelector('.pick input');
        card.classList.toggle('picked', !!(b && b.checked));
      });
    }
    function setSelecting(on) {
      document.body.classList.toggle('selecting', on);
      bulkBar.hidden = !on;
      if (!on) boxes().forEach((b) => { b.checked = false; });
      refresh();
    }

    function confirmBulk(what, run) {
      document.getElementById('bulkWhat').textContent = what;
      bulkDialog.returnValue = '';
      bulkDialog.onclose = () => { if (bulkDialog.returnValue === 'ok') run(); };
      bulkDialog.showModal();
    }
    async function report(req) {
      try {
        const data = await req;
        if (data.skipped.length) {
          toast(`Đã xoá ${data.deleted.length}, bỏ qua ${data.skipped.length} (đang chạy?)`, true);
          setTimeout(() => location.reload(), 1500);
        } else {
          location.reload();
        }
      } catch (err) {
        toast('Xoá hỏng: ' + err.message, true);
      }
    }

    document.getElementById('bulkStart').addEventListener('click', () => setSelecting(true));
    document.getElementById('bulkCancel').addEventListener('click', () => setSelecting(false));
    allBox.addEventListener('change', () => {
      boxes().forEach((b) => { b.checked = allBox.checked; });
      refresh();
    });
    document.addEventListener('change', (e) => { if (e.target.closest('.pick')) refresh(); });

    // Đang chọn thì bấm vào đâu trên thẻ cũng là tick, không mở dự án.
    document.addEventListener('click', (e) => {
      if (!document.body.classList.contains('selecting')) return;
      const card = e.target.closest('.job-card');
      if (!card || e.target.closest('.pick')) return;
      e.preventDefault();
      e.stopPropagation();
      const b = card.querySelector('.pick input');
      if (b && !b.disabled) { b.checked = !b.checked; refresh(); }
    }, true);

    delBtn.addEventListener('click', () => {
      const ids = picked().map((b) => b.value);
      if (!ids.length) return;
      confirmBulk(`${ids.length} dự án đã chọn`, () => {
        const body = new FormData();
        ids.forEach((id) => body.append('job_ids', id));
        report(fetch('/jobs/delete-many', { method: 'POST', body }).then(async (res) => {
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(data.detail || res.status);
          return data;
        }));
      });
    });
    document.getElementById('emptyArchive').addEventListener('click', () => {
      const total = document.querySelector('.view-switch a.on b');
      confirmBulk(`toàn bộ ${total ? total.textContent : ''} dự án trong Lưu trữ`,
        () => report(post('/archive/empty')));
    });
  }
})();

// Tab quét: dịch tiêu đề sang tiếng Việt sau khi trang đã hiện. Tiêu đề gốc
// lùi xuống một dòng nhỏ bên dưới để vẫn đối chiếu được.
(function () {
  const cards = document.querySelectorAll('.cards .card[data-title]');
  if (!cards.length) return;
  const titles = [...new Set([...cards].map((c) => c.dataset.title).filter(Boolean))];
  const status = document.getElementById('translateStatus');
  if (status) status.textContent = '· 🌐 đang dịch tiêu đề…';
  fetch('/api/translate-titles', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ titles }),
  })
    .then((r) => r.json())
    .then((data) => {
      const map = data.translations || {};
      cards.forEach((card) => {
        const vi = map[card.dataset.title];
        if (!vi || vi === card.dataset.title) return;
        const h = card.querySelector('.title-vi');
        h.textContent = vi;
        h.title = vi;
        const src = card.querySelector('.title-src');
        src.textContent = card.dataset.title;
        src.title = card.dataset.title;
        src.hidden = false;
      });
      if (status) status.textContent = data.error ? '· ⚠ dịch tiêu đề lỗi: ' + data.error : '';
    })
    .catch(() => { if (status) status.textContent = '· ⚠ không dịch được tiêu đề'; });
})();

// Tab quét: ☆/★ lưu video vào hàng chờ để làm sau, không tạo job.
(function () {
  const counter = document.getElementById('savedCount');
  document.querySelectorAll('.card [data-star]').forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      e.stopPropagation();
      const card = btn.closest('.card');
      const want = !btn.classList.contains('on');
      btn.disabled = true;
      try {
        const res = await fetch('/saved', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ video: JSON.parse(card.dataset.video), saved: want }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || res.status);
        btn.classList.toggle('on', data.saved);
        btn.textContent = data.saved ? '★' : '☆';
        btn.title = data.saved ? 'Bỏ khỏi hàng chờ' : 'Lưu vào hàng chờ để làm sau';
        if (counter) counter.textContent = data.count;
      } catch (err) {
        alert('Không lưu được: ' + err.message);
      } finally {
        btn.disabled = false;
      }
    });
  });
})();

// Hàng chờ: tick nhiều video rồi làm hàng loạt.
(function () {
  const form = document.getElementById('batchForm');
  if (!form) return;
  const boxes = [...document.querySelectorAll('input[name="keys"][form="batchForm"]')];
  const all = document.getElementById('pickAll');
  const count = document.getElementById('pickCount');
  const runPicked = document.getElementById('runPicked');
  const firstN = document.getElementById('firstN');

  function refresh() {
    const n = boxes.filter((b) => b.checked).length;
    count.textContent = n;
    runPicked.disabled = n === 0;
    runPicked.textContent = n ? `▶ Làm ${n} video đã chọn` : '▶ Làm các video đã chọn';
    all.checked = n > 0 && n === boxes.length;
    all.indeterminate = n > 0 && n < boxes.length;
    boxes.forEach((b) => b.closest('.card').classList.toggle('picked', b.checked));
  }
  boxes.forEach((b) => b.addEventListener('change', refresh));
  all.addEventListener('change', () => {
    boxes.forEach((b) => { b.checked = all.checked; });
    refresh();
  });

  // Hai nút cùng một form: nút "Làm N video đầu" gửi `first`, nút kia bỏ `first`
  // để server lấy theo các ô đã tick.
  form.addEventListener('submit', (e) => {
    const byFirst = e.submitter && e.submitter.id === 'runFirst';
    const n = byFirst ? parseInt(firstN.value, 10) || 0 : boxes.filter((b) => b.checked).length;
    if (!n) { e.preventDefault(); return; }
    if (!confirm(`Tạo và chạy ${n} job?`)) { e.preventDefault(); return; }
    firstN.disabled = !byFirst;
    if (byFirst) boxes.forEach((b) => { b.disabled = true; });
  });
  refresh();
})();

// Trang Dự án: báo vừa làm hàng loạt bao nhiêu video.
(function () {
  const n = new URLSearchParams(location.search).get('batch');
  const toast = document.getElementById('toast');
  if (!n || !toast) return;
  toast.textContent = `Đã tạo ${n} job từ hàng chờ — đang chạy lần lượt ▶`;
  toast.hidden = false;
  setTimeout(() => { toast.hidden = true; }, 4000);
  history.replaceState(null, '', location.pathname);
})();

(function () {
  // Tab Tag: dịch tên tiếng Việt thành từ khoá bằng AI, điền vào ô để sửa trước khi lưu.
  document.querySelectorAll('[data-translate]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const form = btn.closest('form');
      const vi = form.elements.vi.value.trim();
      if (!vi) { form.elements.vi.focus(); return; }
      const label = btn.textContent;
      btn.disabled = true;
      btn.textContent = '…';
      try {
        const r = await fetch('/api/tags/translate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ vi, lang: form.elements.lang.value, group: form.elements.group.value }),
        });
        const s = await r.json();
        if (s.q) form.elements.q.value = s.q;
        else alert(s.error || 'AI không dịch được');
      } catch (e) {
        alert('Không gọi được AI: ' + e);
      }
      btn.disabled = false;
      btn.textContent = label;
    });
  });

  // Đổi nền tảng khi thêm tag thì đổi luôn ngôn ngữ dịch mặc định.
  const newTag = document.querySelector('.tag-new');
  if (newTag) {
    newTag.elements.platform.addEventListener('change', () => {
      newTag.elements.lang.value = newTag.elements.platform.value === 'douyin' ? 'zh' : 'en';
    });
  }
})();
