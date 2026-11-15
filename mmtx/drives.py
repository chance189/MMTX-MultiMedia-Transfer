"""What kind of drive is a path on? Decides how many files to copy at once.

Why it matters:
  * Flash (NVMe, SATA SSD) has no seek penalty and deep queues: copying file B
    while file A is being read back for verification overlaps the two passes.
  * A spinning hard drive pays a seek every time it switches between files, and
    files written side by side get fragmented. One file at a time is fastest.
  * An SD card is the bottleneck on its own; a second stream only lets the
    read-back of the previous file overlap with the next copy.

Detection: Windows IOCTL_STORAGE_QUERY_PROPERTY (bus type + seek penalty),
Linux /sys/block/*/queue/rotational, macOS `diskutil info -plist`.
Anything that can't be identified is treated like a hard drive (safe default).
"""

from __future__ import annotations

import os
import plistlib
import struct
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache

KIND_LABELS = {
    "nvme": "NVMe SSD",
    "ssd": "SSD",
    "hdd": "Hard drive",
    "sd": "SD card",
    "usb": "USB drive",
    "network": "Network",
    "unknown": "",
}


@dataclass(frozen=True)
class DriveInfo:
    kind: str  # nvme | ssd | hdd | sd | usb | network | unknown
    model: str = ""

    @property
    def label(self) -> str:
        return KIND_LABELS.get(self.kind, "")

    @property
    def is_flash(self) -> bool:
        return self.kind in ("nvme", "ssd", "sd")


def _existing(path: str) -> str:
    p = os.path.abspath(path)
    while not os.path.exists(p) and os.path.dirname(p) != p:
        p = os.path.dirname(p)
    return p


# ------------------------------------------------------------------ Linux


def _read(p: str) -> str:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return ""


def _linux_block_dir(dev_dir: str, depth: int = 0) -> str:
    """Resolve partitions and device-mapper (LUKS/LVM) layers down to the disk."""
    if depth > 4:
        return dev_dir
    if not os.path.isdir(os.path.join(dev_dir, "queue")) and os.path.isdir(os.path.join(os.path.dirname(dev_dir), "queue")):
        dev_dir = os.path.dirname(dev_dir)
    slaves = os.path.join(dev_dir, "slaves")
    if os.path.basename(dev_dir).startswith(("dm-", "md")) and os.path.isdir(slaves):
        subs = sorted(os.listdir(slaves))
        if subs:
            return _linux_block_dir(os.path.realpath(os.path.join(slaves, subs[0])), depth + 1)
    return dev_dir


def _linux(path: str) -> DriveInfo:
    st = os.stat(path)
    sys_link = f"/sys/dev/block/{os.major(st.st_dev)}:{os.minor(st.st_dev)}"
    if not os.path.exists(sys_link):
        return DriveInfo("unknown")
    real = os.path.realpath(sys_link)
    dev = _linux_block_dir(real)
    name = os.path.basename(dev)
    model = _read(os.path.join(dev, "device", "model")) or _read(os.path.join(dev, "device", "name"))
    if name.startswith("nvme"):
        return DriveInfo("nvme", model)
    if name.startswith("mmcblk"):
        return DriveInfo("sd", model)
    rot = _read(os.path.join(dev, "queue", "rotational"))
    if "/usb" in real:
        if rot == "1" and _read(os.path.join(dev, "removable")) != "1":
            return DriveInfo("hdd", model)
        return DriveInfo("usb", model)
    if rot == "1":
        return DriveInfo("hdd", model)
    if rot == "0":
        return DriveInfo("ssd", model)
    return DriveInfo("unknown", model)


# ------------------------------------------------------------------ Windows

_BUS_NVME, _BUS_SD, _BUS_MMC, _BUS_USB = 0x11, 0x0C, 0x0D, 0x07


def _windows(path: str) -> DriveInfo:
    import ctypes
    from ctypes import wintypes

    drive = os.path.splitdrive(os.path.abspath(path))[0]
    if not drive or drive.startswith("\\\\"):
        return DriveInfo("network")
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if k32.GetDriveTypeW(ctypes.c_wchar_p(drive + "\\")) == 4:
        return DriveInfo("network")

    CreateFileW = k32.CreateFileW
    CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    CreateFileW.restype = wintypes.HANDLE
    DeviceIoControl = k32.DeviceIoControl
    DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    DeviceIoControl.restype = wintypes.BOOL
    CloseHandle = k32.CloseHandle
    CloseHandle.argtypes = [wintypes.HANDLE]

    IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400
    handle = CreateFileW("\\\\.\\" + drive, 0, 0x1 | 0x2, None, 3, 0, None)  # no access rights needed
    if handle is None or handle == ctypes.c_void_p(-1).value:
        return DriveInfo("unknown")
    try:
        def query(prop_id: int, out_size: int):
            q = struct.pack("<IIB3x", prop_id, 0, 0)  # STORAGE_PROPERTY_QUERY (PropertyStandardQuery)
            inbuf = ctypes.create_string_buffer(q, len(q))
            out = ctypes.create_string_buffer(out_size)
            got = wintypes.DWORD(0)
            ok = DeviceIoControl(handle, IOCTL_STORAGE_QUERY_PROPERTY, inbuf, len(q), out, out_size, ctypes.byref(got), None)
            return out.raw[: got.value] if ok and got.value else None

        desc = query(0, 1024)  # StorageDeviceProperty -> STORAGE_DEVICE_DESCRIPTOR
        bus, removable, model = 0, False, ""
        if desc and len(desc) >= 32:
            (_ver, _sz, _dt, _dtm, removable_b, _cq, _vend, prod_off, _rev, _ser, bus) = struct.unpack_from("<IIBBBBIIIII", desc, 0)
            removable = bool(removable_b)
            if 0 < prod_off < len(desc):
                model = desc[prod_off:].split(b"\0", 1)[0].decode("ascii", "replace").strip()
        seek = None
        pen = query(7, 12)  # StorageDeviceSeekPenaltyProperty -> DEVICE_SEEK_PENALTY_DESCRIPTOR
        if pen and len(pen) >= 9:
            seek = bool(pen[8])
    finally:
        CloseHandle(handle)

    if bus == _BUS_NVME:
        return DriveInfo("nvme", model)
    if bus in (_BUS_SD, _BUS_MMC):
        return DriveInfo("sd", model)
    if seek is True:
        return DriveInfo("hdd", model)
    if bus == _BUS_USB:
        return DriveInfo("usb" if (removable or seek is None) else "ssd", model)
    if seek is False:
        return DriveInfo("ssd", model)
    return DriveInfo("unknown", model)


# ------------------------------------------------------------------ macOS


def _macos(path: str) -> DriveInfo:
    out = subprocess.run(["diskutil", "info", "-plist", path], capture_output=True, timeout=5)
    info = plistlib.loads(out.stdout)
    proto = str(info.get("BusProtocol") or info.get("Protocol") or "")
    model = str(info.get("MediaName") or "")
    if "Secure Digital" in proto:
        return DriveInfo("sd", model)
    if proto in ("PCI-Express", "PCI", "Apple Fabric", "NVMe"):
        return DriveInfo("nvme", model)
    solid = info.get("SolidState")
    if solid is False:
        return DriveInfo("hdd", model)
    if proto == "USB":
        return DriveInfo("usb" if solid is None else "ssd", model)
    if solid:
        return DriveInfo("ssd", model)
    return DriveInfo("unknown", model)


@lru_cache(maxsize=64)
def _drive_info_cached(path: str) -> DriveInfo:
    try:
        if sys.platform == "win32":
            return _windows(path)
        if sys.platform == "darwin":
            return _macos(path)
        return _linux(path)
    except Exception:  # noqa: BLE001 - detection must never break a transfer
        return DriveInfo("unknown")


def drive_info(path: str) -> DriveInfo:
    p = _existing(path)
    if sys.platform == "win32":
        p = os.path.splitdrive(p)[0] + "\\" if os.path.splitdrive(p)[0] else p
    return _drive_info_cached(p)


def auto_workers(src: DriveInfo, dst: DriveInfo) -> tuple[int, str]:
    """(files at once, human reason)."""
    if dst.kind in ("hdd", "unknown", "network", "usb"):
        why = {"hdd": "destination is a hard drive", "network": "destination is a network share",
               "usb": "destination is a USB drive of unknown type"}.get(dst.kind, "destination drive type unknown")
        return 1, why
    if src.kind in ("hdd", "unknown", "network"):
        return 1, "source is a hard drive" if src.kind == "hdd" else "source drive type unknown"
    if src.kind in ("sd", "usb"):
        return 2, f"{src.label} to {dst.label}: verify overlaps the next copy"
    return 3, f"{src.label} to {dst.label}: flash on both sides"
