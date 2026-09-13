// reup — xuất danh sách video của một kênh Douyin ra file JSON để nạp vào tab Douyin.
//
// Cách dùng: mở https://www.douyin.com/user/<sec_uid> trên trình duyệt ĐÃ ĐĂNG NHẬP,
// F12 → Console, dán toàn bộ đoạn này rồi Enter. Xong sẽ tải về douyin_<id>.json.
//
// Vì sao phải chạy trong trình duyệt: API /aweme/v1/web/aweme/post/ trả body rỗng
// cho request không mang cookie đăng nhập của trang. Cùng API và cách phân trang
// max_cursor với các script trong douyin-doc/, nhưng chỉ xuất dữ liệu — việc chọn
// theo like, theo vị trí, và tải video làm ở reup.
(async () => {
  const FORMAT = "reup-douyin/1";
  const secUserId = (location.pathname.split("/user/")[1] || "").split(/[/?#]/)[0];
  if (!location.hostname.endsWith("douyin.com") || !secUserId) {
    alert("Mở trang kênh Douyin (douyin.com/user/...) rồi chạy lại.");
    return;
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const https = (u) => (u || "").replace(/^http:\/\//, "https://");

  // Bản gốc thử lại vô hạn khi JSON hỏng; ở đây dừng sau 5 lần để chưa đăng nhập
  // thì báo lỗi thay vì treo tab.
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
      } catch (e) {
        console.warn("reup: thử lại trang", cursor, e);
      }
      await sleep(800 * attempt);
    }
    throw new Error("Douyin trả rỗng 5 lần liền — kiểm tra đã đăng nhập chưa.");
  }

  const videos = [];
  let cursor = 0;
  let hasMore = true;
  let index = 0; // vị trí trên kênh, đếm cả bài ảnh để khớp với thứ tự người xem thấy
  let author = "";

  while (hasMore) {
    const data = await page(cursor);
    const list = data.aweme_list || [];
    if (!list.length) break;
    for (const item of list) {
      index++;
      const play = item.video?.play_addr?.url_list?.[0];
      if (!play || (item.images && item.images.length)) continue; // bài ảnh, không có video
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
    console.log(`reup: đã lấy ${videos.length} video`);
    hasMore = Boolean(data.has_more);
    cursor = data.max_cursor;
    await sleep(500); // tránh rate-limit
  }

  if (!videos.length) {
    alert("reup: không lấy được video nào từ kênh này.");
    return;
  }
  const out = {
    format: FORMAT,
    sec_user_id: secUserId,
    author,
    exported_at: new Date().toISOString(),
    videos,
  };
  const blob = new Blob([JSON.stringify(out)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `douyin_${secUserId.slice(0, 24)}.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  alert(`reup: đã xuất ${videos.length} video. Nạp file này ở tab Douyin (link tải hết hạn sau vài giờ).`);
})();
