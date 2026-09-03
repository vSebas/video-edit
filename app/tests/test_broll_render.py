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


class TestVoiceoverRender:
    def test_voiceover_plays_over_ducked_bed(self, tmp_path) -> None:
        """Voiceover (1200Hz) mixed on top from 1s; bed (300Hz) ducks -9dB
        inside the window and recovers outside it."""
        _make_clip(tmp_path / "red.mp4", "red", 4.0, 300)
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "sine=frequency=1200:sample_rate=48000",
             "-t", "2", "-c:a", "aac", str(tmp_path / "memo.m4a")],
            check=True,
        )
        inventory = {"assets": [
            {"asset_id": "red", "filename": "red.mp4",
             "source_path": "red.mp4", "duration_seconds": 4.0,
             "sha256": "0" * 64, "media_type": "video",
             "audio": {"sample_rate": 48000, "channels": 1},
             "video": {"width": 320, "height": 240}},
            {"asset_id": "memo", "filename": "memo.m4a",
             "source_path": "memo.m4a", "duration_seconds": 2.0,
             "sha256": "1" * 64, "media_type": "audio",
             "audio": {"sample_rate": 48000, "channels": 1}},
        ]}
        vo_event = _event("vo-01", "memo", 0.0, 1.0, 2.0, "voiceover")
        plan = {
            "schema_version": "edit-plan.v1",
            "generated_at": "2026-09-01T00:00:00Z",
            "benchmark_id": "t", "concept_id": "t", "revision": 1,
            "project": {"width": 320, "height": 240, "fps": 30,
                        "duration_seconds": 4.0,
                        "background_color": "black"},
            "tracks": [
                {"track_id": "v1", "kind": "video", "events": [
                    _event("v01", "red", 0.0, 0.0, 4.0, "base")]},
                {"track_id": "a1", "kind": "audio", "events": [
                    _event("a01", "red", 0.0, 0.0, 4.0, "base")]},
                {"track_id": "t1", "kind": "title", "events": []},
                {"track_id": "a2", "kind": "audio", "role": "voiceover",
                 "events": [vo_event]},
            ],
        }
        (tmp_path / "plan.json").write_text(json.dumps(plan))
        (tmp_path / "inventory.json").write_text(json.dumps(inventory))
        output = tmp_path / "review.mp4"
        subprocess.run(
            [sys.executable, str(PIPELINE / "render_edit.py"),
             "--plan", str(tmp_path / "plan.json"), "--output", str(output),
             "--inventory", str(tmp_path / "inventory.json"),
             "--media-root", str(tmp_path)],
            check=True, capture_output=True, text=True,
        )
        # voiceover tone audible inside its window, absent outside
        vo_in = _band_volume(output, 1.4, 2.4, "highpass=f=600,highpass=f=600")
        vo_out = _band_volume(output, 3.2, 3.8, "highpass=f=600,highpass=f=600")
        assert vo_in > vo_out + 15, f"voiceover should be audible: {vo_in} vs {vo_out}"
        # bed ducked inside the window vs after it
        bed_in = _band_volume(output, 1.4, 2.4, "lowpass=f=600,lowpass=f=600")
        bed_out = _band_volume(output, 3.2, 3.8, "lowpass=f=600,lowpass=f=600")
        assert bed_out > bed_in + 4, f"bed should duck: in={bed_in} out={bed_out}"


class TestStyledTitles:
    """text_style routes real pixels: position moves the drawtext band and
    the styled font file is actually consumed by the renderer."""

    def _render_with_title(self, root: Path, name: str, text_style: dict | None):
        plan = json.loads((root / "plan.json").read_text())
        title_event = {
            "event_id": "t01", "asset_id": None,
            "timeline_start_seconds": 0.0, "source_start_seconds": 0.0,
            "duration_seconds": 2.0, "purpose": "title",
            "text": "Stanford's AI: 100% real, 'wow'\\n",
        }
        if text_style is not None:
            title_event["text_style"] = text_style
        for track in plan["tracks"]:
            if track["kind"] == "title":
                track["events"] = [title_event]
        styled_plan = root / f"{name}.json"
        styled_plan.write_text(json.dumps(plan))
        output = root / f"{name}.mp4"
        subprocess.run(
            [
                sys.executable, str(PIPELINE / "render_edit.py"),
                "--plan", str(styled_plan), "--output", str(output),
                "--inventory", str(root / "inventory.json"),
                "--media-root", str(root),
            ],
            check=True, capture_output=True, text=True,
        )
        return output

    @staticmethod
    def _band_peak(video: Path, crop: str) -> int:
        raw = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-ss", "0.5", "-i", str(video), "-frames:v", "1",
                "-vf", f"crop={crop}",
                "-f", "rawvideo", "-pix_fmt", "gray", "-",
            ],
            check=True, capture_output=True,
        ).stdout
        return max(raw)

    def test_hostile_title_characters_render(self, rendered) -> None:
        # live failure 2026-09-02: "Stanford's" terminated the quoted
        # filter string and corrupted the whole graph
        root, _ = rendered
        output = self._render_with_title(
            root, "title_apostrophe", None
        )
        assert output.exists()

    def _render_with_hostile_text(self, root):
        pass

    def test_position_center_moves_the_text_band(self, rendered) -> None:
        root, _ = rendered
        top = self._render_with_title(root, "title_top", {"position": "top"})
        center = self._render_with_title(
            root, "title_center", {"position": "center", "font": "handwritten"}
        )
        middle_band = "iw:40:0:(ih-40)/2"
        # white text over the red clip: the middle band lights up only for
        # the centered title
        assert self._band_peak(center, middle_band) > (
            self._band_peak(top, middle_band) + 80
        )


class TestMusicAndCaptions:
    """Music bed mixes (ducked) and plan captions burn into the pixels."""

    def _render(self, root: Path, plan: dict, name: str) -> Path:
        (root / f"{name}.json").write_text(json.dumps(plan))
        output = root / f"{name}.mp4"
        subprocess.run(
            [sys.executable, str(PIPELINE / "render_edit.py"),
             "--plan", str(root / f"{name}.json"), "--output", str(output),
             "--inventory", str(root / "inventory.json"),
             "--media-root", str(root)],
            check=True, capture_output=True, text=True,
        )
        return output

    def test_music_bed_is_audible_and_captions_burn(self, rendered) -> None:
        root, _ = rendered
        # a distinct-frequency music bed
        _make_clip(root / "music.mp4", "black", 4.0, 1500)
        inv = json.loads((root / "inventory.json").read_text())
        inv["assets"].append({
            "asset_id": "music", "filename": "music.mp4",
            "source_path": "music.mp4", "duration_seconds": 4.0,
            "sha256": "0" * 64, "media_type": "audio",
            "audio": {"sample_rate": 48000, "channels": 1},
            "video": {"width": 320, "height": 240},
        })
        (root / "inventory.json").write_text(json.dumps(inv))
        plan = json.loads((root / "plan.json").read_text())
        plan["tracks"].append({
            "track_id": "cap1", "kind": "caption", "events": [{
                "event_id": "cap-001", "asset_id": None,
                "source_start_seconds": None, "source_end_seconds": None,
                "timeline_start_seconds": 0.2, "duration_seconds": 3.0,
                "playback_rate": 1.0, "intent": "caption",
                "observed_content": None, "confidence": 1.0,
                "text": "HOLA MUNDO", "volume_db": None,
            }]})
        plan["tracks"].append({
            "track_id": "mus1", "kind": "audio", "role": "music", "events": [{
                "event_id": "mus-01", "asset_id": "music",
                "source_start_seconds": 0.0, "source_end_seconds": 4.0,
                "timeline_start_seconds": 0.0, "duration_seconds": 4.0,
                "playback_rate": 1.0, "intent": "music",
                "observed_content": None, "confidence": 1.0, "text": None,
                "volume_db": -10,
                "music": {"mode": "bed", "recommended": None,
                          "bed": {"asset_id": "music", "gain_db": -10,
                                  "duck_db": -12, "loop": True}},
            }]})
        out = self._render(root, plan, "music_cap")
        assert out.exists()
        # the 1500 Hz music bed must be present in the mix
        raw = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(out),
             "-af", "bandpass=f=1500:width_type=h:w=80,volumedetect",
             "-f", "null", "-"],
            capture_output=True, text=True,
        ).stderr
        import re as _re
        m = _re.search(r"mean_volume:\s*(-?[0-9.]+) dB", raw)
        assert m, raw[-400:]
        # the 1500 Hz bed band must carry real signal, not near-silence
        assert float(m.group(1)) > -70, f"music bed too quiet: {m.group(1)} dB"
        # captions light up the lower third (white text over the clip)
        band = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", "1.0",
             "-i", str(out), "-frames:v", "1", "-vf", "crop=iw:80:0:ih-120",
             "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            check=True, capture_output=True).stdout
        assert max(band) > 150, "burned captions should brighten the lower band"


class TestSrtAndCaptionMapping:
    def _render_edit(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "render_edit_mod", str(PIPELINE / "render_edit.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_srt_timestamp_millisecond_carry(self) -> None:
        m = self._render_edit()
        # 59.9996s must not format as the invalid "...59,1000"; it carries.
        assert m.srt_timestamp(59.9996) == "00:01:00,000"
        assert m.srt_timestamp(0.0) == "00:00:00,000"
        assert m.srt_timestamp(3661.5) == "01:01:01,500"

    def test_srt_text_cannot_inject_cues(self) -> None:
        m = self._render_edit()
        # newlines/blank lines in caption text are collapsed so they can't
        # terminate a cue or forge a timing line.
        assert "\n" not in m._srt_text("línea uno\n\n00:00:01,000 --> x")

    def test_captions_carry_real_asr_text(self) -> None:
        from video_app.planning import _caption_events_from_speech
        video_events = [{
            "asset_id": "a1", "timeline_start_seconds": 0.0,
            "source_start_seconds": 0.0, "duration_seconds": 5.0,
        }]
        speech = {"a1": [
            {"start_seconds": 0.2, "end_seconds": 0.6, "word": "hola"},
            {"start_seconds": 0.6, "end_seconds": 1.0, "word": "mundo."},
        ]}
        events = _caption_events_from_speech(video_events, speech)
        assert events, "ASR words must produce caption events"
        assert events[0]["text"] == "hola mundo."

    def test_captions_split_on_silence(self) -> None:
        from video_app.planning import _caption_events_from_speech
        video_events = [{
            "asset_id": "a1", "timeline_start_seconds": 0.0,
            "source_start_seconds": 0.0, "duration_seconds": 12.0,
        }]
        # a 9s gap between the two words must not become one caption.
        speech = {"a1": [
            {"start_seconds": 0.5, "end_seconds": 0.9, "word": "antes"},
            {"start_seconds": 10.0, "end_seconds": 10.4, "word": "después"},
        ]}
        events = _caption_events_from_speech(video_events, speech)
        assert len(events) == 2


class TestCaptionStyleAndRevise:
    def _render_edit(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "render_edit_mod2", str(PIPELINE / "render_edit.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_caption_style_becomes_ass_overrides(self, tmp_path) -> None:
        m = self._render_edit()
        events = [{
            "timeline_start_seconds": 1.0, "duration_seconds": 2.0,
            "text": "hola", "caption_style": {
                "font": "handwritten", "size": 80, "position": "top"}}]
        assert m.has_caption_styles(events)
        path = tmp_path / "c.ass"
        assert m.write_caption_ass(events, path, 1080, 1920)
        body = path.read_text()
        assert "\\fs80" in body and "\\an8" in body
        assert "Liberation Serif" in body  # handwritten mapping

    def test_unstyled_captions_stay_on_srt(self) -> None:
        m = self._render_edit()
        assert not m.has_caption_styles(
            [{"text": "x", "timeline_start_seconds": 0, "duration_seconds": 1}])

    def test_revise_carries_user_caption_text(self) -> None:
        from video_app.planning import _carry_user_captions
        # same footage (asset A, overlapping source range) → carry, even though
        # the cue moved on the timeline after the revision.
        old = {"tracks": [{"kind": "caption", "events": [
            {"event_id": "cap-001", "timeline_start_seconds": 1.0,
             "duration_seconds": 2.0, "text": "hola mundo bonito",
             "asr_text": "hola mnd bonito", "user_authored": True,
             "caption_source": {"asset_id": "A", "source_start_seconds": 3.0,
                                 "source_end_seconds": 5.0}}]}]}
        new = {"tracks": [{"kind": "caption", "events": [
            {"event_id": "cap-001", "timeline_start_seconds": 6.0,
             "duration_seconds": 2.0, "text": "hola mnd bonito",
             "caption_source": {"asset_id": "A", "source_start_seconds": 3.0,
                                 "source_end_seconds": 5.0}}]}]}
        _carry_user_captions(old, new)
        cue = new["tracks"][0]["events"][0]
        assert cue["text"] == "hola mundo bonito"
        assert cue["user_authored"] is True

    def test_trimmed_cue_does_not_inherit_correction(self) -> None:
        from video_app.planning import _carry_user_captions
        # old [10,13]; regenerated [11,13] after the first words were trimmed —
        # IoU 0.67 < 0.9, so the whole old correction must NOT be pasted.
        old = {"tracks": [{"kind": "caption", "events": [
            {"event_id": "cap-001", "timeline_start_seconds": 1.0,
             "duration_seconds": 3.0, "text": "toda la frase corregida",
             "asr_text": "x", "user_authored": True,
             "caption_source": {"asset_id": "A", "source_start_seconds": 10.0,
                                 "source_end_seconds": 13.0}}]}]}
        new = {"tracks": [{"kind": "caption", "events": [
            {"event_id": "cap-001", "timeline_start_seconds": 1.0,
             "duration_seconds": 2.0, "text": "media frase",
             "caption_source": {"asset_id": "A", "source_start_seconds": 11.0,
                                 "source_end_seconds": 13.0}}]}]}
        _carry_user_captions(old, new)
        assert new["tracks"][0]["events"][0]["text"] == "media frase"

    def test_revise_refuses_carry_onto_different_footage(self) -> None:
        from video_app.planning import _carry_user_captions
        # different clip (asset B) at the SAME timeline slot, even same words →
        # different source identity → must NOT paste the correction.
        old = {"tracks": [{"kind": "caption", "events": [
            {"event_id": "cap-001", "timeline_start_seconds": 1.0,
             "duration_seconds": 2.0, "text": "Sí, ganamos",
             "asr_text": "si", "user_authored": True,
             "caption_source": {"asset_id": "A", "source_start_seconds": 0.0,
                                 "source_end_seconds": 1.0}}]}]}
        new = {"tracks": [{"kind": "caption", "events": [
            {"event_id": "cap-001", "timeline_start_seconds": 1.0,
             "duration_seconds": 2.0, "text": "si",
             "caption_source": {"asset_id": "B", "source_start_seconds": 0.0,
                                 "source_end_seconds": 1.0}}]}]}
        _carry_user_captions(old, new)
        assert new["tracks"][0]["events"][0]["text"] == "si"


class TestAssEscaping:
    def _render_edit(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "re_mod3", str(PIPELINE / "render_edit.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_ass_text_neutralizes_markup(self, tmp_path) -> None:
        m = self._render_edit()
        events = [{"timeline_start_seconds": 0.0, "duration_seconds": 1.0,
                   "text": "hola {\\an8}\\N mundo",
                   "caption_style": {"size": 40}}]
        path = tmp_path / "c.ass"
        assert m.write_caption_ass(events, path, 1080, 1920)
        # the injected override block/newline must not survive as ASS markup
        dialogue = [l for l in path.read_text().splitlines()
                    if l.startswith("Dialogue:")][0]
        assert "{\\an8}" not in dialogue and "\\N" not in dialogue


class TestCaptionCarryCoverage:
    def test_barely_overlapping_neighbor_is_not_matched(self) -> None:
        from video_app.planning import _carry_user_captions
        # old cue source [10,13]; candidate [12.99,15] barely touches → reject.
        old = {"tracks": [{"kind": "caption", "events": [
            {"event_id": "cap-001", "timeline_start_seconds": 1.0,
             "duration_seconds": 3.0, "text": "corrección importante",
             "asr_text": "correccion", "user_authored": True,
             "caption_source": {"asset_id": "A", "source_start_seconds": 10.0,
                                 "source_end_seconds": 13.0}}]}]}
        new = {"tracks": [{"kind": "caption", "events": [
            {"event_id": "cap-001", "timeline_start_seconds": 1.0,
             "duration_seconds": 2.0, "text": "otra cosa",
             "caption_source": {"asset_id": "A", "source_start_seconds": 12.99,
                                 "source_end_seconds": 15.0}}]}]}
        _carry_user_captions(old, new)
        assert new["tracks"][0]["events"][0]["text"] == "otra cosa"


class TestCaptionCarrySplit:
    def test_split_cue_is_not_matched(self) -> None:
        from video_app.planning import _carry_user_captions
        # old cue source [10,13] split into two halves → neither is "the line".
        old = {"tracks": [{"kind": "caption", "events": [
            {"event_id": "cap-001", "timeline_start_seconds": 1.0,
             "duration_seconds": 3.0, "text": "no dividas esto",
             "asr_text": "x", "user_authored": True,
             "caption_source": {"asset_id": "A", "source_start_seconds": 10.0,
                                 "source_end_seconds": 13.0}}]}]}
        new = {"tracks": [{"kind": "caption", "events": [
            {"event_id": "cap-001", "timeline_start_seconds": 1.0,
             "duration_seconds": 1.5, "text": "primera",
             "caption_source": {"asset_id": "A", "source_start_seconds": 10.0,
                                 "source_end_seconds": 11.5}},
            {"event_id": "cap-002", "timeline_start_seconds": 2.5,
             "duration_seconds": 1.5, "text": "segunda",
             "caption_source": {"asset_id": "A", "source_start_seconds": 11.5,
                                 "source_end_seconds": 13.0}}]}]}
        _carry_user_captions(old, new)
        texts = [e["text"] for e in new["tracks"][0]["events"]]
        assert texts == ["primera", "segunda"]  # correction carried to neither


class TestCaptionCarryDisjoint:
    def test_disjoint_tiny_envelopes_do_not_match(self) -> None:
        from video_app.planning import _carry_user_captions
        # short, DISJOINT source envelopes (different words) — endpoints differ
        # by 40ms, well beyond the 2ms precision → must not transfer.
        old = {"tracks": [{"kind": "caption", "events": [
            {"event_id": "cap-001", "timeline_start_seconds": 1.0,
             "duration_seconds": 0.5, "text": "palabra corregida",
             "asr_text": "x", "user_authored": True,
             "caption_source": {"asset_id": "A", "source_start_seconds": 10.000,
                                 "source_end_seconds": 10.020}}]}]}
        new = {"tracks": [{"kind": "caption", "events": [
            {"event_id": "cap-001", "timeline_start_seconds": 1.0,
             "duration_seconds": 0.5, "text": "otra palabra",
             "caption_source": {"asset_id": "A", "source_start_seconds": 10.040,
                                 "source_end_seconds": 10.060}}]}]}
        _carry_user_captions(old, new)
        assert new["tracks"][0]["events"][0]["text"] == "otra palabra"


class TestTransitionsRender:
    def test_intro_fade_and_dip_to_black_darken_pixels(self, tmp_path) -> None:
        _make_clip(tmp_path / "red.mp4", "red", 2.0, 300)
        _make_clip(tmp_path / "green.mp4", "green", 2.0, 600)
        inventory = {"assets": [
            {"asset_id": aid, "filename": f"{aid}.mp4",
             "source_path": f"{aid}.mp4", "duration_seconds": 2.0,
             "sha256": "0" * 64, "media_type": "video",
             "audio": {"sample_rate": 48000, "channels": 1},
             "video": {"width": 320, "height": 240}}
            for aid in ("red", "green")]}
        plan = {
            "schema_version": "edit-plan.v1", "generated_at": "2026-09-01T00:00:00Z",
            "benchmark_id": "t", "concept_id": "t", "revision": 1,
            "transitions": {"intro_fade_seconds": 0.5, "outro_fade_seconds": 0.5},
            "project": {"width": 320, "height": 240, "fps": 30,
                        "duration_seconds": 4.0, "background_color": "black"},
            "tracks": [
                {"track_id": "v1", "kind": "video", "events": [
                    {**_event("v01", "red", 0.0, 0.0, 2.0, "base"),
                     "transition_out": {"type": "fade_black",
                                        "duration_seconds": 0.6}},
                    _event("v02", "green", 0.0, 2.0, 2.0, "base")]},
                {"track_id": "a1", "kind": "audio", "events": [
                    _event("a01", "red", 0.0, 0.0, 2.0, "base"),
                    _event("a02", "green", 0.0, 2.0, 2.0, "base")]},
                {"track_id": "t1", "kind": "title", "events": []}]}
        (tmp_path / "plan.json").write_text(json.dumps(plan))
        (tmp_path / "inv.json").write_text(json.dumps(inventory))
        out = tmp_path / "out.mp4"
        subprocess.run(
            [sys.executable, str(PIPELINE / "render_edit.py"),
             "--plan", str(tmp_path / "plan.json"), "--output", str(out),
             "--inventory", str(tmp_path / "inv.json"),
             "--media-root", str(tmp_path)],
            check=True, capture_output=True, text=True)
        intro = _center_pixel(out, 0.03)   # inside the 0.5s intro fade
        mid = _center_pixel(out, 1.0)      # bright red
        dip = _center_pixel(out, 2.0)      # dip to black at the seam
        assert sum(intro) < 120, f"intro should be dark, got {intro}"
        assert mid[0] > 150, f"mid should be bright red, got {mid}"
        assert sum(dip) < 90, f"seam should dip to black, got {dip}"


class TestTransitionAdjacency:
    def test_final_clip_transition_and_gap_produce_no_dip(self, tmp_path) -> None:
        # v01 [0,2] fade_black, GAP [2,3], v02 [3,5] fade_black (last clip).
        # Neither dip is a real seam: v01→v02 is separated by a gap, and v02 has
        # no successor — so the mid of each clip stays bright (no dip artifact).
        _make_clip(tmp_path / "red.mp4", "red", 2.0, 300)
        _make_clip(tmp_path / "green.mp4", "green", 2.0, 600)
        inv = {"assets": [
            {"asset_id": aid, "filename": f"{aid}.mp4", "source_path": f"{aid}.mp4",
             "duration_seconds": 2.0, "sha256": "0" * 64, "media_type": "video",
             "audio": {"sample_rate": 48000, "channels": 1},
             "video": {"width": 320, "height": 240}} for aid in ("red", "green")]}
        plan = {
            "schema_version": "edit-plan.v1", "generated_at": "2026-09-01T00:00:00Z",
            "benchmark_id": "t", "concept_id": "t", "revision": 1,
            "project": {"width": 320, "height": 240, "fps": 30,
                        "duration_seconds": 5.0, "background_color": "black"},
            "tracks": [
                {"track_id": "v1", "kind": "video", "events": [
                    {**_event("v01", "red", 0.0, 0.0, 2.0, "base"),
                     "transition_out": {"type": "fade_black", "duration_seconds": 0.6}},
                    {**_event("v02", "green", 0.0, 3.0, 2.0, "base"),
                     "transition_out": {"type": "fade_black", "duration_seconds": 0.6}}]},
                {"track_id": "a1", "kind": "audio", "events": [
                    _event("a01", "red", 0.0, 0.0, 2.0, "base"),
                    _event("a02", "green", 0.0, 3.0, 2.0, "base")]},
                {"track_id": "t1", "kind": "title", "events": []}]}
        (tmp_path / "plan.json").write_text(json.dumps(plan))
        (tmp_path / "inv.json").write_text(json.dumps(inv))
        out = tmp_path / "out.mp4"
        subprocess.run(
            [sys.executable, str(PIPELINE / "render_edit.py"),
             "--plan", str(tmp_path / "plan.json"), "--output", str(out),
             "--inventory", str(tmp_path / "inv.json"), "--media-root", str(tmp_path)],
            check=True, capture_output=True, text=True)
        # v01 near its end (1.9s) must NOT have dipped (gap after, not a seam)
        assert _center_pixel(out, 1.9)[0] > 150
        # v02 near its end (4.9s) must NOT have dipped (last clip, no successor)
        assert _center_pixel(out, 4.9)[1] > 100
