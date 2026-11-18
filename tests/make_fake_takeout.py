"""Build fake Google Photos takeouts (multi-part zips) with all of Takeout's naming quirks.

    python tests/make_fake_takeout.py <out_dir>

Used by tests/test_takeout.py and the GUI screenshots.
"""

from __future__ import annotations

import io
import json
import os
import struct
import sys
import zipfile
from datetime import datetime, timezone

GP = "Takeout/Google Photos"


def exif_app1(dt: str, offset: str | None = None) -> bytes:
    """An APP1 Exif segment with DateTimeOriginal (and OffsetTimeOriginal)."""
    dt_b = dt.encode() + b"\x00"  # 20 bytes
    off_b = (offset.encode() + b"\x00") if offset else b""
    n_ex = 2 if offset else 1
    ifd0_off = 8
    ifd0_len = 2 + 12 + 4
    ex_off = ifd0_off + ifd0_len
    ex_len = 2 + 12 * n_ex + 4
    data_off = ex_off + ex_len
    t = b"II*\x00" + struct.pack("<I", ifd0_off)
    t += struct.pack("<H", 1) + struct.pack("<HHII", 0x8769, 4, 1, ex_off) + struct.pack("<I", 0)
    ents = struct.pack("<HHII", 0x9003, 2, len(dt_b), data_off)
    if offset:
        ents += struct.pack("<HHII", 0x9011, 2, len(off_b), data_off + len(dt_b))
    t += struct.pack("<H", n_ex) + ents + struct.pack("<I", 0)
    t += dt_b + off_b
    body = b"Exif\x00\x00" + t
    return b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body


def jpeg(seed: int, exif: bytes = b"", size: int = 0) -> bytes:
    """A real, decodable JPEG (when Pillow is around) carrying a unique comment and optional Exif."""
    try:
        from PIL import Image, ImageDraw

        hue = (seed * 47) % 255
        im = Image.new("RGB", (320, 240), ((hue * 3) % 255, (hue * 7 + 60) % 255, (hue * 11 + 120) % 255))
        d = ImageDraw.Draw(im)
        for k in range(6):
            x = (seed * 37 + k * 53) % 300
            y = (seed * 23 + k * 41) % 220
            d.ellipse((x, y, x + 40 + k * 6, y + 30 + k * 4), fill=((x * 3) % 255, (y * 5) % 255, (k * 40) % 255))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80)
        raw = buf.getvalue()
    except ImportError:  # pragma: no cover
        raw = b"\xff\xd8" + bytes(range(256)) * 8 + b"\xff\xd9"
    com = b"seed %d" % seed
    com_seg = b"\xff\xfe" + struct.pack(">H", len(com) + 2) + com
    out = raw[:2] + exif + com_seg + raw[2:]
    if size and len(out) < size:  # pad after EOI, still a valid JPEG for viewers
        out += bytes((seed + i) & 0xFF for i in range(size - len(out)))
    return out


def heic(seed: int, exif: bytes = b"") -> bytes:
    """Not a real HEIC, but laid out like one far enough for the Exif search."""
    body = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"meta" + bytes(64)
    if exif:
        body += b"\x00\x00\x00\x06" + exif[4:]  # Exif item: offset, then "Exif\0\0" + TIFF
    return body + bytes((seed * 13 + i) & 0xFF for i in range(4000))


def video(seed: int, size: int = 30000) -> bytes:
    return b"\x00\x00\x00\x18ftypqt  " + bytes((seed * 7 + i * 3) & 0xFF for i in range(size))


def _box(typ: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body) + 8) + typ + body


def mov(seed: int, apple: str | None = None, mvhd_utc: int | None = None, payload: int = 20000) -> bytes:
    """A QuickTime file laid out like an iPhone video: ftyp, mdat, then moov at the end with mvhd
    (UTC, 1904 epoch) and Apple's com.apple.quicktime.creationdate (local time with zone)."""
    ftyp = _box(b"ftyp", b"qt  \x00\x00\x00\x00qt  ")
    mdat = _box(b"mdat", bytes((seed * 31 + i * 7) & 0xFF for i in range(payload)))
    secs = (mvhd_utc + 2082844800) if mvhd_utc else 0
    mvhd = _box(b"mvhd", b"\x00\x00\x00\x00" + struct.pack(">IIII", secs, secs, 600, 6000) + bytes(80))
    moov = mvhd
    if apple:
        key = b"com.apple.quicktime.creationdate"
        keys = _box(b"keys", b"\x00\x00\x00\x00" + struct.pack(">I", 1) + struct.pack(">I", len(key) + 8) + b"mdta" + key)
        data = _box(b"data", struct.pack(">II", 1, 0) + apple.encode())
        ilst = _box(b"ilst", struct.pack(">I", len(data) + 8) + struct.pack(">I", 1) + data)
        hdlr = _box(b"hdlr", bytes(8) + b"mdta" + bytes(13))
        moov += _box(b"meta", hdlr + keys + ilst)
    return ftyp + mdat + _box(b"moov", moov)


def sidecar(title: str, taken: int, created: int | None = None, desc: str = "", lat: float = 0.0,
            lon: float = 0.0, people=(), favorited: bool = False) -> bytes:
    def stamp(ts: int) -> dict:
        return {"timestamp": str(ts),
                "formatted": datetime.fromtimestamp(ts, timezone.utc).strftime("%b %d, %Y, %I:%M:%S %p UTC")}

    d = {
        "title": title,
        "description": desc,
        "imageViews": "8",
        "creationTime": stamp(created or taken + 3600),
        "photoTakenTime": stamp(taken),
        "geoData": {"latitude": lat, "longitude": lon, "altitude": 0.0, "latitudeSpan": 0.0, "longitudeSpan": 0.0},
        "url": f"https://photos.google.com/photo/AF1Qip{abs(hash(title)) % 10**12}",
        "googlePhotosOrigin": {"mobileUpload": {"deviceType": "IOS_PHONE"}},
    }
    if people:
        d["people"] = [{"name": p} for p in people]
    if favorited:
        d["favorited"] = True
    return json.dumps(d, indent=2).encode()


def album_meta(title: str) -> bytes:
    return json.dumps({"title": title, "description": "", "access": "protected",
                       "date": {"timestamp": "1700000000", "formatted": "Nov 14, 2023"}}).encode()


def write_zip(path: str, entries: list[tuple[str, bytes]]) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Takeout/archive_browser.html", b"<html>Takeout</html>")
        for name, data in entries:
            zf.writestr(name, data)
    return path


def ts(y, mo, d, h=12, mi=0, s=0) -> int:
    """UTC instant."""
    return int(datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).timestamp())


def make_takeout(out: str, n_extra: int = 0) -> dict:
    """Two-part export with every quirk. Returns {name: expected day folder} for checks."""
    yr = f"{GP}/Photos from 2026"
    y25 = f"{GP}/Photos from 2025"
    p1: list[tuple[str, bytes]] = []
    p2: list[tuple[str, bytes]] = []
    expect: dict[str, str] = {}

    # Chance's own sidecars (iPhone HEIC, supplemental-metadata, no location)
    for i, (name, taken) in enumerate([("IMG_6367.HEIC", 1769111724), ("IMG_6282.HEIC", 1768163469),
                                       ("IMG_6313.HEIC", 1768423137)]):
        p1.append((f"{yr}/{name}", heic(100 + i)))
        p2.append((f"{yr}/{name}.supplemental-metadata.json", sidecar(name, taken, desc="")))  # other part!
    expect["IMG_6367.HEIC"] = "2026/2026_01/2026_01_22"
    expect["IMG_6282.HEIC"] = "2026/2026_01/2026_01_11"
    expect["IMG_6313.HEIC"] = "2026/2026_01/2026_01_14"

    # duplicate numbering: old style and new style
    p1 += [(f"{y25}/IMG_0001.jpg", jpeg(1)), (f"{y25}/IMG_0001.jpg.json", sidecar("IMG_0001.jpg", ts(2025, 5, 1))),
           (f"{y25}/IMG_0001(1).jpg", jpeg(2)), (f"{y25}/IMG_0001.jpg(1).json", sidecar("IMG_0001.jpg", ts(2025, 5, 2)))]
    p1 += [(f"{y25}/IMG_0002(1).jpg", jpeg(3)),
           (f"{y25}/IMG_0002.jpg.supplemental-metadata(1).json", sidecar("IMG_0002.jpg", ts(2025, 5, 3)))]
    expect["IMG_0001.jpg"] = "2025/2025_05/2025_05_01"
    expect["IMG_0001(1).jpg"] = "2025/2025_05/2025_05_02"
    expect["IMG_0002(1).jpg"] = "2025/2025_05/2025_05_03"
    # supplemental-metadata cut at random lengths
    p1 += [(f"{y25}/IMG_0003.jpg", jpeg(4)),
           (f"{y25}/IMG_0003.jpg.supplemental-meta.json", sidecar("IMG_0003.jpg", ts(2025, 5, 4)))]
    p2 += [(f"{y25}/IMG_0004.HEIC", heic(5)),
           (f"{y25}/IMG_0004.HEIC.supplementa.json", sidecar("IMG_0004.HEIC", ts(2025, 5, 5)))]
    expect["IMG_0003.jpg"] = "2025/2025_05/2025_05_04"
    expect["IMG_0004.HEIC"] = "2025/2025_05/2025_05_05"
    # a long name: the sidecar is cut to 46 characters
    long_name = "Screenshot_from_some_messaging_app_holiday_party.jpg"  # stem 48 chars
    p1 += [(f"{y25}/{long_name}", jpeg(6)),
           (f"{y25}/{long_name[:46]}.json", sidecar(long_name, ts(2025, 6, 1)))]
    expect[long_name] = "2025/2025_06/2025_06_01"
    # edited copy without its own sidecar
    p1 += [(f"{y25}/IMG_0005.jpg", jpeg(7)), (f"{y25}/IMG_0005-edited.jpg", jpeg(8)),
           (f"{y25}/IMG_0005.jpg.supplemental-metadata.json",
            sidecar("IMG_0005.jpg", ts(2025, 6, 2), desc="Sunset at the pier", lat=28.5383, lon=-81.3792,
                    people=["Zulu"], favorited=True))]
    expect["IMG_0005.jpg"] = "2025/2025_06/2025_06_02"
    expect["IMG_0005-edited.jpg"] = "2025/2025_06/2025_06_02"
    # Live Photo: the MOV has no sidecar of its own
    p2 += [(f"{y25}/IMG_0006.HEIC", heic(9)), (f"{y25}/IMG_0006.MOV", video(9)),
           (f"{y25}/IMG_0006.HEIC.supplemental-metadata.json", sidecar("IMG_0006.HEIC", ts(2025, 6, 3)))]
    expect["IMG_0006.HEIC"] = "2025/2025_06/2025_06_03"
    expect["IMG_0006.MOV"] = "2025/2025_06/2025_06_03"
    # only the title inside the JSON gives it away
    p1 += [(f"{y25}/weird name.jpg", jpeg(10)),
           (f"{y25}/totally different.json", sidecar("weird name.jpg", ts(2025, 6, 4)))]
    expect["weird name.jpg"] = "2025/2025_06/2025_06_04"
    # no sidecar: name, camera clock, nothing
    p1 += [(f"{y25}/PXL_20250704_120000123.jpg", jpeg(11)),
           (f"{y25}/DSC0001.JPG", jpeg(12, exif_app1("2024:12:31 23:30:00"))),
           (f"{y25}/random.png", b"\x89PNG\r\n\x1a\n" + bytes(range(200)))]
    expect["PXL_20250704_120000123.jpg"] = "2025/2025_07/2025_07_04"
    expect["DSC0001.JPG"] = "2024/2024_12/2024_12_31"
    expect["random.png"] = "Unknown date"
    # travel: Tokyo, 7:30 am on the 11th (still the 10th in Orlando). Camera clock wins the day.
    tokyo = ts(2025, 3, 10, 22, 30)
    p1 += [(f"{y25}/TOKYO1.jpg", jpeg(13, exif_app1("2025:03:11 07:30:00", "+09:00"))),
           (f"{y25}/TOKYO1.jpg.supplemental-metadata.json", sidecar("TOKYO1.jpg", tokyo)),
           (f"{y25}/TOKYO2.jpg", jpeg(14, exif_app1("2025:03:11 07:30:00"))),
           (f"{y25}/TOKYO2.jpg.supplemental-metadata.json", sidecar("TOKYO2.jpg", tokyo))]
    expect["TOKYO1.jpg"] = "2025/2025_03/2025_03_11"
    expect["TOKYO2.jpg"] = "2025/2025_03/2025_03_11"
    # date fixed in Google Photos: the camera clock was wrong, Google wins
    p1 += [(f"{y25}/OLDCAM.jpg", jpeg(15, exif_app1("2010:01:01 00:00:00"))),
           (f"{y25}/OLDCAM.jpg.supplemental-metadata.json", sidecar("OLDCAM.jpg", ts(2015, 6, 15)))]
    expect["OLDCAM.jpg"] = "2015/2015_06/2015_06_15"
    # album: the same bytes again, plus the album's own metadata.json
    p2 += [(f"{GP}/Beach Trip/IMG_0005.jpg", jpeg(7)),
           (f"{GP}/Beach Trip/IMG_0005.jpg.supplemental-metadata.json", sidecar("IMG_0005.jpg", ts(2025, 6, 2))),
           (f"{GP}/Beach Trip/metadata.json", album_meta("Beach Trip"))]
    # Trash, and another Google product: both left alone
    p2 += [(f"{GP}/Trash/IMG_9999.jpg", jpeg(99)), ("Takeout/Google Drive/notes.jpg", jpeg(98))]
    for k in range(n_extra):
        name = f"IMG_{7000 + k}.jpg"
        p1.append((f"{yr}/{name}", jpeg(200 + k)))
        p1.append((f"{yr}/{name}.supplemental-metadata.json", sidecar(name, ts(2026, 2, 1 + k % 27, 15))))
        expect[name] = f"2026/2026_02/2026_02_{1 + k % 27:02d}"
    write_zip(os.path.join(out, "takeout-20261101T120000Z-001.zip"), p1)
    write_zip(os.path.join(out, "takeout-20261101T120000Z-002.zip"), p2)
    return expect


if __name__ == "__main__":
    dst = sys.argv[1] if len(sys.argv) > 1 else "fake_takeout"
    print(make_takeout(dst))
