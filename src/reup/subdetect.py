"""Phân biệt phụ đề với watermark và chữ trong cảnh — spec §7.1.

Hàm thuần, không đụng ffmpeg hay Vision. Đây là chỗ dễ sai nhất của phase 3:
blur nhầm watermark thì vô hại, nhưng blur nhầm chữ trong cảnh làm hỏng hình,
còn bỏ sót phụ đề thì chữ gốc còn nguyên trên video.

Ba dấu hiệu phân loại (spec §7.1):

| Loại       | Vị trí          | Tần suất | Chữ          |
|------------|-----------------|----------|--------------|
| Phụ đề     | 1/3 dưới, giữa  | >= 25%   | đổi liên tục |
| Watermark  | góc, nhỏ        | ~100%    | không đổi    |
| Chữ cảnh   | không ổn định   | bất kỳ   | bất kỳ       |
"""
from __future__ import annotations

from dataclasses import dataclass, field

from reup.media.ocr import TextBox

# Hai hộp coi là cùng một vùng nếu tâm lệch không quá ngần này so với chiều cao
# khung. 0.03 chứ không phải 0.06: trên video thật, hai dòng cách nhau 120px
# (bảng điểm và phụ đề) bị gộp làm một ở mức 0.06.
CLUSTER_TOLERANCE = 0.03
# Phụ đề nằm ở phần dưới khung. 0.55 chứ không phải 0.66: nhiều video đặt sub
# hơi cao hơn 1/3 dưới để chừa chỗ cho thanh tương tác của nền tảng.
SUBTITLE_TOP = 0.55
# Nằm giữa theo chiều ngang: tâm lệch khỏi giữa không quá ngần này.
CENTER_TOLERANCE = 0.22
SUBTITLE_MIN_COVERAGE = 0.25
# KHÔNG có trần tần suất. Spec §7.1 ghi "hiện 40-90%", nhưng clip thật đầu tiên
# đem ra thử có hardsub ở gần như mọi khung — người nói liên tục suốt 18 giây.
# Dấu hiệu thật sự phân biệt phụ đề với bảng điểm là **chữ có đổi hay không**,
# và điều đó đã do MIN_DISTINCT_RATIO lo.
# Watermark hiện gần như suốt video và chữ không đổi.
WATERMARK_MIN_COVERAGE = 0.90
WATERMARK_MAX_AREA = 0.06  # so với diện tích khung
# Chỉ dùng để nhận watermark, KHÔNG dùng để loại phụ đề.
#
# Bản đầu bắt phụ đề phải đổi chữ, và vì thế bỏ sót đúng cái video thật đầu
# tiên đem ra thử: nó có một caption TĨNH cháy sẵn suốt 18 giây. Caption tĩnh
# vẫn phải che. Dấu hiệu quyết định cho việc che là **vị trí ổn định**, còn
# chữ có đổi hay không chỉ nói lên đó là phụ đề chạy hay caption đứng yên.
MIN_DISTINCT_RATIO = 0.35
PADDING = 8


@dataclass
class Cluster:
    """Một vùng chữ ổn định qua nhiều khung.

    `anchor_cy` là tâm dọc của hộp ĐẦU TIÊN và không bao giờ đổi. Việc gán hộp
    mới so với mốc này chứ không so với tâm của hộp bao: nếu so với hộp bao thì
    cụm nở ra tới đâu, tâm dịch tới đó, và nó hút dần các dòng lân cận. Trên
    video thật, bảng điểm và phụ đề đã bị gộp thành một khối cao 206px vì lỗi đó.
    """

    x: int
    y: int
    w: int
    h: int
    frames: set[int] = field(default_factory=set)
    texts: list[str] = field(default_factory=list)
    anchor_cy: float | None = None
    anchor_cx: float | None = None

    def __post_init__(self) -> None:
        if self.anchor_cy is None:
            self.anchor_cy = self.y + self.h / 2
        if self.anchor_cx is None:
            self.anchor_cx = self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    def absorb(self, box: TextBox, frame: int) -> None:
        """Mở rộng thành hợp của hai hộp — thà blur rộng hơn còn hơn sót chữ."""
        right = max(self.x + self.w, box.x + box.w)
        bottom = max(self.y + self.h, box.y + box.h)
        self.x = min(self.x, box.x)
        self.y = min(self.y, box.y)
        self.w = right - self.x
        self.h = bottom - self.y
        self.frames.add(frame)
        self.texts.append(box.text)

    def distinct_ratio(self) -> float:
        if not self.texts:
            return 0.0
        return len(set(self.texts)) / len(self.texts)


@dataclass(frozen=True)
class Region:
    x: int
    y: int
    w: int
    h: int
    kind: str
    coverage: float


def cluster_boxes(
    frames: list[list[TextBox]], height: int, tolerance: float = CLUSTER_TOLERANCE
) -> list[Cluster]:
    """Gom hộp chữ của nhiều khung thành các vùng ổn định, theo vị trí tâm."""
    limit = height * tolerance
    clusters: list[Cluster] = []
    for index, boxes in enumerate(frames):
        for box in boxes:
            match = None
            best = limit
            for c in clusters:
                dy = abs(c.anchor_cy - box.cy)
                dx = abs(c.anchor_cx - box.cx)
                # Dọc chặt, ngang lỏng: phụ đề đứng yên theo chiều dọc nhưng
                # co giãn theo chiều ngang vì câu dài ngắn khác nhau.
                if dy <= limit and dx <= limit * 4 and dy < best:
                    best = dy
                    match = c
            if match is None:
                clusters.append(
                    Cluster(box.x, box.y, box.w, box.h, {index}, [box.text])
                )
            else:
                match.absorb(box, index)
    return clusters


def classify(cluster: Cluster, total_frames: int, width: int, height: int) -> str:
    if total_frames <= 0:
        raise ValueError("total_frames phải dương")
    coverage = len(cluster.frames) / total_frames
    area_ratio = (cluster.w * cluster.h) / (width * height)
    in_lower = cluster.cy >= height * SUBTITLE_TOP
    centered = abs(cluster.cx - width / 2) <= width * CENTER_TOLERANCE

    # Xét phụ đề TRƯỚC watermark. Một caption tĩnh ở giữa dưới thoả cả hai luật,
    # và phải gọi là phụ đề: chỉ vùng "subtitle" mới được stage ocr đọc chữ, mà
    # chữ đó chính là thứ đem đối chiếu với ASR.
    if in_lower and centered and coverage >= SUBTITLE_MIN_COVERAGE:
        return "subtitle"

    if (
        coverage >= WATERMARK_MIN_COVERAGE
        and area_ratio <= WATERMARK_MAX_AREA
        and cluster.distinct_ratio() < MIN_DISTINCT_RATIO
    ):
        return "watermark"

    return "scene"


def detect_regions(
    frames: list[list[TextBox]], width: int, height: int, padding: int = PADDING
) -> list[Region]:
    """Trả về các vùng cần blur (phụ đề và watermark), đã cộng đệm."""
    total = len(frames)
    if total == 0:
        return []
    regions = []
    for c in cluster_boxes(frames, height):
        kind = classify(c, total, width, height)
        if kind == "scene":
            continue  # chữ trong cảnh: blur vào là hỏng hình
        x = max(0, c.x - padding)
        y = max(0, c.y - padding)
        regions.append(
            Region(
                x=x,
                y=y,
                w=min(width - x, c.w + padding * 2),
                h=min(height - y, c.h + padding * 2),
                kind=kind,
                coverage=round(len(c.frames) / total, 4),
            )
        )
    return regions


def present_ranges(
    frames: list[list[TextBox]], width: int, height: int, step_ms: int
) -> list[list[int]]:
    """Các khoảng thời gian có phụ đề, gộp các khung liền nhau."""
    total = len(frames)
    if total == 0:
        return []
    sub_frames: set[int] = set()
    for c in cluster_boxes(frames, height):
        if classify(c, total, width, height) == "subtitle":
            sub_frames |= c.frames

    ranges: list[list[int]] = []
    for index in sorted(sub_frames):
        start = index * step_ms
        end = start + step_ms
        if ranges and ranges[-1][1] == start:
            ranges[-1][1] = end
        else:
            ranges.append([start, end])
    return ranges
