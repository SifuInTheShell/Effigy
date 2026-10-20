"""AMD: what nvidia-smi answers, for an AMD card on Windows (no nvidia-smi, no working amd-smi).

Name, VRAM and driver from DXGI; VRAM in use from the "GPU Adapter Memory" performance counter, which
counts every process (HIP's own mem_get_info only sees the asking process). No torch, so the backend's
venv can ask. ponytail: no GPU utilisation (the per-engine counters need two samples).
"""

import ctypes
import functools
import os
import shutil
from ctypes import wintypes

AMD = 0x1002


class _Desc1(ctypes.Structure):  # DXGI_ADAPTER_DESC1
    _fields_ = [
        ("Description", ctypes.c_wchar * 128),
        ("VendorId", wintypes.UINT),
        ("DeviceId", wintypes.UINT),
        ("SubSysId", wintypes.UINT),
        ("Revision", wintypes.UINT),
        ("DedicatedVideoMemory", ctypes.c_size_t),
        ("DedicatedSystemMemory", ctypes.c_size_t),
        ("SharedSystemMemory", ctypes.c_size_t),
        ("LuidLow", wintypes.DWORD),
        ("LuidHigh", wintypes.LONG),
        ("Flags", wintypes.UINT),
    ]


class _Value(ctypes.Structure):  # PDH_FMT_COUNTERVALUE with the LONGLONG member
    _fields_ = [("CStatus", wintypes.DWORD), ("largeValue", ctypes.c_longlong)]


def _guid(s: str) -> bytes:
    import uuid

    return uuid.UUID(s).bytes_le


def _call(obj, index, *args, argtypes=()):
    """COM method `index` of `obj`'s vtable."""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtbl[index])
    return fn(obj, *args)


@functools.cache  # ponytail: once per app run, as machine.sm()
def adapter() -> dict | None:
    """{name, total_mib, driver, luid} of the AMD adapter with the most dedicated memory, or None."""
    if os.name != "nt":
        return None
    try:
        factory = ctypes.c_void_p()
        iid = _guid("770aae78-f26f-4dba-a829-253c83d1b387")  # IDXGIFactory1
        if ctypes.windll.dxgi.CreateDXGIFactory1(iid, ctypes.byref(factory)) < 0:
            return None
        found, i = [], 0
        while True:
            a = ctypes.c_void_p()
            if (
                _call(factory, 12, i, ctypes.byref(a), argtypes=(wintypes.UINT, ctypes.c_void_p))
                < 0
            ):
                break  # DXGI_ERROR_NOT_FOUND: no more adapters
            d, umd = _Desc1(), ctypes.c_longlong()
            _call(a, 10, ctypes.byref(d), argtypes=(ctypes.c_void_p,))  # GetDesc1
            dev = _guid("54ec77fa-1377-44e6-8c32-88fd5f44c84c")  # IDXGIDevice: the UMD version
            _call(a, 9, dev, ctypes.byref(umd), argtypes=(ctypes.c_char_p, ctypes.c_void_p))
            _call(a, 2)  # Release
            if d.VendorId == AMD and not d.Flags & 2:  # not the software adapter
                v = umd.value
                found.append(
                    {
                        "name": d.Description,
                        "total_mib": d.DedicatedVideoMemory / 2**20,
                        "driver": ".".join(str(v >> s & 0xFFFF) for s in (48, 32, 16, 0)),
                        "luid": f"luid_0x{d.LuidHigh & 0xFFFFFFFF:08x}_0x{d.LuidLow:08x}_phys_0",
                    }
                )
            i += 1
        _call(factory, 2)
        return max(found, key=lambda g: g["total_mib"], default=None)
    except OSError:
        return None


def active() -> bool:
    """The steps run on an AMD card: one is there and nvidia-smi is not (an AMD iGPU beside an NVIDIA card
    changes nothing)."""
    return shutil.which("nvidia-smi") is None and adapter() is not None


def used_mib() -> float | None:
    """VRAM in use on `adapter()` by every process, or None."""
    if not (g := adapter()):
        return None
    pdh, q, c, v = ctypes.windll.pdh, ctypes.c_void_p(), ctypes.c_void_p(), _Value()
    path = f"\\GPU Adapter Memory({g['luid']})\\Dedicated Usage"
    try:
        if pdh.PdhOpenQueryW(None, None, ctypes.byref(q)):
            return None
        try:
            if pdh.PdhAddEnglishCounterW(q, path, None, ctypes.byref(c)) or pdh.PdhCollectQueryData(
                q
            ):
                return None
            if pdh.PdhGetFormattedCounterValue(c, 0x400, None, ctypes.byref(v)):  # PDH_FMT_LARGE
                return None
            return v.largeValue / 2**20
        finally:
            pdh.PdhCloseQuery(q)
    except OSError:
        return None


def smi(*fields: str) -> list[float | None] | None:
    """jobs._smi's answer from the AMD card. None for "index": HIP numbers the devices its own way, so
    jobs._pin_gpu leaves the choice to HIP."""
    if "index" in fields or not (g := adapter()) or (used := used_mib()) is None:
        return None
    total = g["total_mib"]
    have = {"memory.total": total, "memory.used": used, "memory.free": total - used}
    return [have.get(f) for f in fields]  # utilization.gpu: None
