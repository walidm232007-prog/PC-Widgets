"""
wgc.py
======
Windows.Graphics.Capture, through ctypes: frames of one window as Windows
composes it, handed over on the graphics card at the window's own pace. Live
glass (glass.py) follows a live wallpaper with it, as fast as the wallpaper
draws - about 26 frames a second for Wallpaper Engine here.

PrintWindow (livewall.py) has the compositor draw the whole wallpaper layer
into a bitmap for every grab: 40 ms and 15 ms of CPU for 3000x1920, which
held live glass to about ten frames a second. Here each frame is already on
the graphics card, and reading back the patches behind four cards takes
under a millisecond of CPU.

What it captures is the desktop window, Progman, which since Windows 11 24H2
holds the wallpaper layer, the live wallpaper's window inside it, and the
icons. Before 24H2 the layer is a window of its own, and that is captured
instead. The cards are windows of their own, so they are never in it:
nothing is left out of screen captures, and the widgets stay in screenshots.
Desktop icons behind a card do show through its glass, as through glass.

Windows draws a yellow border round a captured window; it is turned off
(IsBorderRequired), and the cursor left out. Where the border can't be
turned off - Windows 10 - no capture is made, and live glass keeps to
PrintWindow, rather than framing the whole desktop in yellow.

No package is needed: the few COM and WinRT interfaces it touches are called
through their vtables. A Capture belongs to the thread that opened it, which
must have joined the multithreaded apartment (RoInitialize) - live glass's
thread does.
"""

from __future__ import annotations

import ctypes as C
import ctypes.wintypes as W
import uuid
from typing import List, Optional, Sequence, Tuple

from PySide6.QtGui import QImage

HRESULT = C.c_long


class _GUID(C.Structure):
    _fields_ = [("Data1", C.c_uint32), ("Data2", C.c_uint16), ("Data3", C.c_uint16),
                ("Data4", C.c_ubyte * 8)]


def _guid(text: str) -> _GUID:
    u = uuid.UUID(text)
    g = _GUID(u.time_low, u.time_mid, u.time_hi_version)
    for i, b in enumerate(u.bytes[8:]):
        g.Data4[i] = b
    return g


_IID_DXGI_DEVICE = _guid("54EC77FA-1377-44E6-8C32-88FD5F44C84C")
_IID_ITEM_INTEROP = _guid("3628E81B-3CAC-4C60-B7F4-23CE0E0C3356")
_IID_ITEM = _guid("79C3F95B-31F7-4EC2-A464-632EF5D30760")
_IID_POOL_STATICS2 = _guid("589B103F-6BBC-5DF5-A991-02E28B3B66D5")
_IID_SESSION2 = _guid("2C39AE40-7D2E-5044-804E-8B6799D4CF9E")
_IID_SESSION3 = _guid("F2CDD966-22AE-5EA1-9596-3A289344C3BE")
_IID_DXGI_ACCESS = _guid("A9B3D012-3DF2-4EE3-B8D1-8695F457D3C1")
_IID_TEXTURE2D = _guid("6F15AAF2-D208-4E89-9AB4-489535D34F9C")
_IID_CLOSABLE = _guid("30D5A829-7FA4-4026-83BB-D75BAE4EA99E")

_BGRA = 87                      # DXGI_FORMAT_B8G8R8A8_UNORM / DirectXPixelFormat
_STAGING, _CPU_READ, _MAP_READ = 3, 0x20000, 1


class _Size(C.Structure):
    _fields_ = [("Width", C.c_int32), ("Height", C.c_int32)]


class _TexDesc(C.Structure):
    _fields_ = [("Width", C.c_uint), ("Height", C.c_uint), ("MipLevels", C.c_uint),
                ("ArraySize", C.c_uint), ("Format", C.c_uint), ("SampleCount", C.c_uint),
                ("SampleQuality", C.c_uint), ("Usage", C.c_uint), ("BindFlags", C.c_uint),
                ("CPUAccessFlags", C.c_uint), ("MiscFlags", C.c_uint)]


class _Box(C.Structure):
    _fields_ = [("left", C.c_uint), ("top", C.c_uint), ("front", C.c_uint),
                ("right", C.c_uint), ("bottom", C.c_uint), ("back", C.c_uint)]


class _Mapped(C.Structure):
    _fields_ = [("pData", C.c_void_p), ("RowPitch", C.c_uint), ("DepthPitch", C.c_uint)]


try:
    _combase = C.WinDLL("combase")
    _d3d11 = C.WinDLL("d3d11")
    _user = C.WinDLL("user32")
    _combase.RoInitialize.argtypes, _combase.RoInitialize.restype = [C.c_int], HRESULT
    _combase.RoUninitialize.argtypes = []
    _combase.WindowsCreateString.argtypes = [W.LPCWSTR, C.c_uint32, C.POINTER(C.c_void_p)]
    _combase.WindowsCreateString.restype = HRESULT
    _combase.WindowsDeleteString.argtypes = [C.c_void_p]
    _combase.RoGetActivationFactory.argtypes = [C.c_void_p, C.POINTER(_GUID), C.POINTER(C.c_void_p)]
    _combase.RoGetActivationFactory.restype = HRESULT
    _d3d11.D3D11CreateDevice.argtypes = [C.c_void_p, C.c_int, C.c_void_p, C.c_uint, C.c_void_p,
                                         C.c_uint, C.c_uint, C.POINTER(C.c_void_p),
                                         C.POINTER(C.c_int), C.POINTER(C.c_void_p)]
    _d3d11.D3D11CreateDevice.restype = HRESULT
    _d3d11.CreateDirect3D11DeviceFromDXGIDevice.argtypes = [C.c_void_p, C.POINTER(C.c_void_p)]
    _d3d11.CreateDirect3D11DeviceFromDXGIDevice.restype = HRESULT
    _user.GetAncestor.argtypes, _user.GetAncestor.restype = [W.HWND, W.UINT], W.HWND
    _user.GetWindowRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
except (AttributeError, OSError):          # not Windows, or too old a one
    _combase = _d3d11 = _user = None


_prototypes: dict = {}
_methods: dict = {}


def _call(obj, index: int, argtypes, *args, restype=HRESULT):
    """Method ``index`` of a COM object's vtable. The callable for each kind
    of object's method is made once and kept: made afresh at every call it
    cost 9 microseconds, and live glass makes hundreds of calls a frame."""
    address = obj.value if isinstance(obj, C.c_void_p) else obj
    table = C.c_void_p.from_address(address).value
    shape = (restype, tuple(argtypes))
    key = (table, index, shape)
    method = _methods.get(key)
    if method is None:
        prototype = _prototypes.get(shape)
        if prototype is None:
            prototype = _prototypes[shape] = C.WINFUNCTYPE(restype, C.c_void_p, *argtypes)
        method = _methods[key] = prototype(C.c_void_p.from_address(
            table + index * C.sizeof(C.c_void_p)).value)
    return method(address, *args)


def _ok(hr, what: str) -> None:
    if hr is not None and hr < 0:
        raise OSError(f"{what} failed (0x{hr & 0xFFFFFFFF:08X})")


def _query(obj, iid: _GUID) -> C.c_void_p:
    out = C.c_void_p()
    _ok(_call(obj, 0, [C.POINTER(_GUID), C.POINTER(C.c_void_p)], C.byref(iid), C.byref(out)),
        "QueryInterface")
    return out


def _release(obj) -> None:
    if obj is not None and obj.value:
        _call(obj, 2, [], restype=C.c_ulong)
        obj.value = None


def _close(obj) -> None:
    """IClosable.Close, for a WinRT object that holds something until closed."""
    if obj is None or not obj.value:
        return
    try:
        closable = _query(obj, _IID_CLOSABLE)
        _call(closable, 6, [])
        _release(closable)
    except OSError:
        pass


def _factory(name: str, iid: _GUID) -> C.c_void_p:
    text = C.c_void_p()
    _ok(_combase.WindowsCreateString(name, len(name), C.byref(text)), "WindowsCreateString")
    out = C.c_void_p()
    try:
        _ok(_combase.RoGetActivationFactory(text, C.byref(iid), C.byref(out)), name)
    finally:
        _combase.WindowsDeleteString(text)
    return out


def join_apartment() -> None:
    """Join the multithreaded apartment, as a thread must before it opens a
    Capture. Harmless if it already has."""
    if _combase is not None:
        _combase.RoInitialize(1)


def leave_apartment() -> None:
    if _combase is not None:
        _combase.RoUninitialize()


def target(layer: int) -> int:
    """The window to capture for a live wallpaper drawing in ``layer``: the
    desktop window holding it (24H2 and later), or the layer itself when it is
    a window of its own."""
    if _user is None or not layer:
        return 0
    return int(_user.GetAncestor(layer, 2) or layer)            # GA_ROOT


class Frame:
    """One frame, held on the graphics card until closed."""

    def __init__(self, frame: C.c_void_p) -> None:
        self._frame = frame
        self._surface, self._access, self.texture = C.c_void_p(), None, C.c_void_p()
        try:
            _ok(_call(frame, 6, [C.POINTER(C.c_void_p)], C.byref(self._surface)), "frame surface")
            self._access = _query(self._surface, _IID_DXGI_ACCESS)
            _ok(_call(self._access, 3, [C.POINTER(_GUID), C.POINTER(C.c_void_p)],
                      C.byref(_IID_TEXTURE2D), C.byref(self.texture)), "frame texture")
            size = _Size()
            _ok(_call(frame, 8, [C.POINTER(_Size)], C.byref(size)), "frame size")
            self.size = (size.Width, size.Height)
            # When the compositor shows it, in s on time.perf_counter's clock:
            # SystemRelativeTime counts the same QueryPerformanceCounter, in
            # 100 ns steps.
            span = C.c_int64()
            self.time = (span.value / 1e7 if _call(frame, 7, [C.POINTER(C.c_int64)],
                                                   C.byref(span)) >= 0 else None)
        except OSError:
            self.close()
            raise

    def close(self) -> None:
        for obj in (self.texture, self._access, self._surface):
            _release(obj)
        _close(self._frame)
        _release(self._frame)


class Capture:
    """Frames of one window. Opened on, and used from, one thread that has
    joined the multithreaded apartment (join_apartment)."""

    def __init__(self, hwnd: int) -> None:
        if _combase is None:
            raise OSError("Windows.Graphics.Capture isn't available")
        self.hwnd = int(hwnd)
        self._device, self._context, self._dxgi = C.c_void_p(), C.c_void_p(), None
        self._rt, self._item, self._pool, self._session = None, C.c_void_p(), C.c_void_p(), C.c_void_p()
        self._staging = {}
        self.size = (0, 0)
        try:
            self._open()
        except Exception:
            self.close()
            raise

    def _open(self) -> None:
        level = C.c_int()
        hr = -1
        for driver in (1, 5):                                    # hardware, then WARP
            hr = _d3d11.D3D11CreateDevice(None, driver, None, 0x20, None, 0, 7,
                                          C.byref(self._device), C.byref(level),
                                          C.byref(self._context))
            if hr >= 0:
                break
        _ok(hr, "D3D11CreateDevice")
        self._dxgi = _query(self._device, _IID_DXGI_DEVICE)
        self._rt = C.c_void_p()
        _ok(_d3d11.CreateDirect3D11DeviceFromDXGIDevice(self._dxgi, C.byref(self._rt)),
            "CreateDirect3D11DeviceFromDXGIDevice")
        interop = _factory("Windows.Graphics.Capture.GraphicsCaptureItem", _IID_ITEM_INTEROP)
        try:
            _ok(_call(interop, 3, [W.HWND, C.POINTER(_GUID), C.POINTER(C.c_void_p)], self.hwnd,
                      C.byref(_IID_ITEM), C.byref(self._item)), "CreateForWindow")
        finally:
            _release(interop)
        size = _Size()
        _ok(_call(self._item, 7, [C.POINTER(_Size)], C.byref(size)), "item size")
        self.size = (size.Width, size.Height)
        statics = _factory("Windows.Graphics.Capture.Direct3D11CaptureFramePool", _IID_POOL_STATICS2)
        try:
            _ok(_call(statics, 6, [C.c_void_p, C.c_int, C.c_int32, _Size, C.POINTER(C.c_void_p)],
                      self._rt, _BGRA, 2, size, C.byref(self._pool)), "CreateFreeThreaded")
        finally:
            _release(statics)
        _ok(_call(self._pool, 10, [C.c_void_p, C.POINTER(C.c_void_p)], self._item,
                  C.byref(self._session)), "CreateCaptureSession")
        # No yellow frame round the desktop, or no capture at all.
        third = _query(self._session, _IID_SESSION3)
        try:
            _ok(_call(third, 7, [C.c_bool], False), "IsBorderRequired")
        finally:
            _release(third)
        try:
            second = _query(self._session, _IID_SESSION2)
            _call(second, 7, [C.c_bool], False)                   # the cursor left out
            _release(second)
        except OSError:
            pass
        _ok(_call(self._session, 6, []), "StartCapture")

    def latest(self) -> Optional[Frame]:
        """The newest frame since the last call, or None if nothing new was
        drawn; any older one waiting is dropped. When the window has changed
        size, the pool is remade to it, and frames are right from the next."""
        newest = None
        while True:
            got = C.c_void_p()
            if _call(self._pool, 7, [C.POINTER(C.c_void_p)], C.byref(got)) < 0 or not got.value:
                break
            if newest is not None:
                _close(newest)
                _release(newest)
            newest = got
        if newest is None:
            return None
        frame = Frame(newest)
        if frame.size != self.size:
            self.size = frame.size
            _call(self._pool, 6, [C.c_void_p, C.c_int, C.c_int32, _Size], self._rt, _BGRA, 2,
                  _Size(*frame.size))                              # Recreate
            self._staging.clear()
        return frame

    def origin(self) -> Tuple[int, int]:
        """The captured window's top-left on the virtual screen."""
        r = W.RECT()
        _user.GetWindowRect(self.hwnd, C.byref(r))
        return r.left, r.top

    def read(self, frame: Frame, rects: Sequence[Tuple[int, int, int, int]],
             at: Optional[Tuple[int, int]] = None) -> List[Optional[Tuple[Tuple[int, int, int, int], QImage]]]:
        """Patches of ``frame``, one for each of ``rects`` (x, y, w, h on the
        virtual screen) in order: (the rect clipped to the frame, an opaque
        Format_RGB32 image), or None for one off it. ``at`` is the window's
        top-left, if known."""
        left, top = at if at is not None else self.origin()
        fw, fh = frame.size
        out = []
        for x, y, w, h in rects:
            x0, y0 = max(x, left), max(y, top)
            x1, y1 = min(x + w, left + fw), min(y + h, top + fh)
            if x1 - x0 < 1 or y1 - y0 < 1:
                out.append(None)
                continue
            out.append(((x0, y0, x1 - x0, y1 - y0),
                        self._copy(frame, x0 - left, y0 - top, x1 - x0, y1 - y0)))
        return out

    def whole(self, frame: Frame) -> QImage:
        """The whole frame, opaque Format_RGB32."""
        return self._copy(frame, 0, 0, frame.size[0], frame.size[1])

    def _copy(self, frame: Frame, x: int, y: int, w: int, h: int) -> QImage:
        staging = self._staging.get((w, h))
        if staging is None:
            desc = _TexDesc(w, h, 1, 1, _BGRA, 1, 0, _STAGING, 0, _CPU_READ, 0)
            staging = C.c_void_p()
            _ok(_call(self._device, 5, [C.POINTER(_TexDesc), C.c_void_p, C.POINTER(C.c_void_p)],
                      C.byref(desc), None, C.byref(staging)), "staging texture")
            self._staging[(w, h)] = staging
        box = _Box(x, y, 0, x + w, y + h, 1)
        _call(self._context, 46, [C.c_void_p, C.c_uint, C.c_uint, C.c_uint, C.c_uint, C.c_void_p,
                                  C.c_uint, C.POINTER(_Box)],
              staging, 0, 0, 0, 0, frame.texture, 0, C.byref(box), restype=None)
        mapped = _Mapped()
        _ok(_call(self._context, 14, [C.c_void_p, C.c_uint, C.c_uint, C.c_uint, C.POINTER(_Mapped)],
                  staging, 0, _MAP_READ, 0, C.byref(mapped)), "Map")
        try:
            img = QImage(w, h, QImage.Format_RGB32)
            line = img.bytesPerLine()
            try:
                import numpy as np
            except ImportError:
                np = None
            if np is not None:
                # One copy, rows at once, and the alpha set: the compositor's
                # is 255 here, but Format_RGB32 must be, or the glass built
                # from it comes out clear.
                src = np.ctypeslib.as_array((C.c_ubyte * (mapped.RowPitch * h)).from_address(
                    mapped.pData)).reshape(h, mapped.RowPitch)
                dst = np.frombuffer(img.bits(), np.uint8).reshape(h, line)
                dst[:, :w * 4] = src[:, :w * 4]
                dst[:, 3:w * 4:4] = 255
                del src, dst
            else:
                dest = (C.c_ubyte * (line * h)).from_buffer(img.bits())
                base = C.addressof(dest)
                for row in range(h):
                    C.memmove(base + row * line, mapped.pData + row * mapped.RowPitch, w * 4)
                del dest
        finally:
            _call(self._context, 15, [C.c_void_p, C.c_uint], staging, 0, restype=None)
        return img

    def close(self) -> None:
        """Stop capturing and let everything go."""
        _close(self._session)
        _close(self._pool)
        for staging in self._staging.values():
            _release(staging)
        self._staging.clear()
        for obj in (self._session, self._pool, self._item, self._rt, self._dxgi,
                    self._context, self._device):
            _release(obj)
