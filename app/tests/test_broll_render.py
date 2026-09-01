"""P2 end-to-end proof on synthetic media: a B-roll overlay actually
composites over the primary picture while the primary audio continues, and
both timeline exports carry the V2 track.

Media is generated with ffmpeg lavfi sources (red primary, blue B-roll), so
the overlay can be verified by sampling real pixels from the render.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

PIPELINE = Path(__file__).resolve().parents[1] / "pipeline"


def _make_clip(path: Path, color: str, seconds: float, tone_hz: int) -> None:
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c={color}:size=320x240:rate=30",
            "-f", "lavfi", "-i", f"sine=frequency={tone_hz}:sample_rate=48000",
            "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
        ],
        check=True,
    )


def _center_pixel(video: Path, at_seconds: float) -> tuple[int, int, int]:
    raw = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-ss", str(at_seconds), "-i", str(video), "-frames:v", "1",
            "-vf", "crop=8:8:(iw-8)/2:(ih-8)/2,scale=1:1",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        check=True, capture_output=True,
    ).stdout
    return raw[0], raw[1], raw[2]


def _event(event_id, asset_id, src_start, timeline_start, duration, intent):
    return {
        "event_id": event_id, "asset_id": asset_id,
        "source_start_seconds": src_start,
        "source_end_seconds": round(src_start + duration, 6),
        "timeline_start_seconds": timeline_start,
        "duration_seconds": duration, "playback_rate": 1.0,
        "intent": intent, "observed_content": None, "confidence": 0.9,
        "reframe": None, "transition_out": None, "text": None,
        "volume_db": None,
    }


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    root = tmp_path_factory.mktemp("broll")
    _make_clip(root / "red.mp4", "red", 4.0, 440)
    _make_clip(root / "blue.mp4", "blue", 4.0, 880)
    inventory = {"assets": [
        {
            "asset_id": aid, "filename": f"{aid}.mp4",
            "source_path": f"{aid}.mp4", "duration_seconds": 4.0,
            "sha256": "0" * 64, "media_type": "video", "audio": {
                "sample_rate": 48000, "channels": 1,
            }, "video": {"width": 320, "height": 240},
        }
        for aid in ("red", "blue")
    ]}
    plan = {
        "schema_version": "edit-plan.v1",
        "generated_at": "2026-09-01T00:00:00Z",
        "benchmark_id": "test", "concept_id": "test", "revision": 1,
        "project": {
            "width": 320, "height": 240, "fps": 30,
            "duration_seconds": 4.0, "background_color": "black",
        },
        "tracks": [
            {"track_id": "v1", "kind": "video", "events": [
                _event("v01", "red", 0.0, 0.0, 2.0, "base"),
                _event("v02", "red", 2.0, 2.0, 2.0, "base"),
            ]},
            {"track_id": "a1", "kind": "audio", "events": [
                _event("a01", "red", 0.0, 0.0, 2.0, "base"),
                _event("a02", "red", 2.0, 2.0, 2.0, "base"),
            ]},
            {"track_id": "t1", "kind": "title", "events": []},
            {"track_id": "v2", "kind": "video", "role": "broll", "events": [
                _event("bro-01", "blue", 0.5, 1.0, 2.0, "b-roll"),
            ]},
        ],
    }
    (root / "plan.json").write_text(json.dumps(plan))
    (root / "inventory.json").write_text(json.dumps(inventory))
    output = root / "review.mp4"
    subprocess.run(
        [
            sys.executable, str(PIPELINE / "render_edit.py"),
            "--plan", str(root / "plan.json"), "--output", str(output),
            "--inventory", str(root / "inventory.json"),
            "--media-root", str(root),
        ],
        check=True, capture_output=True, text=True,
    )
    return root, output


class TestBrollRender:
    def test_overlay_shows_broll_pixels_inside_its_window(self, rendered) -> None:
        _, output = rendered
        before = _center_pixel(output, 0.5)   # primary only
        during = _center_pixel(output, 2.0)   # overlay active (1.0-3.0s)
        after = _center_pixel(output, 3.5)    # primary again
        assert before[0] > 150 and before[2] < 100, f"expected red, got {before}"
        assert during[2] > 150 and during[0] < 100, f"expected blue, got {during}"
        assert after[0] > 150 and after[2] < 100, f"expected red, got {after}"

    def test_render_duration_matches_plan(self, rendered) -> None:
        _, output = rendered
        probed = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(output)],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        assert abs(float(probed) - 4.0) < 0.2

    def test_exports_carry_v2_track(self, rendered) -> None:
        root, _ = rendered
        result = subprocess.run(
            [
                sys.executable, str(PIPELINE / "export_timelines.py"),
                "--plan", str(root / "plan.json"),
                "--inventory", str(root / "inventory.json"),
                "--media-root", str(root), "--output-dir", str(root / "out"),
                "--basename", "timeline", "--name", "broll-test",
            ],
            check=True, capture_output=True, text=True,
        )
        report = json.loads(result.stdout)
        assert report["valid"], report
        xml = (root / "out" / "timeline-davinci.xml").read_text()
        assert xml.count("<track>") >= 3 and "bro-01" in xml


def _band_volume(video: Path, start: float, end: float, band: str) -> float:
    """Mean volume of a time slice after a frequency filter, in dB."""
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-ss", str(start), "-to", str(end),
            "-i", str(video), "-af", f"{band},volumedetect", "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    for line in result.stderr.splitlines():
        if "mean_volume" in line:
            return float(line.split("mean_volume:")[1].split(" dB")[0])
    raise AssertionError(result.stderr[-500:])


class TestJCutRender:
    def test_audio_leads_the_picture(self, tmp_path) -> None:
        """J-cut: the second scene's audio starts 0.4s before its picture."""  # 300Hz vs 1200Hz tones
        _make_clip(tmp_path / "red.mp4", "red", 4.0, 300)
        _make_clip(tmp_path / "blue.mp4", "blue", 4.0, 1200)
        inventory = {"assets": [
            {
                "asset_id": aid, "filename": f"{aid}.mp4",
                "source_path": f"{aid}.mp4", "duration_seconds": 4.0,
                "sha256": "0" * 64, "media_type": "video", "audio": {
                    "sample_rate": 48000, "channels": 1,
                }, "video": {"width": 320, "height": 240},
            }
            for aid in ("red", "blue")
        ]}
        plan = {
            "schema_version": "edit-plan.v1",
            "generated_at": "2026-09-01T00:00:00Z",
            "benchmark_id": "test", "concept_id": "test", "revision": 1,
            "project": {
                "width": 320, "height": 240, "fps": 30,
                "duration_seconds": 4.0, "background_color": "black",
            },
            "tracks": [
                {"track_id": "v1", "kind": "video", "events": [
                    _event("v01", "red", 0.0, 0.0, 2.0, "base"),
                    _event("v02", "blue", 0.0, 2.0, 2.0, "base"),
                ]},
                {"track_id": "a1", "kind": "audio", "events": [
                    _event("a01", "red", 0.0, 0.0, 1.6, "base"),
                    _event("a02", "blue", 0.0, 1.6, 2.4, "base"),
                ]},
                {"track_id": "t1", "kind": "title", "events": []},
            ],
        }
        (tmp_path / "plan.json").write_text(json.dumps(plan))
        (tmp_path / "inventory.json").write_text(json.dumps(inventory))
        output = tmp_path / "review.mp4"
        subprocess.run(
            [
                sys.executable, str(PIPELINE / "render_edit.py"),
                "--plan", str(tmp_path / "plan.json"), "--output", str(output),
                "--inventory", str(tmp_path / "inventory.json"),
                "--media-root", str(tmp_path),
            ],
            check=True, capture_output=True, text=True,
        )
        # picture is still red at 1.8s...
        pixel = _center_pixel(output, 1.8)
        assert pixel[0] > 150 and pixel[2] < 100, f"expected red, got {pixel}"
        # ...but the 1200Hz tone is already playing in the 1.7-1.9s window
        high = _band_volume(output, 1.7, 1.9, "highpass=f=600,highpass=f=600")
        low = _band_volume(output, 1.7, 1.9, "lowpass=f=600,lowpass=f=600")
        assert high > low + 10, f"1200Hz should dominate: high={high} low={low}"
        # and before the J point the 300Hz tone dominates
        high = _band_volume(output, 1.2, 1.4, "highpass=f=600,highpass=f=600")
        low = _band_volume(output, 1.2, 1.4, "lowpass=f=600,lowpass=f=600")
        assert low > high + 10, f"300Hz should dominate: high={high} low={low}"


class TestP5Polish:
    def test_cut_edges_carry_click_fades(self, rendered) -> None:
        _, output = rendered
        manifest = json.loads(
            output.with_suffix(".render-command.json").read_text()
        )
        joined = " ".join(manifest["command"])
        assert "afade=t=in" in joined and "afade=t=out" in joined

    def test_burned_captions_appear_in_lower_third(self, rendered) -> None:
        root, _ = rendered
        (root / "subs.srt").write_text(
            "1\n00:00:00,200 --> 00:00:03,800\nHOLA HOLA HOLA\n\n"
        )
        output = root / "subtitled.mp4"
        subprocess.run(
            [
                sys.executable, str(PIPELINE / "render_edit.py"),
                "--plan", str(root / "plan.json"), "--output", str(output),
                "--inventory", str(root / "inventory.json"),
                "--media-root", str(root),
                "--captions", str(root / "subs.srt"),
            ],
            check=True, capture_output=True, text=True,
        )

        def lower_third_peak_green(video):
            raw = subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-ss", "0.5", "-i", str(video), "-frames:v", "1",
                    "-vf", "crop=iw:60:0:ih-80",
                    "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
                ],
                check=True, capture_output=True,
            ).stdout
            return max(raw[1::3])  # brightest green byte — red bg has ~0

        plain = lower_third_peak_green(root / "review.mp4")
        subtitled = lower_third_peak_green(output)
        assert subtitled > plain + 100, (
            f"white caption should light up the lower third: "
            f"{plain} -> {subtitled}"
        )

    def test_fill_reframe_crops_toward_center(self, tmp_path) -> None:
        # 640x240 source: left half red, right half blue
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi",
                "-i", "color=c=red:size=320x240:rate=30",
                "-f", "lavfi",
                "-i", "color=c=blue:size=320x240:rate=30",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                "-filter_complex", "[0:v][1:v]hstack[v]",
                "-map", "[v]", "-map", "2:a", "-t", "2",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-c:a", "aac",
                str(tmp_path / "wide.mp4"),
            ],
            check=True,
        )
        inventory = {"assets": [{
            "asset_id": "wide", "filename": "wide.mp4",
            "source_path": "wide.mp4", "duration_seconds": 2.0,
            "sha256": "0" * 64, "media_type": "video",
            "audio": {"sample_rate": 48000, "channels": 1},
            "video": {"width": 640, "height": 240},
        }]}
        for center_x, expect in ((0.0, "red"), (1.0, "blue")):
            event = _event("v01", "wide", 0.0, 0.0, 2.0, "base")
            event["reframe"] = {
                "mode": "fill", "center_x": center_x, "center_y": 0.5,
                "scale": 1.0, "manual_review": False,
            }
            audio = _event("a01", "wide", 0.0, 0.0, 2.0, "base")
            plan = {
                "schema_version": "edit-plan.v1",
                "generated_at": "2026-09-01T00:00:00Z",
                "benchmark_id": "t", "concept_id": "t", "revision": 1,
                "project": {"width": 240, "height": 240, "fps": 30,
                            "duration_seconds": 2.0,
                            "background_color": "black"},
                "tracks": [
                    {"track_id": "v1", "kind": "video", "events": [event]},
                    {"track_id": "a1", "kind": "audio", "events": [audio]},
                    {"track_id": "t1", "kind": "title", "events": []},
                ],
            }
            (tmp_path / "plan.json").write_text(json.dumps(plan))
            (tmp_path / "inventory.json").write_text(json.dumps(inventory))
            output = tmp_path / f"fill-{center_x}.mp4"
            subprocess.run(
                [
                    sys.executable, str(PIPELINE / "render_edit.py"),
                    "--plan", str(tmp_path / "plan.json"),
                    "--output", str(output),
                    "--inventory", str(tmp_path / "inventory.json"),
                    "--media-root", str(tmp_path),
                ],
                check=True, capture_output=True, text=True,
            )
            pixel = _center_pixel(output, 1.0)
            if expect == "red":
                assert pixel[0] > 150 and pixel[2] < 100, pixel
            else:
                assert pixel[2] > 150 and pixel[0] < 100, pixel
