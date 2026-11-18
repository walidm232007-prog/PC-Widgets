"""
gpuglass.py
===========
Liquid glass bent on the graphics card, from the frame Windows' capture
(wgc.py) already holds there. Live glass (glass.py) uses it to follow a live
wallpaper at 60 frames a second without spending a core on it.

On the CPU each moving card cost about 2 ms a frame: its patch halved and
frosted, then bent (lens.py) - fine at ten frames a second, a core at sixty.
Here three compute shaders do the same work on the captured frame, still on
the card:

- ``down`` halves the box round all the cards' patches, each pixel the mean
  of four, as Qt's smooth scaling halves the clear copy;
- ``blur`` frosts it with the box passes Settings' Blur asks for, edges held,
  as glass._box_blur does. Once for every card in view: inside a card it
  comes out as frosting the card's own patch would, since PATCH_MARGIN keeps
  a patch's edges further out than the frost reaches;
- ``bend`` looks through each card's rim with lens.py's own field - the same
  pull for every point, red further than blue, the same bilinear sampling - so
  the pane comes out as lens.bend would draw it, to within a level.

The panes are bent side by side into one texture and come back in one read,
on lens.STEP's grid: 15 KB for a small card. A frame is some sixty calls into
Direct3D for six cards, each set up once (a _Layout) and kept until a card
moves or hides.

No package is needed: the shaders are compiled by d3dcompiler_47, part of
Windows, and everything else goes through Direct3D 11's vtables, on the device
the capture made. A GpuGlass is used from the capture's thread only.
"""

from __future__ import annotations

import ctypes as C
from collections import OrderedDict
from typing import Dict, List, Sequence, Tuple

import numpy as np
from PySide6.QtGui import QImage

from . import lens, wgc
from .wgc import _Box, _call, _Mapped, _ok, _release, _TexDesc

_SHADERS = r"""
Texture2D<float4> Src : register(t0);
Texture2D<float2> Field : register(t1);
RWTexture2D<float4> Dst : register(u0);
RWTexture2D<uint> Pane : register(u0);

cbuffer Params : register(b0)
{
    uint dw; uint dh; uint sw; uint sh;
    int radius; uint across; uint last; uint ax;
    float ex; float ey; float step; float scale;
    uint ay; uint pad0; uint pad1; uint pad2;
};

[numthreads(8, 8, 1)]
void down(uint3 id : SV_DispatchThreadID)
{
    if (id.x >= dw || id.y >= dh) return;
    int2 edge = int2(sw - 1, sh - 1);
    int2 s = int2(id.xy) * 2;
    float4 a = Src.Load(int3(min(s, edge), 0));
    float4 b = Src.Load(int3(min(s + int2(1, 0), edge), 0));
    float4 c = Src.Load(int3(min(s + int2(0, 1), edge), 0));
    float4 d = Src.Load(int3(min(s + int2(1, 1), edge), 0));
    Dst[id.xy] = floor((a + b + c + d) * (255.0 / 4.0) + 0.5);
}

[numthreads(8, 8, 1)]
void blur(uint3 id : SV_DispatchThreadID)
{
    if (id.x >= dw || id.y >= dh) return;
    float4 sum = 0;
    for (int k = -radius; k <= radius; k++)
    {
        int2 p = across ? int2(clamp(int(id.x) + k, 0, int(dw) - 1), id.y)
                        : int2(id.x, clamp(int(id.y) + k, 0, int(dh) - 1));
        sum += Src.Load(int3(p, 0));
    }
    float4 v = sum * (1.0 / (2 * radius + 1));
    Dst[id.xy] = last ? floor(clamp(v, 0.0, 255.0)) : v;
}

float tap(int c, float x, float y)
{
    x = clamp(x, 0.0, sw - 1.001);
    y = clamp(y, 0.0, sh - 1.001);
    int x0 = int(x);
    int y0 = int(y);
    float fx = x - x0;
    float fy = y - y0;
    float a = Src.Load(int3(x0, y0, 0))[c];
    float b = Src.Load(int3(x0 + 1, y0, 0))[c];
    float e = Src.Load(int3(x0, y0 + 1, 0))[c];
    float f = Src.Load(int3(x0 + 1, y0 + 1, 0))[c];
    float top = a + (b - a) * fx;
    return top + (e + (f - e) * fx - top) * fy;
}

[numthreads(8, 8, 1)]
void bend(uint3 id : SV_DispatchThreadID)
{
    if (id.x >= dw || id.y >= dh) return;
    float gx = (id.x * step + step / 2.0 + ex) / scale - 0.5;
    float gy = (id.y * step + step / 2.0 + ey) / scale - 0.5;
    float2 pull = Field.Load(int3(id.xy, 0)) / scale;
    float r = tap(0, gx + pull.x * SPLIT_R, gy + pull.y * SPLIT_R);
    float g = tap(1, gx + pull.x * SPLIT_G, gy + pull.y * SPLIT_G);
    float b = tap(2, gx + pull.x * SPLIT_B, gy + pull.y * SPLIT_B);
    uint3 v = uint3(clamp(floor(float3(r, g, b) + 0.5), 0.0, 255.0));
    Pane[uint2(ax + id.x, ay + id.y)] = v.b | (v.g << 8) | (v.r << 16) | (255u << 24);
}
"""

_F32X4, _F32X2, _U32, _BGRA = 2, 16, 42, 87
_SRV, _UAV, _CBUFFER = 0x8, 0x80, 0x4
ATLAS_WIDTH = 2048         # px: the panes lie side by side in rows this wide
ROOM = 256                 # px: the frost's textures grow by this much at a time
LAYOUTS = 3                # layouts kept: a card being dragged makes a new one each frame
FIELDS = 24                # bend fields kept beyond those in use
_bytecode: Dict[str, bytes] = {}

Card = Tuple[Tuple[int, int, int, int], Tuple[int, int, int, int], float]


class _BufDesc(C.Structure):
    _fields_ = [("ByteWidth", C.c_uint), ("Usage", C.c_uint), ("BindFlags", C.c_uint),
                ("CPUAccessFlags", C.c_uint), ("MiscFlags", C.c_uint),
                ("StructureByteStride", C.c_uint)]


class _Init(C.Structure):
    _fields_ = [("pSysMem", C.c_void_p), ("SysMemPitch", C.c_uint), ("SysMemSlicePitch", C.c_uint)]


class _Params(C.Structure):
    _fields_ = [("dw", C.c_uint), ("dh", C.c_uint), ("sw", C.c_uint), ("sh", C.c_uint),
                ("radius", C.c_int), ("across", C.c_uint), ("last", C.c_uint), ("ax", C.c_uint),
                ("ex", C.c_float), ("ey", C.c_float), ("step", C.c_float), ("scale", C.c_float),
                ("ay", C.c_uint), ("pad0", C.c_uint), ("pad1", C.c_uint), ("pad2", C.c_uint)]


def _compile(entry: str) -> bytes:
    """One shader's bytecode, compiled once a run by Windows' own compiler."""
    if entry in _bytecode:
        return _bytecode[entry]
    compiler = C.WinDLL("d3dcompiler_47")
    compiler.D3DCompile.argtypes = [C.c_char_p, C.c_size_t, C.c_char_p, C.c_void_p, C.c_void_p,
                                    C.c_char_p, C.c_char_p, C.c_uint, C.c_uint,
                                    C.POINTER(C.c_void_p), C.POINTER(C.c_void_p)]
    compiler.D3DCompile.restype = wgc.HRESULT
    split = lens.SPLIT
    source = (f"#define SPLIT_R {split[0]!r}\n#define SPLIT_G {split[1]!r}\n"
              f"#define SPLIT_B {split[2]!r}\n" + _SHADERS).encode("ascii")
    code, errors = C.c_void_p(), C.c_void_p()
    hr = compiler.D3DCompile(source, len(source), b"gpuglass", None, None, entry.encode(),
                             b"cs_5_0", 1 << 15, 0, C.byref(code), C.byref(errors))
    if hr < 0:
        text = ""
        if errors.value:
            text = C.string_at(_call(errors, 3, [], restype=C.c_void_p)).decode(errors="replace")
            _release(errors)
        raise OSError(f"compiling {entry} failed: {text.strip()[:300]}")
    _release(errors)
    data = C.string_at(_call(code, 3, [], restype=C.c_void_p), _call(code, 4, [], restype=C.c_size_t))
    _release(code)
    _bytecode[entry] = data
    return data


def _bounds(cards: Sequence[Card]) -> Tuple[int, int, int, int]:
    """The box round the cards' patches: (x, y, w, h) in the frame's pixels."""
    x0 = min(p[0] for p, _c, _r in cards)
    y0 = min(p[1] for p, _c, _r in cards)
    x1 = max(p[0] + p[2] for p, _c, _r in cards)
    y1 = max(p[1] + p[3] for p, _c, _r in cards)
    return x0, y0, x1 - x0, y1 - y0


class _Owner:
    """Something holding Direct3D objects, let go of together."""

    def __init__(self) -> None:
        self.objects: List[C.c_void_p] = []

    def keep(self, obj: C.c_void_p) -> C.c_void_p:
        self.objects.append(obj)
        return obj

    def close(self) -> None:
        for obj in reversed(self.objects):
            _release(obj)
        self.objects = []


class _Frost(_Owner):
    """The textures a frame's cards are frosted in: a copy of the box round
    their patches, and two half-size ones the passes go back and forth
    between. Kept for as long as the box fits in them."""

    def __init__(self, gpu: "GpuGlass", w: int, h: int) -> None:
        super().__init__()
        self.w, self.h = w, h
        try:
            hw, hh = max(2, w // 2), max(2, h // 2)
            self.src = self.keep(gpu._texture(w, h, _BGRA, _SRV))
            self.src_view = self.keep(gpu._view(self.src))
            tex = [self.keep(gpu._texture(hw, hh, _F32X4, _SRV | _UAV)) for _ in range(2)]
            self.views = [self.keep(gpu._view(t)) for t in tex]
            self.writes = [self.keep(gpu._write(t)) for t in tex]
        except Exception:
            self.close()
            raise


class _Field(_Owner):
    """A card's bend field on the graphics card: each point's pull, zero where
    lens.bend looks straight through."""

    def __init__(self, gpu: "GpuGlass", field: lens.Field) -> None:
        super().__init__()
        self.cols, self.rows = field.cols, field.rows
        pulls = np.zeros((field.rows, field.cols, 2), np.float32)
        pulls[field.bent_rows, field.bent_cols, 0] = field.pull_x
        pulls[field.bent_rows, field.bent_cols, 1] = field.pull_y
        try:
            tex = self.keep(gpu._texture(field.cols, field.rows, _F32X2, _SRV, pulls,
                                         field.cols * 8))
            self.view = self.keep(gpu._view(tex))
        except Exception:
            self.close()
            raise


class _Layout(_Owner):
    """A frame's cards as the graphics card works on them: the box round their
    patches, each pass with its constants, and where each card's pane lies in
    the texture they're all bent into. The same from frame to frame until a
    card moves or hides, or Blur changes."""

    def __init__(self, gpu: "GpuGlass", frost_tex: _Frost, cards: Sequence[Card],
                 frost: Tuple[int, int], step: int) -> None:
        super().__init__()
        try:
            self._build(gpu, frost_tex, cards, frost, step)
        except Exception:
            self.close()
            raise

    def _build(self, gpu, frost_tex, cards, frost, step) -> None:
        bx, by, bw, bh = _bounds(cards)
        hw, hh = max(2, bw // 2), max(2, bh // 2)
        self.box = _Box(bx, by, 0, bx + bw, by + bh, 1)

        def run(entry, reads, write, params, size):
            return gpu._pass(entry, reads, write, self.keep(gpu._buffer(params)), size)

        # Halved, then frosted: once, for every card.
        passes = [run("down", [frost_tex.src_view], frost_tex.writes[0],
                      _Params(dw=hw, dh=hh, sw=bw, sh=bh), (hw, hh))]
        radius, count = frost
        at = 0
        for n in range(count if radius else 0):
            for across in (1, 0):
                last = int(n == count - 1 and not across)
                passes.append(run("blur", [frost_tex.views[at]], frost_tex.writes[1 - at],
                                  _Params(dw=hw, dh=hh, sw=hw, sh=hh, radius=radius,
                                          across=across, last=last), (hw, hh)))
                at = 1 - at
        # Each card's pane, in rows across one texture.
        self.field_keys = [gpu._field_key(card[2], card[3], r, step) for _p, card, r in cards]
        fields = [gpu._field(key) for key in self.field_keys]
        self.spots, x, y, row, width = [], 0, 0, 0, 1
        for f in fields:
            if x and x + f.cols > ATLAS_WIDTH:
                x, y, row = 0, y + row, 0
            self.spots.append((x, y, f.cols, f.rows))
            x += f.cols
            row, width = max(row, f.rows), max(width, x)
        self.height = max(1, y + row)
        self.atlas = self.keep(gpu._texture(width, self.height, _U32, _UAV))
        atlas_write = self.keep(gpu._write(self.atlas))
        self.staging = self.keep(gpu._staging(width, self.height, _U32))
        for (_patch, card, _r), f, (ax, ay, cols, rows) in zip(cards, fields, self.spots):
            passes.append(run("bend", [frost_tex.views[at], f.view], atlas_write,
                              _Params(dw=cols, dh=rows, sw=hw, sh=hh, ax=ax, ay=ay,
                                      ex=float(card[0] - bx), ey=float(card[1] - by),
                                      step=float(step), scale=2.0), (cols, rows)))
        self.passes = passes


class GpuGlass:
    """Bends cards' glass on the graphics card of a wgc.Capture."""

    def __init__(self, capture: "wgc.Capture") -> None:
        self._device, self._context = capture._device, capture._context
        self._shaders: Dict[str, C.c_void_p] = {}
        self._fields: Dict[Tuple, _Field] = {}
        self._layouts: "OrderedDict[Tuple, _Layout]" = OrderedDict()
        self._frost = None
        self._current = self._writing = None   # what's set already, not set again
        try:
            for entry in ("down", "blur", "bend"):
                code = _compile(entry)
                shader = C.c_void_p()
                _ok(_call(self._device, 18, [C.c_char_p, C.c_size_t, C.c_void_p, C.POINTER(C.c_void_p)],
                          code, len(code), None, C.byref(shader)), f"compute shader {entry}")
                self._shaders[entry] = shader
        except Exception:
            self.close()
            raise

    # -- resources ------------------------------------------------------- #
    def _texture(self, w, h, fmt, bind, data=None, pitch=0) -> C.c_void_p:
        desc = _TexDesc(w, h, 1, 1, fmt, 1, 0, 0, bind, 0, 0)
        init = _Init(data.ctypes.data, pitch, 0) if data is not None else None
        out = C.c_void_p()
        _ok(_call(self._device, 5, [C.POINTER(_TexDesc), C.c_void_p, C.POINTER(C.c_void_p)],
                  C.byref(desc), C.byref(init) if init is not None else None, C.byref(out)), "texture")
        return out

    def _staging(self, w, h, fmt) -> C.c_void_p:
        desc = _TexDesc(w, h, 1, 1, fmt, 1, 0, 3, 0, 0x20000, 0)
        out = C.c_void_p()
        _ok(_call(self._device, 5, [C.POINTER(_TexDesc), C.c_void_p, C.POINTER(C.c_void_p)],
                  C.byref(desc), None, C.byref(out)), "staging texture")
        return out

    def _view(self, resource) -> C.c_void_p:
        out = C.c_void_p()
        _ok(_call(self._device, 7, [C.c_void_p, C.c_void_p, C.POINTER(C.c_void_p)],
                  resource, None, C.byref(out)), "shader resource view")
        return out

    def _write(self, resource) -> C.c_void_p:
        out = C.c_void_p()
        _ok(_call(self._device, 8, [C.c_void_p, C.c_void_p, C.POINTER(C.c_void_p)],
                  resource, None, C.byref(out)), "unordered access view")
        return out

    def _buffer(self, params: _Params) -> C.c_void_p:
        """A constant buffer holding ``params``, made once for a pass of a
        layout: its passes never change, so nothing is uploaded each frame."""
        desc = _BufDesc(C.sizeof(_Params), 0, _CBUFFER, 0, 0, 0)
        init = _Init(C.addressof(params), 0, 0)
        out = C.c_void_p()
        _ok(_call(self._device, 3, [C.POINTER(_BufDesc), C.c_void_p, C.POINTER(C.c_void_p)],
                  C.byref(desc), C.byref(init), C.byref(out)), "constant buffer")
        return out

    @staticmethod
    def _field_key(w: int, h: int, radius: float, step: int) -> Tuple:
        return (int(w), int(h), float(radius), int(step))

    def _field(self, key: Tuple) -> _Field:
        """A card's bend field, shared by cards of a size and radius."""
        f = self._fields.get(key)
        if f is None:
            f = self._fields[key] = _Field(self, lens.field(*key))
        return f

    def _frost_for(self, w: int, h: int) -> _Frost:
        """Frost textures a box of w x h fits in: the last ones, or bigger."""
        frost = self._frost
        if frost is not None and frost.w >= w and frost.h >= h:
            return frost
        # The layouts read and write the old ones: they go with them.
        self._drop_layouts()
        if frost is not None:
            frost.close()
            self._frost = None
        self._current = self._writing = None
        size = lambda n: -(-max(n, 2) // ROOM) * ROOM     # noqa: E731
        self._frost = _Frost(self, size(w), size(h))
        return self._frost

    def _layout(self, cards: Tuple[Card, ...], frost: Tuple[int, int], step: int) -> _Layout:
        key = (cards, frost, step)
        layout = self._layouts.get(key)
        if layout is not None:
            self._layouts.move_to_end(key)
            return layout
        _bx, _by, bw, bh = _bounds(cards)
        layout = _Layout(self, self._frost_for(bw, bh), cards, frost, step)
        self._layouts[key] = layout
        while len(self._layouts) > LAYOUTS:
            self._layouts.popitem(last=False)[1].close()
        if len(self._fields) > FIELDS:
            used = {k for kept in self._layouts.values() for k in kept.field_keys}
            for k in [k for k in self._fields if k not in used]:
                self._fields.pop(k).close()
        return layout

    def _drop_layouts(self) -> None:
        for layout in self._layouts.values():
            layout.close()
        self._layouts.clear()

    # -- one dispatch ---------------------------------------------------- #
    @staticmethod
    def _pass(entry: str, reads, write, buffer, size: Tuple[int, int]) -> Tuple:
        """A pass ready to run: its views and constants as the calls take them."""
        srvs = (C.c_void_p * 2)(*([r.value for r in reads] + [None] * (2 - len(reads))))
        return (entry, write.value, (C.c_void_p * 1)(write.value), srvs,
                (C.c_void_p * 1)(buffer.value), ((size[0] + 7) // 8, (size[1] + 7) // 8))

    def _run(self, entry: str, writing: int, write, reads, buffer, groups: Tuple[int, int]) -> None:
        ctx = self._context
        if self._current != entry:
            _call(ctx, 69, (C.c_void_p, C.c_void_p, C.c_uint), self._shaders[entry], None, 0,
                  restype=None)                                                   # CSSetShader
            self._current = entry
        # The texture to write first, then the ones to read: binding one to
        # write lets go of it where it was being read, and one the last pass
        # wrote is then free to be read - in the other order Direct3D would
        # refuse it, still bound for writing. The cards' bends all write one.
        if self._writing != writing:
            _call(ctx, 68, (C.c_uint, C.c_uint, C.c_void_p, C.c_void_p), 0, 1, write, None,
                  restype=None)                                                   # CSSetUnorderedAccessViews
            self._writing = writing
        _call(ctx, 67, (C.c_uint, C.c_uint, C.c_void_p), 0, 2, reads, restype=None)
        _call(ctx, 71, (C.c_uint, C.c_uint, C.c_void_p), 0, 1, buffer, restype=None)
        _call(ctx, 41, (C.c_uint, C.c_uint, C.c_uint), groups[0], groups[1], 1, restype=None)

    # -- panes ----------------------------------------------------------- #
    def pane(self, texture, patch: Tuple[int, int, int, int], card: Tuple[int, int, int, int],
             radius: float, frost: Tuple[int, int], step: int = lens.STEP) -> QImage:
        """A card's glass, as lens.bend draws it: ``texture`` is the captured
        frame, ``patch`` (x, y, w, h in the frame's pixels, from an even pixel
        of the clear copy's grid) the part read round the card, and ``card``
        the card's glass in the same pixels. Returns a Format_RGB32 image on
        ``step``'s grid."""
        return self.panes(texture, [(patch, card, radius)], frost, step)[0]

    def panes(self, texture, cards: Sequence[Card], frost: Tuple[int, int],
              step: int = lens.STEP) -> List[QImage]:
        """pane() for each of ``cards`` - (patch, card, radius) - at once: the
        box round their patches frosted once, each card bent from it, and all
        read back together, so the CPU waits on the graphics card once a
        frame."""
        if not cards:
            return []
        cards = tuple((tuple(p), tuple(c), float(r)) for p, c, r in cards)
        layout = self._layout(cards, (int(frost[0]), int(frost[1])), int(step))
        ctx = self._context
        _call(ctx, 46, (C.c_void_p, C.c_uint, C.c_uint, C.c_uint, C.c_uint, C.c_void_p, C.c_uint,
                        C.POINTER(_Box)), self._frost.src, 0, 0, 0, 0, texture, 0,
              C.byref(layout.box), restype=None)                                  # CopySubresourceRegion
        for p in layout.passes:
            self._run(*p)
        _call(ctx, 47, (C.c_void_p, C.c_void_p), layout.staging, layout.atlas, restype=None)  # CopyResource
        return self._read(layout)

    def _read(self, layout: _Layout) -> List[QImage]:
        ctx = self._context
        mapped = _Mapped()
        _ok(_call(ctx, 14, (C.c_void_p, C.c_uint, C.c_uint, C.c_uint, C.POINTER(_Mapped)),
                  layout.staging, 0, 1, 0, C.byref(mapped)), "Map")
        try:
            src = np.ctypeslib.as_array((C.c_ubyte * (mapped.RowPitch * layout.height)).from_address(
                mapped.pData)).reshape(layout.height, mapped.RowPitch)
            panes = []
            for ax, ay, cols, rows in layout.spots:
                img = QImage(cols, rows, QImage.Format_RGB32)
                line = img.bytesPerLine()
                np.frombuffer(img.bits(), np.uint8).reshape(rows, line)[:, :cols * 4] = \
                    src[ay:ay + rows, ax * 4:(ax + cols) * 4]
                panes.append(img)
            del src
        finally:
            _call(ctx, 15, (C.c_void_p, C.c_uint), layout.staging, 0, restype=None)
        return panes

    def close(self) -> None:
        self._drop_layouts()
        for f in self._fields.values():
            f.close()
        self._fields.clear()
        if self._frost is not None:
            self._frost.close()
            self._frost = None
        for shader in self._shaders.values():
            _release(shader)
        self._shaders.clear()
        self._current = self._writing = None
