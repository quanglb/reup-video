from pathlib import Path

from PIL import Image

from reup import projects
from reup.core.job import create_job
from reup.media.ffmpeg import probe
from reup.stages import frame as frame_stage
from reup.stages import stages_for


def test_lol_frame_green_box_found():
    x, y, w, h = projects.green_box(projects.PROJECTS["lol"].frame)
    assert x == 0 and w == 1.0
    assert 0.25 < y < 0.32
    assert 0.40 < h < 0.46


def test_render_frame_cuts_a_transparent_hole(tmp_path: Path):
    out = tmp_path / "frame.png"
    x, y, w, h = projects.render_frame(projects.PROJECTS["lol"].frame, out, 1080, 1920)
    assert all(v % 2 == 0 for v in (x, y, w, h))
    im = Image.open(out)
    assert im.size == (1080, 1920)
    assert im.getpixel((x + w // 2, y + h // 2))[3] == 0
    assert im.getpixel((540, 100))[3] == 255
    assert im.getpixel((540, y + h + 50))[3] == 255


def test_unknown_project_is_none():
    assert projects.get("") is None
    assert projects.get("khong-co") is None


def test_frame_chain_puts_video_into_the_hole():
    chain = frame_stage.build_frame_chain((0, 546, 1080, 824), 1)
    assert "pad=1080:1920:0:546" in chain
    assert chain.count("[v]") == 1
    assert "[fp][1:v]overlay=0:0" in chain


def test_lol_project_skips_dubbing(cfg_fixture):
    names = [s.name for s in stages_for(cfg_fixture, "lol")]
    assert names == ["fetch", "precheck", "meta", "frame", "policy", "export"]
    assert "tts" in [s.name for s in stages_for(cfg_fixture)]


def test_lol_job_renders_into_vertical_frame_keeping_source_audio(
    tmp_path: Path, sample_video: Path, cfg_fixture
):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.update_settings(project="lol")
    job.source_video.write_bytes(sample_video.read_bytes())

    frame_stage.run(job, cfg_fixture)

    info = probe(job.final_mp4)
    assert (info.width, info.height) == (1080, 1920)
    assert info.has_audio == probe(job.source_video).has_audio


def test_export_works_without_translation(tmp_path: Path, cfg_fixture):
    from dataclasses import replace

    from reup.stages import export

    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.final_mp4.write_bytes(b"x")

    class LLM:
        def complete_json(self, prompt, schema):
            assert "giữ tiếng gốc" in prompt
            return {"title": "Pha xử lý", "description": "d", "hashtags": ["lol"]}

    cfg = replace(cfg_fixture, review=replace(cfg_fixture.review, output_dir=str(tmp_path / "out")))
    export.run_with(job, cfg, LLM())
    assert job.meta_json.exists()
