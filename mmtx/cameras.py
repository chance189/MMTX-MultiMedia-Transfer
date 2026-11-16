"""Camera profiles: which camera made a file, what role it plays, when it was shot.

Card layouts handled:

  GoPro      DCIM/100GOPRO/GX010123.MP4, GL010123.LRV, GX010123.THM, GOPR0123.JPG
             model from MISC/version.txt ("camera type")
  DJI        DCIM/DJI_001/DJI_20251114063012_0001_D.MP4 (+ .LRF proxy, .SRT telemetry)
             DCIM/100MEDIA/DJI_0001.MP4 (older), DCIM/PANORAMA/...
  Insta360   DCIM/Camera01/VID_20251114_063012_00_001.insv, LRV_..._01_001.lrv, IMG_...insp
             model sniffed from the .insv trailer when present
  Sony       PRIVATE/M4ROOT/CLIP/C0001.MP4 + C0001M01.XML (date + model, e.g. ILME-FX3)
             PRIVATE/M4ROOT/SUB/C0001S03.MP4 (proxy), THMBNL/C0001T01.JPG
             DCIM/100MSDCF/DSC00001.ARW
  Phone      DCIM/Camera/PXL_20251114_063012123.mp4, 20251114_063012.mp4 (Samsung),
             DCIM/100APPLE/IMG_0001.MOV (iPhone)
  Generic    anything else with a media extension (other cameras)
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Optional

VIDEO_EXTS = {".mp4", ".mov", ".mxf", ".avi", ".mts", ".m2ts", ".mkv", ".360", ".insv", ".osv", ".braw", ".crm"}
PHOTO_EXTS = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".hif", ".dng", ".arw", ".cr2", ".cr3", ".nef",
    ".raf", ".orf", ".rw2", ".gpr", ".insp", ".tif", ".tiff",
}
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac"}
PROXY_EXTS = {".lrv", ".lrf"}
SIDECAR_EXTS = {".srt", ".thm", ".xml"}
MEDIA_EXTS = VIDEO_EXTS | PHOTO_EXTS | AUDIO_EXTS | PROXY_EXTS | {".srt", ".thm"}

PROFILE_NAMES = {
    "gopro": "GoPro",
    "dji": "DJI",
    "insta360": "Insta360",
    "sony": "Sony",
    "phone": "Phone",
    "generic": "Camera",
}

# profile -> icon family (drawn in ui/icons.py)
PROFILE_KIND = {"gopro": "action", "dji": "drone", "insta360": "360", "sony": "cinema", "phone": "phone",
                "generic": "card"}

_GOPRO_DIR = re.compile(r"^\d{3}(GOPRO|GBACK|GFRNT)$", re.I)
_GOPRO_CHAPTER = re.compile(r"^(G[A-Z])(\d{2})(\d{4})(\.[A-Za-z0-9]+)$", re.I)
_GOPRO_PHOTO = re.compile(r"^GOPR(\d{4})", re.I)
_GOPRO_BURST = re.compile(r"^G(\d{3})(\d{4})", re.I)
_DJI_DIR = re.compile(r"^(DJI_\d{3}|\d{3}MEDIA|PANORAMA|HYPERLAPSE)$", re.I)
_DJI_TS = re.compile(r"^DJI_(\d{14})", re.I)
_INSTA_DIR = re.compile(r"^Camera\d{2}$", re.I)
_INSTA_NAME = re.compile(r"^(?:VID|IMG|LRV|PRO_VID|PRO_LRV|PRO_IMG)_(\d{8})_(\d{6})_(\d{2})_(\d{3})", re.I)
_SONY_DIR = re.compile(r"^\d{3}MSDCF$", re.I)
_SONY_CLIP = re.compile(r"^(C\d{4})", re.I)
_PHONE_TS = re.compile(r"^(?:PXL|IMG|VID|MVIMG|LMC|Screenrecorder)[_-](\d{8})[_-](\d{6})", re.I)
_SAMSUNG_TS = re.compile(r"^(\d{8})_(\d{6})")
_PHONE_DIR = re.compile(r"^(Camera|\d{3}APPLE|\d{3}_\d{4}|\d{3}CANON)$", re.I)
_SONY_DATE = re.compile(r'<CreationDate\s+value="([^"]+)"')
_SONY_MODEL = re.compile(r'<Device\s+[^>]*modelName="([^"]+)"')
_GOPRO_MODEL = re.compile(r'"camera type"\s*:\s*"([^"]+)"', re.I)
_INSTA_MODEL = re.compile(
    rb"Insta360 ?(X\d(?: Pro)?|ONE ?X\d?|ONE RS|ONE R|GO ?\d(?: Pro)?|Ace(?: Pro)?(?: ?\d)?|Flow\d?)"
)

SONY_MODELS = {
    "ILME-FX3": "Sony FX3",
    "ILME-FX3A": "Sony FX3",
    "ILME-FX30": "Sony FX30",
    "ILME-FX6": "Sony FX6",
    "ILME-FX2": "Sony FX2",
    "ILCE-7SM3": "Sony A7S III",
    "ILCE-7M4": "Sony A7 IV",
    "ILCE-7RM5": "Sony A7R V",
    "ILCE-7CM2": "Sony A7C II",
    "ILCE-1": "Sony A1",
    "ILCE-1M2": "Sony A1 II",
    "ILCE-9M3": "Sony A9 III",
    "ILCE-6700": "Sony A6700",
    "ZV-E1": "Sony ZV-E1",
    "ZV-E10M2": "Sony ZV-E10 II",
}


def _upper_parts(rel: str) -> list[str]:
    return [p.upper() for p in rel.split("/") if p]


def classify(rel: str) -> str:
    """Profile key for a card-relative path (forward slashes)."""
    parts = rel.split("/")
    up = [p.upper() for p in parts]
    name = parts[-1]
    ext = os.path.splitext(name)[1].lower()
    dirs = parts[:-1]
    if len(up) >= 3 and up[0] == "PRIVATE" and up[1] == "M4ROOT":
        return "sony"
    if any(_SONY_DIR.match(d) for d in dirs):
        return "sony"
    if any(_GOPRO_DIR.match(d) for d in dirs):
        return "gopro"
    if name.upper().startswith("DJI_") or any(_DJI_DIR.match(d) for d in dirs):
        return "dji"
    if ext in (".insv", ".insp") or any(_INSTA_DIR.match(d) for d in dirs) or (
        _INSTA_NAME.match(name) and ext in (".lrv", ".mp4", ".dng", ".jpg")
    ):
        return "insta360"
    if (len(up) >= 3 and up[0] == "DCIM" and (dirs[1] == "Camera" or re.match(r"^\d{3}APPLE$", up[1])
                                              or re.match(r"^\d{3}_\d{4}$", up[1]))) or _PHONE_TS.match(name):
        return "phone"
    return "generic"


def role_of(profile: str, rel: str) -> str:
    up = _upper_parts(rel)
    ext = os.path.splitext(rel)[1].lower()
    if profile == "sony" and len(up) >= 3 and up[1] == "M4ROOT":
        if up[2] == "SUB":
            return "proxy"
        if up[2] == "THMBNL":
            return "thumb"
    if ext in PROXY_EXTS:
        return "proxy"
    if ext == ".thm":
        return "thumb"
    if ext in (".srt", ".xml"):
        return "sidecar"
    return "media"


def clip_key(profile: str, rel: str) -> str:
    """Files sharing a key belong to one shot (clip + proxy + thumbnail + sidecar)."""
    parts = rel.split("/")
    name = parts[-1]
    stem = os.path.splitext(name)[0].upper()
    parent = "/".join(parts[:-1]).upper()
    if profile == "gopro":
        m = _GOPRO_CHAPTER.match(name)
        if m:
            return f"{parent}|{m.group(3)}-{m.group(2)}"
        m = _GOPRO_PHOTO.match(name)
        if m:
            return f"{parent}|P{m.group(1)}"
        m = _GOPRO_BURST.match(name)
        if m:
            return f"{parent}|B{m.group(1)}"
        return f"{parent}|{stem}"
    if profile == "insta360":
        m = _INSTA_NAME.match(name)
        if m:
            return f"{parent}|{m.group(1)}_{m.group(2)}_{m.group(4)}"
        return f"{parent}|{stem}"
    if profile == "sony":
        m = _SONY_CLIP.match(name)
        if m and "M4ROOT" in parent:
            return m.group(1).upper()
        return f"{parent}|{stem}"
    return f"{parent}|{stem}"


def _parse_sony_xml(path: str) -> tuple[Optional[datetime], Optional[str]]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read(65536)
    except OSError:
        return None, None
    dt = model = None
    m = _SONY_DATE.search(text)
    if m:
        try:
            dt = datetime.fromisoformat(m.group(1).replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            dt = None
    m = _SONY_MODEL.search(text)
    if m:
        model = m.group(1)
    return dt, model


def _ts(date8: str, time6: str) -> Optional[datetime]:
    try:
        return datetime.strptime(date8 + time6, "%Y%m%d%H%M%S")
    except ValueError:
        return None


def capture_time(profile: str, path: str, rel: str, mtime: float) -> datetime:
    """Best-effort local wall-clock capture time."""
    name = os.path.basename(rel)
    if profile == "dji":
        m = _DJI_TS.match(name)
        if m:
            dt = _ts(m.group(1)[:8], m.group(1)[8:])
            if dt:
                return dt
    elif profile == "insta360":
        m = _INSTA_NAME.match(name)
        if m:
            dt = _ts(m.group(1), m.group(2))
            if dt:
                return dt
    elif profile == "sony":
        stem, ext = os.path.splitext(name)
        xml = None
        if path.startswith("portable://"):
            xml = None  # don't open sidecars over USB just for a date
        elif ext.lower() == ".xml":
            xml = path
        else:
            m = _SONY_CLIP.match(name)
            if m and "/CLIP/" in rel.upper():
                cand = os.path.join(os.path.dirname(path), f"{m.group(1)}M01.XML")
                if os.path.isfile(cand):
                    xml = cand
        if xml:
            dt, _ = _parse_sony_xml(xml)
            if dt:
                return dt
    elif profile in ("phone", "generic"):
        m = _PHONE_TS.match(name) or _SAMSUNG_TS.match(name)
        if m:
            dt = _ts(m.group(1), m.group(2))
            if dt:
                return dt
    return datetime.fromtimestamp(mtime) if mtime > 0 else datetime.now()


def gopro_sorted_name(name: str) -> str:
    """GX010123.MP4 -> GX0123_01.MP4 so chapters of one clip sort together."""
    m = _GOPRO_CHAPTER.match(name)
    if not m:
        return name
    prefix, chapter, num, ext = m.groups()
    return f"{prefix.upper()}{num}_{chapter}{ext}"


# ---------------------------------------------------------------- device labels


def _find_ci(root: str, *parts: str) -> Optional[str]:
    """Case-insensitive path lookup (FAT/exFAT cards mounted on Linux can differ in case)."""
    from . import sources

    cur = root
    for p in parts:
        try:
            names = sources.listdir(cur)
        except OSError:
            return None
        match = next((n for n in names if n.lower() == p.lower()), None)
        if match is None:
            return None
        cur = sources.join(cur, match)
    return cur


def gopro_model(root: str) -> Optional[str]:
    if root.startswith("portable://"):
        return None
    p = _find_ci(root, "MISC", "version.txt")
    if not p:
        return None
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            m = _GOPRO_MODEL.search(f.read(8192))
        if m:
            model = m.group(1).strip()
            return model if model.lower().startswith("gopro") else f"GoPro {model}"
    except OSError:
        pass
    return None


def insta360_model(insv_path: str) -> Optional[str]:
    try:
        size = os.path.getsize(insv_path)
        with open(insv_path, "rb") as f:
            f.seek(max(0, size - 1024 * 1024))
            tail = f.read()
        m = _INSTA_MODEL.search(tail)
        if m:
            return "Insta360 " + m.group(1).decode("ascii", "replace").strip()
    except OSError:
        pass
    return None


def sony_model(xml_path: str) -> Optional[str]:
    _, model = _parse_sony_xml(xml_path)
    if not model:
        return None
    return SONY_MODELS.get(model.upper(), f"Sony {model}")


def card_signature(root: str) -> Optional[str]:
    """Quick, cheap guess of what camera a volume came out of (for the drive picker)."""
    from . import sources

    dcim = _find_ci(root, "DCIM")
    if _find_ci(root, "PRIVATE", "M4ROOT"):
        return "Sony"
    if not dcim:
        return None
    try:
        subs = sources.listdir(dcim)
    except OSError:
        return "Camera"
    if any(_GOPRO_DIR.match(s) for s in subs):
        return gopro_model(root) or "GoPro"
    if any(_DJI_DIR.match(s) for s in subs):
        return "DJI"
    if any(_INSTA_DIR.match(s) for s in subs):
        return "Insta360"
    if any(_SONY_DIR.match(s) for s in subs):
        return "Sony"
    if any(_PHONE_DIR.match(s) for s in subs):
        return "Phone"
    return "Camera"
