"""Chống dính chính sách TikTok — luật chữ, precheck, cách che, meta, policy."""
import json
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from reup import cover
from reup.core.job import create_job
from reup.models import Segment, Transcript
from reup.policy import PolicyBlocked, local, review, rules
from reup.stages import compose as compose_stage
from reup.stages import export as export_stage
from reup.stages import frame as frame_stage
from reup.stages import meta as meta_stage
from reup.stages import policy as policy_stage
from reup.stages import precheck as precheck_stage


# --- luật chữ ---------------------------------------------------------------

def test_the_deleted_video_title_is_caught():
    """Tiêu đề video thật đã bị TikTok xoá."""
    hits = rules.scan_text("Tết này chán đánh bài? Học ngay các chiêu múa bài Thần Bài cực ảo")
    assert {h.term for h in hits} == {"đánh bài", "thần bài"}
    assert rules.worst(hits) == rules.RISKY


def test_chinese_gambling_tags_in_the_source_are_caught():
    hits = rules.scan_text("过年不想打牌怎么办 #赌神 #我要验牌 #魔术教学")
    assert {h.term for h in hits} == {"赌神", "验牌"}


def test_promoting_gambling_is_a_hard_block():
    assert rules.worst(rules.scan_text("Game bài đổi thưởng uy tín nhất")) == rules.BLOCK
    assert rules.worst(rules.scan_text("棋牌游戏")) == rules.BLOCK


def test_game_slang_is_not_flagged():
    """Dòng LOL nói thế suốt — không được bắt nhầm."""
    assert rules.scan_text("Khi Shaco vào tay Pink Ward: Đỉnh cao hack não, bom tấn mãn nhãn") == []


def test_word_boundaries_hold():
    assert rules.scan_text("sexy dance") == []
    assert rules.scan_text("Bịp thế này ai chịu nổi")


def test_soften_replaces_and_keeps_casing():
    assert rules.soften("Kéo bài kiểu Thần Bài") == "Kéo bài kiểu Cao Thủ Ảo Thuật"
    assert rules.soften("tết chán đánh bài") == "tết chán chơi bài"
    assert rules.soften("Tráo bài nhanh") == "Đổi bài nhanh"
    assert rules.scan_text(rules.soften("Thần Bài đánh bài")) == []


def test_hashtags_drop_cross_platform_generic_and_sensitive_tags():
    tags = ["#Shorts", "Reels", "fypシ", "viral", "thanbai", "aothuat", "cardistry", "xuhuong"]
    assert rules.clean_hashtags(tags) == ["aothuat", "cardistry"]


def test_hashtags_are_capped_at_five():
    assert len(rules.clean_hashtags([f"tag{i}" for i in range(20)])) == 5


def test_cross_platform_tags_are_removed_from_text():
    assert rules.strip_cross_platform("Hay quá #viral #Shorts #aothuat") == "Hay quá #aothuat"


def test_templated_titles_are_similar_and_distinct_ones_are_not():
    old = ["Đẳng cấp Thách Đấu Hàn: Những pha xử lý xứng đáng gọi là nghệ thuật"]
    same, _ = rules.most_similar("Đẳng cấp Thách Đấu Hàn: Những pha xử lý đỉnh cao khiến bạn mê", old)
    other, _ = rules.most_similar("Mẹo ảo thuật giải Rubik trong chớp mắt", old)
    assert same >= rules.SIMILAR_TITLE_RATIO > other


def test_vocab_guide_names_risky_categories():
    guide = rules.vocab_guide(["gambling_theme"])
    assert "Thần bài" in guide or "thần bài" in guide
    assert "chủ đề bài bạc" in guide


# --- precheck ---------------------------------------------------------------

def test_precheck_flags_but_does_not_block_a_magic_video(cfg_fixture):
    r = precheck_stage.evaluate("学会这五招 #赌神 #魔术教学", "", 120, 1080, cfg_fixture)
    assert not r["blocked"]
    assert r["risk_categories"] == ["gambling_theme"]


def test_precheck_blocks_gambling_promotion_and_tiny_sources(cfg_fixture):
    assert precheck_stage.evaluate("棋牌 下注 赢钱", "", 60, 1080, cfg_fixture)["blocked"]
    assert precheck_stage.evaluate("ok", "", 60, 300, cfg_fixture)["blocked"]
    assert precheck_stage.evaluate("ok", "", 2.0, 1080, cfg_fixture)["blocked"]
    warn = precheck_stage.evaluate("ok", "", 60, 420, cfg_fixture)
    assert not warn["blocked"] and warn["warnings"]


def test_precheck_stage_stops_the_job_and_allow_lets_it_through(tmp_path, sample_video, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.source_video.write_bytes(sample_video.read_bytes())
    job.source_info.write_text(json.dumps({"title": "博彩 下注"}), encoding="utf-8")
    with pytest.raises(PolicyBlocked, match="policy-allow"):
        precheck_stage.run(job, cfg_fixture)
    assert not job.precheck_json.exists()  # để lần sau kiểm lại

    job.set_override("policy", "allow")
    precheck_stage.run(job, cfg_fixture)
    assert json.loads(job.precheck_json.read_text())["blocked"] is True


# --- cách che: không còn ô mờ trống -----------------------------------------

def _img(color, size=(200, 200), box=None, box_color=(255, 255, 255)):
    im = Image.new("RGB", size, color)
    if box:
        im.paste(box_color, (box[0], box[1], box[0] + box[2], box[1] + box[3]))
    return im


def test_text_on_a_letterbox_is_filled_with_the_background_colour():
    box = {"x": 50, "y": 80, "w": 100, "h": 30}
    imgs = [_img((0, 0, 0), box=(50, 80, 100, 30)) for _ in range(3)]
    assert cover.classify(imgs, box) == {"mode": "flat", "color": "0x000000"}


def test_text_over_picture_gets_a_plate():
    import random

    rnd = random.Random(1)
    noisy = Image.new("RGB", (200, 200))
    noisy.putdata([tuple(rnd.randrange(256) for _ in range(3)) for _ in range(200 * 200)])
    assert cover.classify([noisy, noisy], {"x": 50, "y": 80, "w": 100, "h": 30})["mode"] == "plate"


def test_anything_entering_the_ring_in_any_frame_means_plate():
    """Một bàn tay thò vào viền ở một khung: tô đen là cắt ngang bàn tay."""
    box = {"x": 50, "y": 80, "w": 100, "h": 30}
    clean = _img((0, 0, 0))
    dirty = _img((0, 0, 0), box=(40, 70, 60, 50))
    assert cover.classify([dirty, clean, clean, clean], box)["mode"] == "plate"


def test_neighbouring_text_that_is_also_covered_does_not_count(tmp_path):
    """Câu kề bên / dòng tiêu đề sát bên cũng bị che — chữ đó không phải vật thò vào."""
    frames = []
    for i in range(6):
        p = tmp_path / f"f_{i:05d}.png"
        # khung 0: câu kề bên (vùng che khác) tràn vào viền bên phải
        _img((0, 0, 0), box=(150, 80, 40, 30) if i == 0 else None).save(p)
        frames.append(p)
    region = {"x": 50, "y": 80, "w": 100, "h": 30, "start_ms": 0, "end_ms": 2500}
    neighbour = {"x": 150, "y": 80, "w": 40, "h": 30, "start_ms": 0, "end_ms": 400}
    assert cover.classify_region(frames, region)["mode"] == "plate"
    assert cover.classify_region(frames, region, others=[region, neighbour])["mode"] == "flat"


def test_flat_regions_are_drawboxes_without_blur():
    chain, out = compose_stage.build_blur_chain([
        {"x": 10, "y": 20, "w": 300, "h": 90, "start_ms": 0, "end_ms": 1000,
         "fill": {"mode": "flat", "color": "0x000000"}},
        {"x": 400, "y": 20, "w": 100, "h": 60, "fill": {"mode": "flat", "color": "0x101010"}},
    ])
    assert "boxblur" not in chain and "split" not in chain
    assert "drawbox=x=10:y=20:w=300:h=90:color=0x000000@1:t=fill:enable=" in chain
    assert out == "[flat]"


def test_mixed_flat_and_plate_regions_chain_together():
    chain, out = compose_stage.build_blur_chain([
        {"x": 10, "y": 20, "w": 300, "h": 90, "fill": {"mode": "plate"}},
        {"x": 400, "y": 20, "w": 100, "h": 60, "fill": {"mode": "flat", "color": "0x000000"}},
    ])
    assert chain.count("boxblur") == 1
    # tô phẳng trước, dải plate đè lên sau
    assert chain.index("drawbox=x=400") < chain.index("boxblur")
    assert "[flat]split=2" in chain
    assert out == "[cl0]"


# --- meta -------------------------------------------------------------------

class ScriptedLLM:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def complete_json(self, prompt, schema):
        self.prompts.append(prompt)
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def _job_with_translation(tmp_path, title_src="过年 #赌神"):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.source_info.write_text(json.dumps({"title": title_src}), encoding="utf-8")
    Transcript(source_lang="vi", segments=[
        Segment(id=1, start_ms=0, end_ms=2000, text="Kéo bài kiểu cao thủ ảo thuật"),
    ]).save(job.translation_json)
    return job


def _cfg(cfg_fixture, tmp_path):
    return replace(cfg_fixture, review=replace(cfg_fixture.review, output_dir=str(tmp_path / "out")))


def test_meta_softens_and_cleans_without_a_second_call(tmp_path, cfg_fixture):
    job = _job_with_translation(tmp_path)
    llm = ScriptedLLM({"title": "Học múa bài kiểu Thần Bài #shorts", "description": "Tráo bài cực nhanh",
                       "hashtags": ["thanbai", "aothuat", "fyp", "cardistry"]})
    meta = meta_stage.run_with(job, _cfg(cfg_fixture, tmp_path), llm)
    assert meta["title"] == "Học múa bài kiểu Cao Thủ Ảo Thuật"
    assert meta["description"] == "Đổi bài cực nhanh"
    assert meta["hashtags"] == ["aothuat", "cardistry"]
    assert len(llm.prompts) == 1
    assert "LUẬT AN TOÀN" in llm.prompts[0]


def test_meta_repairs_a_title_that_still_breaks_rules(tmp_path, cfg_fixture):
    job = _job_with_translation(tmp_path)
    llm = ScriptedLLM(
        {"title": "Mẹo cờ bạc ngày Tết", "description": "d", "hashtags": ["aothuat"]},
        {"title": "Mẹo ảo thuật bài ngày Tết", "description": "d2"},
    )
    meta = meta_stage.run_with(job, _cfg(cfg_fixture, tmp_path), llm)
    assert meta["title"] == "Mẹo ảo thuật bài ngày Tết"
    assert meta["repaired"]
    assert len(llm.prompts) == 2


def test_meta_asks_for_a_different_title_than_recent_ones(tmp_path, cfg_fixture):
    cfg = _cfg(cfg_fixture, tmp_path)
    out = cfg.output_dir_path()
    out.mkdir(parents=True)
    (out / "01. Đẳng cấp Thách Đấu Hàn Những pha xử lý xứng đáng.json").write_text(
        json.dumps({"title": "Đẳng cấp Thách Đấu Hàn: Những pha xử lý xứng đáng gọi là nghệ thuật",
                    "source_url": "https://other"}), encoding="utf-8")
    job = _job_with_translation(tmp_path, "lol")
    llm = ScriptedLLM(
        {"title": "Đẳng cấp Thách Đấu Hàn: Những pha xử lý đỉnh cao khiến bạn mê", "description": "d",
         "hashtags": ["lol"]},
        {"title": "Faker né chiêu trong gang tấc", "description": "d"},
    )
    meta = meta_stage.run_with(job, cfg, llm)
    assert "Đẳng cấp Thách Đấu Hàn" in llm.prompts[0]  # tiêu đề cũ có trong prompt
    assert meta["title"] == "Faker né chiêu trong gang tấc"


def test_meta_translates_burned_in_static_text(tmp_path, cfg_fixture):
    job = _job_with_translation(tmp_path, "magic")
    job.subrect_json.write_text(json.dumps({"video_w": 1080, "video_h": 1920, "regions": [
        {"x": 100, "y": 300, "w": 600, "h": 70, "kind": "watermark", "coverage": 1.0,
         "text": "教你一招一摸就变的变牌手法"},
        {"x": 50, "y": 1300, "w": 900, "h": 80, "kind": "subtitle", "coverage": 0.9},
    ]}), encoding="utf-8")
    llm = ScriptedLLM({"title": "Đổi lá bài chỉ bằng một cái vuốt", "description": "d",
                       "hashtags": ["aothuat"], "headers": [{"id": 0, "vi": "Mẹo đổi bài bằng một cái chạm"}]})
    meta = meta_stage.run_with(job, _cfg(cfg_fixture, tmp_path), llm)
    assert meta["headers"] == [{"x": 100, "y": 300, "w": 600, "h": 70,
                                "src": "教你一招一摸就变的变牌手法", "vi": "Mẹo đổi bài bằng một cái chạm"}]
    assert "教你一招一摸就变的变牌手法" in llm.prompts[0]


def test_header_left_in_chinese_is_dropped(tmp_path, cfg_fixture):
    job = _job_with_translation(tmp_path, "magic")
    job.subrect_json.write_text(json.dumps({"video_w": 1080, "video_h": 1920, "regions": [
        {"x": 100, "y": 300, "w": 600, "h": 70, "kind": "watermark", "coverage": 1.0, "text": "学会吓哭妹妹"},
    ]}), encoding="utf-8")
    llm = ScriptedLLM({"title": "t", "description": "d", "hashtags": [], "headers": [{"id": 0, "vi": "学会"}]})
    assert meta_stage.run_with(job, _cfg(cfg_fixture, tmp_path), llm)["headers"][0]["vi"] == ""


# --- AI review --------------------------------------------------------------

def test_review_answer_is_normalized():
    out = review.normalize({"verdict": "REJECT", "issues": [
        {"code": "c1", "severity": "HIGH", "note": "dạy gian lận"}, {"code": "ZZ", "severity": "high"}]})
    assert out["verdict"] == "reject"
    assert out["issues"] == [{"code": "C1", "severity": "high", "note": "dạy gian lận"}]


def test_review_prompt_is_compact_and_carries_local_findings():
    prompt = review.build_prompt(
        {"title": "T", "description": "D", "hashtags": ["a"]}, "tóm tắt",
        review.excerpt_of(["x" * 2000]),
        ["câu dính"], ["có chữ Hán"], 6)
    assert "có chữ Hán" in prompt and "câu dính" in prompt
    assert len(prompt) < 3500


def test_contact_sheet_is_one_small_jpeg(tmp_path):
    frames = []
    for i in range(12):
        p = tmp_path / f"f{i}.jpg"
        Image.new("RGB", (720, 1280), (i * 20, 0, 0)).save(p)
        frames.append(p)
    data = local.contact_sheet(frames, 6, 768)
    im = Image.open(__import__("io").BytesIO(data))
    assert im.format == "JPEG" and im.width == 768
    assert len(data) < 200_000


# --- policy stage -----------------------------------------------------------

class VisionLLM:
    supports_images = True

    def __init__(self, answer):
        self.answer = answer
        self.calls = 0
        self.last_usage = {"total_tokens": 1234}

    def complete_json_with_images(self, prompt, images, schema):
        self.calls += 1
        assert images and images[0][:2] == b"\xff\xd8"
        return self.answer

    def complete_json(self, prompt, schema):
        self.calls += 1
        return self.answer


@pytest.fixture
def rendered_job(tmp_path, sample_video):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.final_mp4.parent.mkdir(parents=True, exist_ok=True)
    job.final_mp4.write_bytes(sample_video.read_bytes())
    job.meta_json.write_text(json.dumps({
        "title": "Mẹo đổi bài cực nhanh", "description": "d", "hashtags": ["aothuat"],
        "source_url": "https://a/1", "source_title": "x"}), encoding="utf-8")
    Transcript(source_lang="vi", segments=[
        Segment(id=1, start_ms=0, end_ms=2000, text="Đặt lá bài lên trên"),
    ]).save(job.translation_json)
    return job


def _use(monkeypatch, llm):
    monkeypatch.setattr("reup.adapters.registry.make_llm", lambda cfg, role=None: llm)


def test_policy_passes_a_clean_video_with_one_ai_call(rendered_job, cfg_fixture, monkeypatch, tmp_path):
    llm = VisionLLM({"verdict": "pass", "issues": []})
    _use(monkeypatch, llm)
    policy_stage.run(rendered_job, _cfg(cfg_fixture, tmp_path))
    result = json.loads(rendered_job.policy_json.read_text())
    assert result["verdict"] == "pass" and llm.calls == 1
    assert result["ai"]["usage"] == {"total_tokens": 1234}


def test_policy_rejects_and_keeps_the_ai_answer_for_free_reruns(rendered_job, cfg_fixture, monkeypatch, tmp_path):
    llm = VisionLLM({"verdict": "reject", "issues": [
        {"code": "C1", "severity": "high", "note": "dạy lén đổi bài để gian lận"}]})
    _use(monkeypatch, llm)
    cfg = _cfg(cfg_fixture, tmp_path)
    with pytest.raises(PolicyBlocked, match="C1"):
        policy_stage.run(rendered_job, cfg)
    assert not rendered_job.policy_json.exists()  # export không chạy

    rendered_job.set_override("policy", "allow")
    policy_stage.run(rendered_job, cfg)
    assert llm.calls == 1  # lần chạy lại dùng kết quả cũ
    assert json.loads(rendered_job.policy_json.read_text())["allowed_by_user"] is True


def test_policy_applies_a_safe_title_fix(rendered_job, cfg_fixture, monkeypatch, tmp_path):
    _use(monkeypatch, VisionLLM({"verdict": "fix", "issues": [{"code": "T1", "severity": "low"}],
                                 "title": "Đổi lá bài trong chớp mắt", "hashtags": ["aothuat", "fyp"]}))
    policy_stage.run(rendered_job, _cfg(cfg_fixture, tmp_path))
    meta = json.loads(rendered_job.meta_json.read_text())
    assert meta["title"] == "Đổi lá bài trong chớp mắt"
    assert meta["hashtags"] == ["aothuat"]
    assert json.loads(rendered_job.policy_json.read_text())["verdict"] == "fixed"


def test_policy_ignores_an_unsafe_title_fix(rendered_job, cfg_fixture, monkeypatch, tmp_path):
    _use(monkeypatch, VisionLLM({"verdict": "fix", "issues": [], "title": "Thần bài 赌神"}))
    policy_stage.run(rendered_job, _cfg(cfg_fixture, tmp_path))
    assert json.loads(rendered_job.meta_json.read_text())["title"] == "Mẹo đổi bài cực nhanh"


def test_hard_rule_violation_blocks_without_calling_ai(rendered_job, cfg_fixture, monkeypatch, tmp_path):
    meta = json.loads(rendered_job.meta_json.read_text())
    meta["description"] = "Vào nhà cái uy tín"
    rendered_job.meta_json.write_text(json.dumps(meta), encoding="utf-8")
    llm = VisionLLM({"verdict": "pass", "issues": []})
    _use(monkeypatch, llm)
    with pytest.raises(PolicyBlocked, match="nhà cái"):
        policy_stage.run(rendered_job, _cfg(cfg_fixture, tmp_path))
    assert llm.calls == 0


def test_risky_mode_skips_ai_for_a_clean_video(rendered_job, cfg_fixture, monkeypatch, tmp_path):
    llm = VisionLLM({"verdict": "pass", "issues": []})
    _use(monkeypatch, llm)
    cfg = _cfg(cfg_fixture, tmp_path)
    cfg = replace(cfg, policy=replace(cfg.policy, ai="risky", min_duration_s=1))
    policy_stage.run(rendered_job, cfg)
    assert llm.calls == 0


def test_found_chinese_text_on_most_frames_blocks(rendered_job, cfg_fixture, monkeypatch, tmp_path):
    def fake_scan(video, work, n):
        rep = local.LocalReport(width=540, height=960, duration_s=6)
        rep.frames = [Path(f"/x/{i}.jpg") for i in range(4)]
        rep.cjk_frames = [(1.0, "教你一招"), (2.0, "教你一招")]
        return rep

    monkeypatch.setattr(local, "scan", fake_scan)
    llm = VisionLLM({"verdict": "pass", "issues": []})
    _use(monkeypatch, llm)
    with pytest.raises(PolicyBlocked, match="chữ Hán"):
        policy_stage.run(rendered_job, _cfg(cfg_fixture, tmp_path))
    assert llm.calls == 0


# --- export / frame ---------------------------------------------------------

def test_exported_json_has_a_ready_caption_and_no_internal_fields():
    out = export_stage.public_meta({"title": "T", "description": "D", "hashtags": ["a", "b"],
                                    "headers": [{"vi": "x"}], "source_url": "u", "source_title": "s"},
                                   {"verdict": "pass", "notes": []})
    assert "headers" not in out
    assert out["caption"] == "T\nD\n#a #b"
    assert out["policy"]["verdict"] == "pass"


def test_frame_chain_no_longer_blurs_the_fill():
    chain = frame_stage.build_frame_chain((0, 546, 1080, 824), 1, 586, 576)
    assert "boxblur" not in chain
    assert f"color={frame_stage.FILL_COLOR}" in chain


def test_frame_chain_crops_when_the_shape_is_close():
    chain = frame_stage.build_frame_chain((0, 546, 1080, 824), 1, 1024, 768)
    assert "crop=1080:824" in chain and "boxblur" not in chain


def test_frame_title_is_drawn_above_the_video(tmp_path):
    from reup import projects
    from reup.subtitle import find_font

    png = tmp_path / "frame.png"
    rect = projects.render_frame(projects.PROJECTS["lol"].frame, png, 1080, 1920)
    before = Image.open(png).convert("RGB").crop((0, 0, 1080, rect[1])).tobytes()
    assert frame_stage.draw_title(png, rect, "Faker né chiêu trong gang tấc", find_font("Be Vietnam Pro"))
    after = Image.open(png).convert("RGB").crop((0, 0, 1080, rect[1])).tobytes()
    assert before != after


def test_pervasive_gambling_subtitles_block_but_a_scam_story_does_not(cfg_fixture):
    rep = local.LocalReport(width=1080, height=1920, duration_s=60)
    gamble = ["Kéo bài kiểu Thần Bài", "Thần Bài đổi bài", "Phi Thần Bài", "câu thường"]
    hard, _ = policy_stage.local_findings(rep, [], False, cfg_fixture, {}, (0, ""), gamble, "j")
    assert hard and "thần bài" in hard[0]
    story = ["Hắn là kẻ lừa đảo", "Đừng tin tên lừa đảo", "Lừa đảo cả làng", "Hắn rút súng"]
    hard, _ = policy_stage.local_findings(rep, [], False, cfg_fixture, {}, (0, ""), story, "j")
    assert hard == []


# --- nhãn chữ Hán trong cảnh ------------------------------------------------

def _tb(text, x, y, w=90, h=32, conf=1.0):
    from reup.media.ocr import TextBox
    return TextBox(text, conf, x, y, w, h)


def test_scene_label_becomes_a_timed_track():
    from reup.labels import find_labels

    frames = [[], [_tb("黑桃10", 488, 814)], [_tb("黑桃10", 486, 814)], [_tb("黑桃10", 488, 815)], []]
    (lb,) = find_labels(frames, 720, 1428, 500, [])
    assert lb["text"] == "黑桃10"
    assert (lb["start_ms"], lb["end_ms"]) == (250, 2250)
    assert lb["x"] < 486 and lb["x"] + lb["w"] > 488 + 90


def test_label_jumping_elsewhere_starts_a_new_track_and_flicker_is_ignored():
    from reup.labels import find_labels

    frames = [[_tb("黑桃10", 488, 814)], [_tb("黑桃10", 488, 814), _tb("黑桃10", 100, 300)],
              [_tb("黑桃10", 100, 300), _tb("噪", 600, 100)]]
    found = find_labels(frames, 720, 1428, 500, [])
    assert len(found) == 2
    assert all(f["text"] == "黑桃10" for f in found)


def test_labels_skip_latin_text_and_already_covered_regions():
    from reup.labels import find_labels

    frames = [[_tb("LIGHTUNDERSTORM", 100, 100), _tb("字幕", 300, 950)]] * 3
    covered = [{"x": 40, "y": 920, "w": 640, "h": 80}]
    assert find_labels(frames, 720, 1428, 500, covered) == []


def test_meta_translates_each_label_text_once(tmp_path, cfg_fixture):
    job = _job_with_translation(tmp_path, "magic")
    lab = {"x": 430, "y": 800, "w": 200, "h": 50, "line_h": 34, "text": "黑桃10"}
    job.subrect_json.write_text(json.dumps({"video_w": 720, "video_h": 1428, "regions": [], "labels": [
        {**lab, "start_ms": 1000, "end_ms": 3000}, {**lab, "start_ms": 9000, "end_ms": 9500}]}),
        encoding="utf-8")
    llm = ScriptedLLM({"title": "Đoán bài", "description": "d", "hashtags": [],
                       "labels": [{"id": 0, "vi": "10 Bích"}]})
    meta = meta_stage.run_with(job, _cfg(cfg_fixture, tmp_path), llm)
    assert "  0. 黑桃10" in llm.prompts[0] and "  1. 黑桃10" not in llm.prompts[0]
    assert [m["vi"] for m in meta["labels"]] == ["10 Bích", "10 Bích"]
    assert compose_stage.label_regions(job)[1]["start_ms"] == 9000
