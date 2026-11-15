"""Thumbnail/media helper tests against real encoded clips (needs ffmpeg)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mmtx.media import embedded_jpeg, find_ffmpeg, format_duration, mp4_duration, video_frame_jpeg  # noqa: E402


def make_clip(path: str, seconds: float = 3.0, size: str = "1280x720", codec: str = "libx264",
              src: str = "testsrc2", tag: str = "") -> bool:
    ff = find_ffmpeg()
    if not ff:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fmt = ["-f", "mp4"]
    cmd = [ff, "-y", "-v", "error", "-f", "lavfi", "-i", f"{src}=size={size}:rate=30:duration={seconds}",
           "-c:v", codec, "-preset", "ultrafast", "-g", "30", "-pix_fmt", "yuv420p"]
    if tag:
        cmd += ["-tag:v", tag]
    cmd += fmt + [path]
    return subprocess.run(cmd).returncode == 0


def test_duration_and_frames():
    tmp = tempfile.mkdtemp()
    try:
        clip = os.path.join(tmp, "GX010001.MP4")
        assert make_clip(clip, seconds=4.5)
        d = mp4_duration(clip)
        assert d is not None and abs(d - 4.5) < 0.1, d
        assert format_duration(d) in ("0:04", "0:05")
        jpg = video_frame_jpeg(clip, at=1.0, width=320)
        assert jpg and jpg[:2] == b"\xff\xd8"
        # an LRV is just an MP4 with another extension
        lrv = os.path.join(tmp, "GL010001.LRV")
        shutil.copy(clip, lrv)
        assert video_frame_jpeg(lrv, at=10.0)  # seeking past the end falls back to t=0
        # a 'free' box padding after moov (how the demo makes big files) keeps it parseable
        with open(clip, "ab") as f:
            f.write((8 + 1000).to_bytes(4, "big") + b"free" + b"\0" * 1000)
        assert abs(mp4_duration(clip) - 4.5) < 0.1
        # HEVC (GoPro / FX3 style) if this ffmpeg has an encoder for it
        hevc = os.path.join(tmp, "C0001.MP4")
        if make_clip(hevc, codec="libx265", tag="hvc1"):
            assert video_frame_jpeg(hevc)
    finally:
        shutil.rmtree(tmp)


def test_raw_embedded_preview():
    tmp = tempfile.mkdtemp()
    try:
        clip = os.path.join(tmp, "a.mp4")
        assert make_clip(clip)
        jpg = video_frame_jpeg(clip, width=1280)
        assert len(jpg) > 20_000
        raw = os.path.join(tmp, "DSC00001.ARW")
        with open(raw, "wb") as f:
            f.write(os.urandom(5000) + b"\xff\xd8\xff\xe0tiny\xff\xd9" + os.urandom(3000) + jpg + os.urandom(9000))
        got = embedded_jpeg(raw)
        assert got == jpg
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    for fn in (test_duration_and_frames, test_raw_embedded_preview):
        fn()
        print("PASS", fn.__name__)
