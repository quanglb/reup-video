// reup gọi qua AppleScript (adapters/douyin_chrome.py) trong tab kênh Douyin của
// Chrome đã đăng nhập. Cùng logic quét với web/static/douyin_export.js nhưng không
// alert, không tải file: kết quả để ở window.__reupExport.json cho Python đọc từng
// đoạn. Gọi lại khi đang chạy chỉ trả trạng thái — Python dùng chính nó để hỏi tiến độ.
// Luôn trả chuỗi JSON vì AppleScript chỉ chuyển được giá trị đơn giản.
(() => {
  const view = (s) =>
    JSON.stringify({
      status: s.status,
      videos: s.videos,
      error: s.error,
      size: s.json ? s.json.length : 0,
    });
  const st = window.__reupExport;
  if (st && (st.status === "running" || st.status === "done")) return view(st);

  const state = (window.__reupExport = { status: "running", videos: 0, error: "", json: "" });
  const secUserId = (location.pathname.split("/user/")[1] || "").split(/[/?#]/)[0];
  if (!location.hostname.endsWith("douyin.com") || !secUserId) {
    Object.assign(state, { status: "error", error: "tab không phải trang kênh douyin.com/user/..." });
    return view(state);
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const https = (u) => (u || "").replace(/^http:\/\//, "https://");

  async function page(cursor) {
    const url =
      "https://www.douyin.com/aweme/v1/web/aweme/post/?device_platform=webapp&aid=6383" +
      `&channel=channel_pc_web&sec_user_id=${encodeURIComponent(secUserId)}` +
      `&max_cursor=${cursor}&count=18`;
    for (let attempt = 1; attempt <= 5; attempt++) {
      try {
        const res = await fetch(url, { method: "GET", credentials: "include" });
        const text = await res.text();
        if (text) return JSON.parse(text);
      } catch (e) {}
      await sleep(800 * attempt);
    }
    throw new Error("Douyin trả rỗng 5 lần liền — Chrome profile này đã đăng nhập Douyin chưa?");
  }

  (async () => {
    const videos = [];
    let cursor = 0, hasMore = true, index = 0, author = "", stalls = 0;
    while (hasMore) {
      const data = await page(cursor);
      const list = data.aweme_list || [];
      for (const item of list) {
        index++;
        const play = item.video?.play_addr?.url_list?.[0];
        if (!play || (item.images && item.images.length)) continue; // bài ảnh
        const s = item.statistics || {};
        author = author || item.author?.nickname || "";
        videos.push({
          position: index,
          aweme_id: item.aweme_id,
          desc: (item.desc || "").replace(/\s+/g, " ").trim(),
          digg_count: s.digg_count || 0,
          share_count: s.share_count || 0,
          comment_count: s.comment_count || 0,
          play_count: s.play_count || 0,
          duration_ms: item.video?.duration || item.duration || 0,
          create_time: item.create_time || 0,
          author: item.author?.nickname || "",
          cover: https(item.video?.cover?.url_list?.[0]),
          play_url: https(play),
        });
      }
      state.videos = videos.length;
      hasMore = Boolean(data.has_more);
      // Douyin hay trả trang rỗng mà has_more vẫn 1; chỉ bỏ cuộc khi đứng yên 5 lần.
      const advanced = data.max_cursor && data.max_cursor !== cursor;
      if (!list.length || !advanced) {
        if (++stalls >= 5) break;
        await sleep(1500 * stalls);
      } else {
        stalls = 0;
      }
      if (advanced) cursor = data.max_cursor;
      await sleep(600);
    }
    if (!videos.length) throw new Error("không lấy được video nào từ kênh này.");
    state.json = JSON.stringify({
      format: "reup-douyin/1",
      sec_user_id: secUserId,
      author,
      exported_at: new Date().toISOString(),
      videos,
    });
    state.status = "done";
  })().catch((e) => Object.assign(state, { status: "error", error: String(e.message || e) }));

  return view(state);
})();
