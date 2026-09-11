from pathlib import Path

from reup.core.job import create_job
from reup.media.ffmpeg import probe
from reup.stages import demux as demux_stage


def test_demux_writes_both_audio_files(tmp_path: Path, sample_video: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.source_video.write_bytes(sample_video.read_bytes())

    demux_stage.run(job, cfg_fixture)

    assert job.full_16k.exists()
    assert job.full_48k.exists()
    assert abs(probe(job.full_16k).duration_ms - 6000) <= 100
    assert abs(probe(job.full_48k).duration_ms - 6000) <= 100


def test_spec_declares_its_artifacts():
    assert demux_stage.SPEC.name == "demux"
    assert set(demux_stage.SPEC.produces) == {
        "audio/full_16k.wav", "audio/full_48k.wav",
    }
