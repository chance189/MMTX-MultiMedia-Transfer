"""Windows Portable Devices (WPD): phones, iPhones, GoPro over USB, cameras left in MTP mode.

These show up in Explorer under "This PC" but have no drive letter, so they are read through
the WPD COM API (comtypes):

    PortableDeviceManager.GetDevices  -> PnP ids
    PortableDevice.Open               -> Content -> EnumObjects / Properties
    Content.Transfer().GetStream      -> IStream, read with RemoteRead
    Content.Delete                    -> remove a file after its copy checked out

Every COM call runs on one dedicated thread: COM objects stay in the apartment that created
them, and MTP devices misbehave with parallel access anyway (the engine also copies one file
at a time from these). Failures are contained: a device that errors simply isn't listed.

Written against the documented WPD interfaces; not yet exercised on real hardware.
"""

from __future__ import annotations

import ctypes
import hashlib
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Callable, Optional

from .sources import Backend, Entry, PortableDevice

# Property keys (fmtid, pid)
_OBJ = "{EF6B490D-5CD8-437A-AFFC-DA8B60EE4A3C}"  # WPD_OBJECT_PROPERTIES_V1
PK_NAME = (_OBJ, 4)
PK_CONTENT_TYPE = (_OBJ, 7)
PK_SIZE = (_OBJ, 11)
PK_ORIGINAL_FILE_NAME = (_OBJ, 12)
PK_DATE_MODIFIED = (_OBJ, 19)
PK_RESOURCE_DEFAULT = ("{E81E79BE-34F0-41BF-B53F-F1A06AE87842}", 0)
PK_FUNCTIONAL_CATEGORY = ("{8F052D93-ABCA-4FC5-A5AC-B01DF4DBE598}", 2)

CT_FOLDER = "{27E2E392-A111-48E0-AB0C-E17705A05F85}"
CT_FUNCTIONAL_OBJECT = "{99ED0160-17FF-4C44-9D98-1D7A6F941921}"
CAT_STORAGE = "{23F05BBC-15DE-4C2A-A55B-A9AF5CE412EF}"

STGM_READ = 0
VT_LPWSTR = 31
VT_DATE = 7
DEVICE_OBJECT = "DEVICE"


class WpdBackend(Backend):
    name = "wpd"

    def __init__(self, log: Callable[[str], None] = lambda m: None):
        if sys.platform != "win32":
            raise OSError("Windows Portable Devices only exist on Windows")
        self.log = log
        self._ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wpd", initializer=self._init_thread)
        self._ready: Optional[Exception] = None
        self._call(self._setup)  # raises here if comtypes/WPD is missing, so the caller can log it
        self._open: dict[str, dict] = {}  # key -> {"pnp", "dev", "content", "props", "storages": {name: oid}}
        self._ids: dict[tuple[str, str], str] = {}  # (key, rel) -> object id
        self._kids: dict[tuple[str, str], dict[str, tuple[str, Entry]]] = {}  # (key, rel) -> name -> (oid, entry)

    # ------------------------------------------------------------ COM thread plumbing
    @staticmethod
    def _init_thread() -> None:
        sys.coinit_flags = 0  # COINIT_MULTITHREADED, read by comtypes when it is first imported
        import comtypes

        try:
            comtypes.CoInitializeEx(0)
        except OSError:
            pass  # already initialized in this thread

    def _call(self, fn, *args, timeout: float = 120.0):
        return self._ex.submit(fn, *args).result(timeout=timeout)

    def _setup(self) -> None:
        import comtypes
        import comtypes.client

        comtypes.client.GetModule("portabledeviceapi.dll")
        comtypes.client.GetModule("portabledevicetypes.dll")
        from comtypes.gen import PortableDeviceApiLib as port
        from comtypes.gen import PortableDeviceTypesLib as types

        self.comtypes, self.port, self.types = comtypes, port, types
        self.mgr = self._create(port.PortableDeviceManager, port.IPortableDeviceManager)
        self.pk = {name: self._key(*val) for name, val in {
            "name": PK_NAME, "ctype": PK_CONTENT_TYPE, "size": PK_SIZE, "orig": PK_ORIGINAL_FILE_NAME,
            "mtime": PK_DATE_MODIFIED, "res": PK_RESOURCE_DEFAULT, "cat": PK_FUNCTIONAL_CATEGORY}.items()}

    def _create(self, coclass, iface):
        return self.comtypes.client.CreateObject(coclass, clsctx=self.comtypes.CLSCTX_INPROC_SERVER, interface=iface)

    def _key(self, fmtid: str, pid: int):
        k = self.port._tagpropertykey()
        k.fmtid = self.comtypes.GUID(fmtid)
        k.pid = pid
        return ctypes.pointer(k)

    def _values(self):
        """An IPortableDeviceValues the API library accepts (both type libraries define it)."""
        cls = getattr(self.port, "PortableDeviceValues", None) or self.types.PortableDeviceValues
        iface = getattr(self.port, "IPortableDeviceValues", None) or self.types.IPortableDeviceValues
        return self._create(cls, iface)

    def _key_collection(self, names: list[str]):
        cls = getattr(self.port, "PortableDeviceKeyCollection", None) or self.types.PortableDeviceKeyCollection
        iface = getattr(self.port, "IPortableDeviceKeyCollection", None) or self.types.IPortableDeviceKeyCollection
        keys = self._create(cls, iface)
        for n in names:
            keys.Add(self.pk[n])
        return keys

    # ------------------------------------------------------------ devices
    def devices(self) -> list[PortableDevice]:
        return self._call(self._devices, timeout=30.0)

    def _mgr_string(self, fn, pnp: str) -> str:
        ln = ctypes.pointer(ctypes.c_ulong(0))
        fn(pnp, ctypes.POINTER(ctypes.c_ushort)(), ln)
        if not ln.contents.value:
            return ""
        buf = ctypes.create_unicode_buffer(ln.contents.value)
        fn(pnp, ctypes.cast(buf, ctypes.POINTER(ctypes.c_ushort)), ln)
        return buf.value

    def _devices(self) -> list[PortableDevice]:
        try:
            self.mgr.RefreshDeviceList()
        except Exception:  # noqa: BLE001
            pass
        count = ctypes.pointer(ctypes.c_ulong(0))
        self.mgr.GetDevices(ctypes.POINTER(ctypes.c_wchar_p)(), count)
        n = count.contents.value
        pnps: list[str] = []
        if n:
            arr = (ctypes.c_wchar_p * n)()
            self.mgr.GetDevices(ctypes.cast(arr, ctypes.POINTER(ctypes.c_wchar_p)), count)
            pnps = [arr[i] for i in range(count.contents.value) if arr[i]]
        out = []
        live = set()
        for pnp in pnps:
            key = hashlib.sha1(pnp.encode("utf-8", "replace")).hexdigest()[:8]
            live.add(key)
            try:
                info = self._open.get(key) or self._open_device(key, pnp)
                if info["storages"]:
                    out.append(PortableDevice(key=key, name=info["name"], maker=info["maker"],
                                              storages=list(info["storages"])))
            except Exception as e:  # noqa: BLE001
                self.log(f"Portable device skipped ({pnp[:60]}): {e}")
        for gone in set(self._open) - live:  # unplugged: forget its handles and cached listings
            self._forget(gone)
        return out

    def _open_device(self, key: str, pnp: str) -> dict:
        name = maker = ""
        try:
            name = self._mgr_string(self.mgr.GetDeviceFriendlyName, pnp)
        except Exception:  # noqa: BLE001
            pass
        if not name:
            try:
                name = self._mgr_string(self.mgr.GetDeviceDescription, pnp)
            except Exception:  # noqa: BLE001
                name = "USB device"
        try:
            maker = self._mgr_string(self.mgr.GetDeviceManufacturer, pnp)
        except Exception:  # noqa: BLE001
            pass
        dev = self._create(self.port.PortableDevice, self.port.IPortableDevice)
        dev.Open(pnp, self._values())
        content = dev.Content()
        props = content.Properties()
        info = {"pnp": pnp, "dev": dev, "content": content, "props": props, "name": name, "maker": maker,
                "storages": {}}
        self._open[key] = info
        for oid, entry, ctype in self._children(key, DEVICE_OBJECT, with_type=True):
            if ctype == CT_FUNCTIONAL_OBJECT and self._is_storage(info, oid):
                info["storages"][entry.name] = oid
                self._ids[(key, entry.name)] = oid
        return info

    def _is_storage(self, info: dict, oid: str) -> bool:
        try:
            vals = info["props"].GetValues(oid, self._key_collection(["cat"]))
            return str(vals.GetGuidValue(self.pk["cat"])).upper() == CAT_STORAGE
        except Exception:  # noqa: BLE001 - some drivers don't report it; a functional object with files is fine
            return True

    def _forget(self, key: str) -> None:
        info = self._open.pop(key, None)
        if info is not None:
            try:
                info["dev"].Close()
            except Exception:  # noqa: BLE001
                pass
        for k in [k for k in self._ids if k[0] == key]:
            self._ids.pop(k, None)
        for k in [k for k in self._kids if k[0] == key]:
            self._kids.pop(k, None)

    # ------------------------------------------------------------ objects
    def _date(self, vals) -> float:
        try:
            pv = vals.GetValue(self.pk["mtime"])
            if getattr(pv, "vt", 0) != VT_DATE:
                return 0.0
            for fname, ftype in type(pv)._fields_:
                if hasattr(ftype, "date"):
                    days = getattr(pv, fname).date
                    return (datetime(1899, 12, 30) + timedelta(days=days)).timestamp()
        except Exception:  # noqa: BLE001
            pass
        return 0.0

    def _children(self, key: str, parent_oid: str, with_type: bool = False):
        info = self._open[key]
        content, props = info["content"], info["props"]
        filt = ctypes.POINTER(getattr(self.port, "IPortableDeviceValues", None) or self.types.IPortableDeviceValues)()
        enum = content.EnumObjects(0, parent_oid, filt)
        keys = self._key_collection(["name", "orig", "ctype", "size", "mtime"])
        while True:
            fetched = ctypes.pointer(ctypes.c_ulong(0))
            ids = enum.Next(1, fetched)
            if fetched.contents.value == 0:
                break
            oid = str(ids[0]) if not isinstance(ids, str) else ids
            try:
                vals = props.GetValues(oid, keys)
            except Exception:  # noqa: BLE001
                continue
            ctype = ""
            try:
                ctype = str(vals.GetGuidValue(self.pk["ctype"])).upper()
            except Exception:  # noqa: BLE001
                pass
            is_dir = ctype in (CT_FOLDER, CT_FUNCTIONAL_OBJECT)
            name = ""
            if not is_dir:
                try:
                    name = vals.GetStringValue(self.pk["orig"])
                except Exception:  # noqa: BLE001
                    pass
            if not name:
                try:
                    name = vals.GetStringValue(self.pk["name"])
                except Exception:  # noqa: BLE001
                    name = oid
            size = 0
            if not is_dir:
                try:
                    size = int(vals.GetUnsignedLargeIntegerValue(self.pk["size"]))
                except Exception:  # noqa: BLE001
                    pass
            entry = Entry(name=name, is_dir=is_dir, size=size, mtime=self._date(vals))
            yield (oid, entry, ctype) if with_type else (oid, entry)

    def _resolve(self, key: str, rel: str) -> str:
        if key not in self._open:
            raise FileNotFoundError(2, "Device not connected", key)
        oid = self._ids.get((key, rel))
        if oid:
            return oid
        parent, _, name = rel.rpartition("/")
        if not parent:
            raise FileNotFoundError(2, "No such storage on the device", rel)
        kids = self._kids.get((key, parent))
        if kids is None:
            self._list(key, parent)
            kids = self._kids.get((key, parent), {})
        hit = kids.get(name)
        if hit is None:
            raise FileNotFoundError(2, "No such file on the device", rel)
        return hit[0]

    def _list(self, key: str, rel: str) -> list[Entry]:
        oid = self._resolve(key, rel)
        kids: dict[str, tuple[str, Entry]] = {}
        for child_oid, entry in self._children(key, oid):
            kids[entry.name] = (child_oid, entry)
            self._ids[(key, f"{rel}/{entry.name}")] = child_oid
        self._kids[(key, rel)] = kids
        return [e for _o, e in kids.values()]

    def list_dir(self, key: str, rel: str) -> list[Entry]:
        return self._call(self._list, key, rel)

    # ------------------------------------------------------------ reading and deleting
    def open(self, key: str, rel: str):
        return self._call(self._open_stream, key, rel)

    def _open_stream(self, key: str, rel: str) -> "_WpdReader":
        oid = self._resolve(key, rel)
        resources = self._open[key]["content"].Transfer()
        optimal = ctypes.pointer(ctypes.c_ulong(0))
        optimal, stream = resources.GetStream(oid, self.pk["res"], STGM_READ, optimal)
        block = int(optimal.contents.value) if hasattr(optimal, "contents") else int(optimal or 0)
        stream = getattr(stream, "value", stream)
        return _WpdReader(self, stream, max(block, 256 * 1024))

    def remove(self, key: str, rel: str) -> None:
        self._call(self._remove, key, rel)

    def _remove(self, key: str, rel: str) -> None:
        oid = self._resolve(key, rel)
        cls = getattr(self.port, "PortableDevicePropVariantCollection", None) or self.types.PortableDevicePropVariantCollection
        iface = (getattr(self.port, "IPortableDevicePropVariantCollection", None)
                 or self.types.IPortableDevicePropVariantCollection)
        coll = self._create(cls, iface)
        pv_cls = getattr(self.port, "tag_inner_PROPVARIANT", None) or self.types.tag_inner_PROPVARIANT
        pv = pv_cls()
        pv.vt = VT_LPWSTR
        keep = ctypes.c_wchar_p(oid)
        for fname, ftype in type(pv)._fields_:
            if hasattr(ftype, "pwszVal"):
                getattr(pv, fname).pwszVal = keep
                break
        coll.Add(pv)
        try:
            self._open[key]["content"].Delete(0, coll, None)
        except TypeError:
            self._open[key]["content"].Delete(0, coll, ctypes.pointer(ctypes.POINTER(iface)()))
        parent, _, name = rel.rpartition("/")
        self._ids.pop((key, rel), None)
        self._kids.get((key, parent), {}).pop(name, None)


class _WpdReader:
    """File-like reader over an IStream; every RemoteRead happens on the COM thread."""

    def __init__(self, backend: WpdBackend, stream, block: int):
        self.b = backend
        self.stream = stream
        self.block = block

    def _read(self, n: int) -> bytes:
        out = bytearray()
        want = n if n is not None and n >= 0 else 1 << 62
        while len(out) < want:
            buf, length = self.stream.RemoteRead(min(self.block, want - len(out)))
            if not length:
                break
            out += bytes(buf)[:length]
        return bytes(out)

    def read(self, n: int = -1) -> bytes:
        return self.b._call(self._read, n)

    def close(self) -> None:
        if self.stream is not None:
            holder = [self.stream]
            self.stream = None
            try:
                self.b._call(holder.clear)  # drop the last reference on the COM thread
            except Exception:  # noqa: BLE001
                pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
