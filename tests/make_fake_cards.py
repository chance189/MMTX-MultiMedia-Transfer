"""Build fake camera cards with realistic folder layouts for testing.

    python tests/make_fake_cards.py <out_dir> [--big]

Creates <out_dir>/GOPRO, DJI, INSTA360, FX3 (each looks like a mounted card).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime


def _write(path: str, size: int, when: datetime, seed: int, trailer: bytes = b"") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rnd = (seed.to_bytes(4, "little") * 64)
    block = bytes((b * 31 + i) & 0xFF for i, b in enumerate(rnd * 64))  # 16 KiB pattern
    with open(path, "wb") as f:
        left = size
        i = 0
        while left > 0:
            chunk = block[: min(len(block), left)]
            f.write(bytes([i & 0xFF]) + chunk[1:])
            left -= len(chunk)
            i += 1
        f.write(trailer)
    ts = when.timestamp()
    os.utime(path, (ts, ts))


def make_cards(out: str, big: bool = False) -> dict[str, str]:
    MB = 1_000_000
    d13 = datetime(2026, 11, 13, 6, 30)
    d14 = datetime(2026, 11, 14, 6, 15)
    cards = {}

    # ---- GoPro HERO12 Black
    g = os.path.join(out, "GOPRO")
    cards["gopro"] = g
    os.makedirs(os.path.join(g, "MISC"), exist_ok=True)
    with open(os.path.join(g, "MISC", "version.txt"), "w") as f:
        f.write('{\n"info version":"2.0",\n"firmware version":"H23.01.02.32.00",\n"camera type":"HERO12 Black",\n}\n')
    dc = os.path.join(g, "DCIM", "100GOPRO")
    _write(os.path.join(dc, "GX010001.MP4"), (40 if big else 12) * MB, d13, 1)
    _write(os.path.join(dc, "GX020001.MP4"), 6 * MB, d13.replace(minute=45), 2)
    _write(os.path.join(dc, "GL010001.LRV"), 1 * MB, d13, 3)
    _write(os.path.join(dc, "GX010001.THM"), 20_000, d13, 4)
    # clip recorded across midnight: main clip on the 13th, thumbnail stamped the 14th
    _write(os.path.join(dc, "GX010002.MP4"), 3 * MB, datetime(2026, 11, 13, 23, 59, 50), 5)
    _write(os.path.join(dc, "GX010002.THM"), 20_000, datetime(2026, 11, 14, 0, 0, 5), 6)
    _write(os.path.join(dc, "GX010003.MP4"), 9 * MB, d14, 7)
    _write(os.path.join(dc, "GL010003.LRV"), 1 * MB, d14, 8)
    _write(os.path.join(dc, "GOPR0004.JPG"), 4 * MB, d14.replace(minute=30), 9)
    with open(os.path.join(g, "Get_started_with_GoPro.url"), "w") as f:
        f.write("[InternetShortcut]\n")

    # ---- DJI drone
    d = os.path.join(out, "DJI")
    cards["dji"] = d
    dc = os.path.join(d, "DCIM", "DJI_001")
    for stem, when, size, seed in [
        ("DJI_20261113071200_0001_D", d13, 8 * MB, 11),
        ("DJI_20261114063012_0002_D", d14, 7 * MB, 12),
    ]:
        _write(os.path.join(dc, stem + ".MP4"), size, when, seed)
        _write(os.path.join(dc, stem + ".LRF"), 1 * MB, when, seed + 50)
        _write(os.path.join(dc, stem + ".SRT"), 30_000, when, seed + 60)
    _write(os.path.join(dc, "DJI_20261114063500_0003_D.JPG"), 3 * MB, d14, 13)
    os.makedirs(os.path.join(d, "MISC", "LOG"), exist_ok=True)
    with open(os.path.join(d, "MISC", "LOG", "flight.log"), "w") as f:
        f.write("not media")

    # ---- Insta360 X4
    i = os.path.join(out, "INSTA360")
    cards["insta360"] = i
    dc = os.path.join(i, "DCIM", "Camera01")
    trailer = b"\x00" * 100 + b"\x12\x0bInsta360 X4\x1a\x08IXSE1234" + b"\x00" * 100
    _write(os.path.join(dc, "VID_20261114_062000_00_001.insv"), 10 * MB, d14, 21, trailer)
    _write(os.path.join(dc, "LRV_20261114_062000_01_001.lrv"), 1 * MB, d14, 22)
    _write(os.path.join(dc, "IMG_20261112_180000_00_002.insp"), 5 * MB, datetime(2026, 11, 12, 18), 23)
    with open(os.path.join(i, "DCIM", "fileinfo_list.list"), "wb") as f:
        f.write(b"\x01\x02")

    # ---- Sony FX3
    s = os.path.join(out, "FX3")
    cards["sony"] = s
    m4 = os.path.join(s, "PRIVATE", "M4ROOT")
    for clip, iso, seed in [("C0001", "2026-11-14T06:40:00-05:00", 31), ("C0002", "2026-11-13T19:05:00-05:00", 32)]:
        dt = datetime.fromisoformat(iso).replace(tzinfo=None)
        # file mtime deliberately wrong (camera clock / FS quirks): XML date must win
        _write(os.path.join(m4, "CLIP", clip + ".MP4"), 11 * MB, datetime(2026, 1, 1), seed)
        xml = os.path.join(m4, "CLIP", clip + "M01.XML")
        with open(xml, "w") as f:
            f.write(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<NonRealTimeMeta xmlns="urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.20">\n'
                f'\t<Duration value="1500"/>\n\t<CreationDate value="{iso}"/>\n'
                '\t<Device manufacturer="Sony" modelName="ILME-FX3" serialNo="1234567"/>\n'
                "</NonRealTimeMeta>\n"
            )
        os.utime(xml, (dt.timestamp(), dt.timestamp()))
        _write(os.path.join(m4, "SUB", clip + "S03.MP4"), 1 * MB, dt, seed + 50)
        _write(os.path.join(m4, "THMBNL", clip + "T01.JPG"), 50_000, dt, seed + 60)
    os.makedirs(os.path.join(m4, "GENERAL", "SONY"), exist_ok=True)
    for junk in ("MEDIAPRO.XML", "STATUS.BIN"):
        with open(os.path.join(m4, junk), "w") as f:
            f.write("x")
    _write(os.path.join(s, "DCIM", "100MSDCF", "DSC00001.ARW"), 2 * MB, d14.replace(hour=7), 33)
    return cards


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "fake_cards"
    for k, v in make_cards(out, "--big" in sys.argv).items():
        print(k, v)
