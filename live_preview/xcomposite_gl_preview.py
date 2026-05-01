#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ctypes
import logging
import sys

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, GdkX11


log = logging.getLogger("xcomposite_gl_preview")

libX11 = ctypes.CDLL("libX11.so.6")
libXcomposite = ctypes.CDLL("libXcomposite.so.1")
libGL = ctypes.CDLL("libGL.so.1")
try:
    libXfixes = ctypes.CDLL("libXfixes.so.3")
except OSError:
    libXfixes = None
_CTYPES_READY = False

Window = ctypes.c_ulong
Pixmap = ctypes.c_ulong
GLXFBConfig = ctypes.c_void_p
GLXPixmap = ctypes.c_ulong
DisplayP = ctypes.c_void_p
XserverRegion = ctypes.c_ulong


class XWindowAttributes(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
        ("border_width", ctypes.c_int),
        ("depth", ctypes.c_int),
        ("visual", ctypes.c_void_p),
        ("root", Window),
        ("class_", ctypes.c_int),
        ("bit_gravity", ctypes.c_int),
        ("win_gravity", ctypes.c_int),
        ("backing_store", ctypes.c_int),
        ("backing_planes", ctypes.c_ulong),
        ("backing_pixel", ctypes.c_ulong),
        ("save_under", ctypes.c_int),
        ("colormap", ctypes.c_ulong),
        ("map_installed", ctypes.c_int),
        ("map_state", ctypes.c_int),
        ("all_event_masks", ctypes.c_long),
        ("your_event_mask", ctypes.c_long),
        ("do_not_propagate_mask", ctypes.c_long),
        ("override_redirect", ctypes.c_int),
        ("screen", ctypes.c_void_p),
    ]


class Visual(ctypes.Structure):
    _fields_ = [
        ("ext_data", ctypes.c_void_p),
        ("visualid", ctypes.c_ulong),
        ("class_", ctypes.c_int),
        ("red_mask", ctypes.c_ulong),
        ("green_mask", ctypes.c_ulong),
        ("blue_mask", ctypes.c_ulong),
        ("bits_per_rgb", ctypes.c_int),
        ("map_entries", ctypes.c_int),
    ]


# XComposite constants
COMPOSITE_REDIRECT_AUTOMATIC = 0
SHAPE_INPUT = 2

# GL constants
GL_COLOR_BUFFER_BIT = 0x00004000
GL_TEXTURE_2D = 0x0DE1
GL_TRIANGLE_STRIP = 0x0005
GL_LINEAR = 0x2601
GL_TEXTURE_MIN_FILTER = 0x2801
GL_TEXTURE_MAG_FILTER = 0x2800
GL_VENDOR = 0x1F00
GL_RENDERER = 0x1F01
GL_VERSION = 0x1F02
GL_TEXTURE0 = 0x84C0
GL_RGB = 0x1907
GL_UNSIGNED_BYTE = 0x1401
GL_ARRAY_BUFFER = 0x8892
GL_STATIC_DRAW = 0x88E4
GL_FLOAT = 0x1406
GL_FALSE = 0
GL_VERTEX_SHADER = 0x8B31
GL_FRAGMENT_SHADER = 0x8B30
GL_COMPILE_STATUS = 0x8B81
GL_LINK_STATUS = 0x8B82
GL_INFO_LOG_LENGTH = 0x8B84

# GLX constants
GLX_RGBA_BIT = 0x00000001
GLX_PIXMAP_BIT = 0x00000002
GLX_DRAWABLE_TYPE = 0x8010
GLX_RENDER_TYPE = 0x8011
GLX_X_VISUAL_TYPE = 0x22
GLX_TRUE_COLOR = 0x8002
GLX_RED_SIZE = 8
GLX_GREEN_SIZE = 9
GLX_BLUE_SIZE = 10
GLX_ALPHA_SIZE = 11
GLX_DEPTH_SIZE = 12
GLX_VISUAL_ID = 0x800B
GLX_BIND_TO_TEXTURE_RGB_EXT = 0x20D0
GLX_BIND_TO_TEXTURE_RGBA_EXT = 0x20D1
GLX_TEXTURE_FORMAT_EXT = 0x20D5
GLX_TEXTURE_TARGET_EXT = 0x20D6
GLX_TEXTURE_FORMAT_RGB_EXT = 0x20D9
GLX_TEXTURE_FORMAT_RGBA_EXT = 0x20DA
GLX_TEXTURE_2D_EXT = 0x20DC
GLX_FRONT_LEFT_EXT = 0x20DE


def _setup_ctypes() -> None:
    global _CTYPES_READY
    if _CTYPES_READY:
        return
    libX11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    libX11.XOpenDisplay.restype = DisplayP
    libX11.XCloseDisplay.argtypes = [DisplayP]
    libX11.XDefaultScreen.argtypes = [DisplayP]
    libX11.XDefaultScreen.restype = ctypes.c_int
    libX11.XGetWindowAttributes.argtypes = [DisplayP, Window, ctypes.POINTER(XWindowAttributes)]
    libX11.XGetWindowAttributes.restype = ctypes.c_int
    libX11.XFreePixmap.argtypes = [DisplayP, Pixmap]
    libX11.XSync.argtypes = [DisplayP, ctypes.c_int]
    libX11.XFlush.argtypes = [DisplayP]

    if libXfixes is not None:
        libXfixes.XFixesCreateRegion.argtypes = [DisplayP, ctypes.c_void_p, ctypes.c_int]
        libXfixes.XFixesCreateRegion.restype = XserverRegion
        libXfixes.XFixesSetWindowShapeRegion.argtypes = [
            DisplayP, Window, ctypes.c_int, ctypes.c_int, ctypes.c_int, XserverRegion
        ]
        libXfixes.XFixesDestroyRegion.argtypes = [DisplayP, XserverRegion]

    libXcomposite.XCompositeQueryExtension.argtypes = [
        DisplayP, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)
    ]
    libXcomposite.XCompositeQueryExtension.restype = ctypes.c_int
    libXcomposite.XCompositeQueryVersion.argtypes = [
        DisplayP, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)
    ]
    libXcomposite.XCompositeQueryVersion.restype = ctypes.c_int
    libXcomposite.XCompositeRedirectWindow.argtypes = [DisplayP, Window, ctypes.c_int]
    libXcomposite.XCompositeUnredirectWindow.argtypes = [DisplayP, Window, ctypes.c_int]
    libXcomposite.XCompositeNameWindowPixmap.argtypes = [DisplayP, Window]
    libXcomposite.XCompositeNameWindowPixmap.restype = Pixmap

    libGL.glXChooseFBConfig.argtypes = [
        DisplayP, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)
    ]
    libGL.glXChooseFBConfig.restype = ctypes.POINTER(GLXFBConfig)
    libGL.glXGetFBConfigAttrib.argtypes = [
        DisplayP, GLXFBConfig, ctypes.c_int, ctypes.POINTER(ctypes.c_int)
    ]
    libGL.glXGetFBConfigAttrib.restype = ctypes.c_int
    libGL.glXCreatePixmap.argtypes = [
        DisplayP, GLXFBConfig, Pixmap, ctypes.POINTER(ctypes.c_int)
    ]
    libGL.glXCreatePixmap.restype = GLXPixmap
    libGL.glXDestroyPixmap.argtypes = [DisplayP, GLXPixmap]
    libGL.glXGetProcAddressARB.argtypes = [ctypes.c_char_p]
    libGL.glXGetProcAddressARB.restype = ctypes.c_void_p

    libGL.glGenTextures.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
    libGL.glBindTexture.argtypes = [ctypes.c_uint, ctypes.c_uint]
    libGL.glTexParameteri.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_int]
    libGL.glGetString.argtypes = [ctypes.c_uint]
    libGL.glGetString.restype = ctypes.c_char_p
    libGL.glGetError.argtypes = []
    libGL.glGetError.restype = ctypes.c_uint
    libGL.glEnable.argtypes = [ctypes.c_uint]
    libGL.glClearColor.argtypes = [ctypes.c_float, ctypes.c_float, ctypes.c_float, ctypes.c_float]
    libGL.glClear.argtypes = [ctypes.c_uint]
    libGL.glViewport.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    libGL.glReadPixels.argtypes = [
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p,
    ]
    libGL.glDeleteTextures.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
    libGL.glActiveTexture.argtypes = [ctypes.c_uint]
    libGL.glCreateShader.argtypes = [ctypes.c_uint]
    libGL.glCreateShader.restype = ctypes.c_uint
    libGL.glShaderSource.argtypes = [
        ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(ctypes.c_int)
    ]
    libGL.glCompileShader.argtypes = [ctypes.c_uint]
    libGL.glGetShaderiv.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_int)]
    libGL.glGetShaderInfoLog.argtypes = [
        ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.c_char_p
    ]
    libGL.glDeleteShader.argtypes = [ctypes.c_uint]
    libGL.glCreateProgram.argtypes = []
    libGL.glCreateProgram.restype = ctypes.c_uint
    libGL.glAttachShader.argtypes = [ctypes.c_uint, ctypes.c_uint]
    libGL.glLinkProgram.argtypes = [ctypes.c_uint]
    libGL.glGetProgramiv.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_int)]
    libGL.glGetProgramInfoLog.argtypes = [
        ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.c_char_p
    ]
    libGL.glDeleteProgram.argtypes = [ctypes.c_uint]
    libGL.glUseProgram.argtypes = [ctypes.c_uint]
    libGL.glGetUniformLocation.argtypes = [ctypes.c_uint, ctypes.c_char_p]
    libGL.glGetUniformLocation.restype = ctypes.c_int
    libGL.glUniform1i.argtypes = [ctypes.c_int, ctypes.c_int]
    libGL.glGenVertexArrays.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
    libGL.glBindVertexArray.argtypes = [ctypes.c_uint]
    libGL.glDeleteVertexArrays.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
    libGL.glGenBuffers.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
    libGL.glBindBuffer.argtypes = [ctypes.c_uint, ctypes.c_uint]
    libGL.glBufferData.argtypes = [ctypes.c_uint, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_uint]
    libGL.glDeleteBuffers.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
    libGL.glEnableVertexAttribArray.argtypes = [ctypes.c_uint]
    libGL.glVertexAttribPointer.argtypes = [
        ctypes.c_uint, ctypes.c_int, ctypes.c_uint, ctypes.c_ubyte,
        ctypes.c_int, ctypes.c_void_p,
    ]
    libGL.glDrawArrays.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_int]
    _CTYPES_READY = True


class LivePreview(Gtk.Window):
    def __init__(self, xid: int, fps: int, *, overlay: bool = False) -> None:
        _setup_ctypes()
        if overlay:
            super().__init__(type=Gtk.WindowType.POPUP)
            self.set_title(f"XComposite GL preview 0x{xid:x}")
        else:
            super().__init__(title=f"XComposite GL preview 0x{xid:x}")
        self.xid = Window(xid)
        self.fps = max(1, min(60, fps))
        self._frame_interval_ms = max(16, int(1000 / self.fps))
        self._last_frame_ms = 0
        self.overlay = overlay
        self.display: DisplayP | None = None
        self.width = 1
        self.height = 1
        self.depth = 24
        self.visualid = 0
        self.named_pixmap = Pixmap(0)
        self.glx_pixmap = GLXPixmap(0)
        self.texture = ctypes.c_uint(0)
        self.program = ctypes.c_uint(0)
        self.vao = ctypes.c_uint(0)
        self.vbo = ctypes.c_uint(0)
        self.bind_tex_image = None
        self.release_tex_image = None
        self._redirected = False

        self.set_default_size(720, 450)
        if overlay:
            self.set_decorated(False)
            self.set_accept_focus(False)
            self.set_focus_on_map(False)
            self.set_keep_above(True)
            self.set_skip_taskbar_hint(True)
            self.set_skip_pager_hint(True)
            self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
            self.connect("realize", self._make_input_transparent)
            self.connect("map-event", self._make_input_transparent)
            self.connect("size-allocate", self._make_input_transparent)
        self.connect("destroy", self._on_destroy)
        self.area = Gtk.GLArea()
        self.area.set_required_version(3, 3)
        self.area.set_has_depth_buffer(False)
        self.area.connect("realize", self._on_realize)
        self.area.connect("render", self._on_render)
        self.add(self.area)
        GLib.timeout_add(16, self._tick)

    def set_bounds(self, x: int, y: int, width: int, height: int) -> None:
        self.move(int(x), int(y))
        self.resize(max(1, int(width)), max(1, int(height)))

    def set_fps(self, fps: int) -> None:
        self.fps = max(1, min(60, fps))
        self._frame_interval_ms = max(16, int(1000 / self.fps))

    def snapshot_to_png(self, path: str) -> bool:
        if not self.area.get_realized():
            return False
        width = max(1, self.area.get_allocated_width())
        height = max(1, self.area.get_allocated_height())
        try:
            self.area.make_current()
            if self.area.get_error() is not None:
                return False
            self._draw_frame(width, height)
            raw = (ctypes.c_ubyte * (width * height * 3))()
            libGL.glReadPixels(0, 0, width, height, GL_RGB, GL_UNSIGNED_BYTE, raw)
            if self._log_gl_error("snapshot readback"):
                return False
            rowstride = width * 3
            data = bytes(raw)
            flipped = bytearray(len(data))
            for y in range(height):
                src = (height - 1 - y) * rowstride
                dst = y * rowstride
                flipped[dst:dst + rowstride] = data[src:src + rowstride]
            pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
                GLib.Bytes.new(bytes(flipped)),
                GdkPixbuf.Colorspace.RGB,
                False,
                8,
                width,
                height,
                rowstride,
            )
            pixbuf.savev(path, "png", [], [])
            return True
        except Exception:
            log.debug("Failed to snapshot live preview", exc_info=True)
            return False

    def _make_input_transparent(self, *_args) -> None:
        try:
            import cairo
            window = self.get_window()
            if window is not None:
                window.input_shape_combine_region(cairo.Region(), 0, 0)
                self._set_xfixes_empty_input_region(window)
        except Exception:
            log.debug("Failed to make live preview input-transparent", exc_info=True)

    def _set_xfixes_empty_input_region(self, window: GdkX11.X11Window) -> None:
        if libXfixes is None or not hasattr(window, "get_xid"):
            return
        display = libX11.XOpenDisplay(None)
        if not display:
            return
        region = XserverRegion(0)
        try:
            region = libXfixes.XFixesCreateRegion(display, None, 0)
            libXfixes.XFixesSetWindowShapeRegion(
                display, Window(window.get_xid()), SHAPE_INPUT, 0, 0, region
            )
            libX11.XFlush(display)
        finally:
            if region:
                libXfixes.XFixesDestroyRegion(display, region)
            libX11.XCloseDisplay(display)

    def _open_x(self) -> None:
        self.display = libX11.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError("XOpenDisplay failed")
        ev = ctypes.c_int()
        err = ctypes.c_int()
        if not libXcomposite.XCompositeQueryExtension(self.display, ctypes.byref(ev), ctypes.byref(err)):
            raise RuntimeError("XComposite extension is not available")
        major = ctypes.c_int()
        minor = ctypes.c_int()
        libXcomposite.XCompositeQueryVersion(self.display, ctypes.byref(major), ctypes.byref(minor))
        log.info("XComposite %d.%d available", major.value, minor.value)
        attrs = XWindowAttributes()
        if not libX11.XGetWindowAttributes(self.display, self.xid, ctypes.byref(attrs)):
            raise RuntimeError(f"cannot read window attributes for 0x{int(self.xid):x}")
        self.width = max(1, attrs.width)
        self.height = max(1, attrs.height)
        self.depth = attrs.depth
        if attrs.visual:
            self.visualid = int(ctypes.cast(attrs.visual, ctypes.POINTER(Visual)).contents.visualid)
        log.info(
            "source window size: %dx%d depth=%d visual=0x%x",
            self.width, self.height, attrs.depth, self.visualid,
        )

    def _on_realize(self, area: Gtk.GLArea) -> None:
        area.make_current()
        err = area.get_error()
        if err:
            raise RuntimeError(err.message)
        self._open_x()
        log.info("GL vendor: %s", _gl_string(GL_VENDOR))
        log.info("GL renderer: %s", _gl_string(GL_RENDERER))
        log.info("GL version: %s", _gl_string(GL_VERSION))
        self._load_glx_texture_from_pixmap()
        self._create_texture()
        self._create_shader_pipeline()
        self._redirect_window()
        self._bind_window_pixmap()

    def _load_glx_texture_from_pixmap(self) -> None:
        bind_addr = libGL.glXGetProcAddressARB(b"glXBindTexImageEXT")
        release_addr = libGL.glXGetProcAddressARB(b"glXReleaseTexImageEXT")
        if not bind_addr or not release_addr:
            raise RuntimeError("GLX_EXT_texture_from_pixmap functions are not available")
        self.bind_tex_image = ctypes.CFUNCTYPE(None, DisplayP, GLXPixmap, ctypes.c_int, ctypes.POINTER(ctypes.c_int))(bind_addr)
        self.release_tex_image = ctypes.CFUNCTYPE(None, DisplayP, GLXPixmap, ctypes.c_int)(release_addr)

    def _choose_fbconfig(self) -> GLXFBConfig:
        assert self.display is not None
        screen = libX11.XDefaultScreen(self.display)
        bind_attr = GLX_BIND_TO_TEXTURE_RGBA_EXT if self.depth == 32 else GLX_BIND_TO_TEXTURE_RGB_EXT
        attrs = (ctypes.c_int * 15)(
            GLX_DRAWABLE_TYPE, GLX_PIXMAP_BIT,
            GLX_RENDER_TYPE, GLX_RGBA_BIT,
            GLX_X_VISUAL_TYPE, GLX_TRUE_COLOR,
            bind_attr, 1,
            GLX_RED_SIZE, 8,
            GLX_GREEN_SIZE, 8,
            GLX_BLUE_SIZE, 8,
            0,
        )
        count = ctypes.c_int()
        configs = libGL.glXChooseFBConfig(self.display, screen, attrs, ctypes.byref(count))
        if not configs or count.value <= 0:
            fmt = "RGBA" if self.depth == 32 else "RGB"
            raise RuntimeError(f"no GLX FBConfig supports texture_from_pixmap {fmt}")
        for i in range(count.value):
            visual_id = ctypes.c_int()
            if libGL.glXGetFBConfigAttrib(self.display, configs[i], GLX_VISUAL_ID, ctypes.byref(visual_id)) == 0:
                if visual_id.value == self.visualid:
                    log.info("selected FBConfig matching visual=0x%x", self.visualid)
                    return configs[i]
        first_visual = ctypes.c_int()
        libGL.glXGetFBConfigAttrib(self.display, configs[0], GLX_VISUAL_ID, ctypes.byref(first_visual))
        log.warning(
            "no FBConfig matched source visual=0x%x; using first visual=0x%x",
            self.visualid, first_visual.value,
        )
        return configs[0]

    def _create_texture(self) -> None:
        libGL.glGenTextures(1, ctypes.byref(self.texture))
        libGL.glBindTexture(GL_TEXTURE_2D, self.texture.value)
        libGL.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        libGL.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)

    def _create_shader_pipeline(self) -> None:
        vertex_src = b"""#version 330 core
layout (location = 0) in vec2 in_pos;
layout (location = 1) in vec2 in_uv;
out vec2 uv;
void main() {
  uv = in_uv;
  gl_Position = vec4(in_pos, 0.0, 1.0);
}
"""
        fragment_src = b"""#version 330 core
in vec2 uv;
out vec4 out_color;
uniform sampler2D tex;
void main() {
  out_color = texture(tex, uv);
}
"""
        vs = _compile_shader(GL_VERTEX_SHADER, vertex_src)
        fs = _compile_shader(GL_FRAGMENT_SHADER, fragment_src)
        program = libGL.glCreateProgram()
        libGL.glAttachShader(program, vs)
        libGL.glAttachShader(program, fs)
        libGL.glLinkProgram(program)
        _check_program(program)
        libGL.glDeleteShader(vs)
        libGL.glDeleteShader(fs)
        self.program = ctypes.c_uint(program)

        # x, y, u, v. V coordinates are flipped because X pixmap origin is top-left.
        vertices = (ctypes.c_float * 16)(
            -1.0, -1.0, 0.0, 1.0,
             1.0, -1.0, 1.0, 1.0,
            -1.0,  1.0, 0.0, 0.0,
             1.0,  1.0, 1.0, 0.0,
        )
        libGL.glGenVertexArrays(1, ctypes.byref(self.vao))
        libGL.glGenBuffers(1, ctypes.byref(self.vbo))
        libGL.glBindVertexArray(self.vao.value)
        libGL.glBindBuffer(GL_ARRAY_BUFFER, self.vbo.value)
        libGL.glBufferData(GL_ARRAY_BUFFER, ctypes.sizeof(vertices), ctypes.cast(vertices, ctypes.c_void_p), GL_STATIC_DRAW)
        stride = 4 * ctypes.sizeof(ctypes.c_float)
        libGL.glEnableVertexAttribArray(0)
        libGL.glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, stride, ctypes.c_void_p(0))
        libGL.glEnableVertexAttribArray(1)
        libGL.glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, stride, ctypes.c_void_p(2 * ctypes.sizeof(ctypes.c_float)))
        libGL.glBindVertexArray(0)
        self._log_gl_error("create shader pipeline")

    def _redirect_window(self) -> None:
        assert self.display is not None
        libXcomposite.XCompositeRedirectWindow(self.display, self.xid, COMPOSITE_REDIRECT_AUTOMATIC)
        libX11.XSync(self.display, 0)
        self._redirected = True

    def _bind_window_pixmap(self) -> None:
        assert self.display is not None and self.bind_tex_image is not None
        fbconfig = self._choose_fbconfig()
        self.named_pixmap = libXcomposite.XCompositeNameWindowPixmap(self.display, self.xid)
        if not self.named_pixmap:
            raise RuntimeError("XCompositeNameWindowPixmap returned 0")
        log.info("named XComposite pixmap: 0x%x", int(self.named_pixmap))
        texture_format = GLX_TEXTURE_FORMAT_RGBA_EXT if self.depth == 32 else GLX_TEXTURE_FORMAT_RGB_EXT
        pix_attrs = (ctypes.c_int * 5)(
            GLX_TEXTURE_TARGET_EXT, GLX_TEXTURE_2D_EXT,
            GLX_TEXTURE_FORMAT_EXT, texture_format,
            0,
        )
        self.glx_pixmap = libGL.glXCreatePixmap(self.display, fbconfig, self.named_pixmap, pix_attrs)
        if not self.glx_pixmap:
            raise RuntimeError("glXCreatePixmap failed")
        log.info("created GLXPixmap: 0x%x", int(self.glx_pixmap))
        libGL.glBindTexture(GL_TEXTURE_2D, self.texture.value)
        self.bind_tex_image(self.display, self.glx_pixmap, GLX_FRONT_LEFT_EXT, None)
        log.info("bound window pixmap to GL texture id=%d", int(self.texture.value))
        self._log_gl_error("bind texture")

    def _on_render(self, area: Gtk.GLArea, _ctx) -> bool:
        area.make_current()
        alloc = area.get_allocation()
        self._draw_frame(alloc.width, alloc.height)
        self._log_gl_error("render")
        return True

    def _draw_frame(self, width: int, height: int) -> None:
        libGL.glViewport(0, 0, width, height)
        libGL.glClearColor(0.05, 0.05, 0.08, 1.0)
        libGL.glClear(GL_COLOR_BUFFER_BIT)
        if self.release_tex_image and self.bind_tex_image and self.display and self.glx_pixmap:
            self.release_tex_image(self.display, self.glx_pixmap, GLX_FRONT_LEFT_EXT)
            self.bind_tex_image(self.display, self.glx_pixmap, GLX_FRONT_LEFT_EXT, None)
        libGL.glUseProgram(self.program.value)
        libGL.glActiveTexture(GL_TEXTURE0)
        libGL.glBindTexture(GL_TEXTURE_2D, self.texture.value)
        tex_loc = libGL.glGetUniformLocation(self.program.value, b"tex")
        libGL.glUniform1i(tex_loc, 0)
        libGL.glBindVertexArray(self.vao.value)
        libGL.glDrawArrays(GL_TRIANGLE_STRIP, 0, 4)
        libGL.glBindVertexArray(0)
        libGL.glUseProgram(0)

    def _tick(self) -> bool:
        now_ms = GLib.get_monotonic_time() // 1000
        if now_ms - self._last_frame_ms < self._frame_interval_ms:
            return True
        self._last_frame_ms = now_ms
        self.area.queue_render()
        return True

    def _on_destroy(self, *_args) -> None:
        if self.display and self.release_tex_image and self.glx_pixmap:
            try:
                self.release_tex_image(self.display, self.glx_pixmap, GLX_FRONT_LEFT_EXT)
            except Exception:
                log.debug("release texture failed", exc_info=True)
        if self.program:
            libGL.glDeleteProgram(self.program.value)
        if self.vbo:
            libGL.glDeleteBuffers(1, ctypes.byref(self.vbo))
        if self.vao:
            libGL.glDeleteVertexArrays(1, ctypes.byref(self.vao))
        if self.display and self.glx_pixmap:
            libGL.glXDestroyPixmap(self.display, self.glx_pixmap)
        if self.display and self.named_pixmap:
            libX11.XFreePixmap(self.display, self.named_pixmap)
        if self.display and self._redirected:
            libXcomposite.XCompositeUnredirectWindow(self.display, self.xid, COMPOSITE_REDIRECT_AUTOMATIC)
        if self.display:
            libX11.XCloseDisplay(self.display)

    def _log_gl_error(self, label: str) -> None:
        err = libGL.glGetError()
        if err:
            log.warning("GL error after %s: 0x%x", label, err)


def parse_xid(value: str) -> int:
    return int(value, 16) if value.lower().startswith("0x") else int(value)


def _gl_string(name: int) -> str:
    raw = libGL.glGetString(name)
    return raw.decode("utf-8", "replace") if raw else "<none>"


def _compile_shader(kind: int, source: bytes) -> int:
    shader = libGL.glCreateShader(kind)
    src = ctypes.c_char_p(source)
    length = ctypes.c_int(len(source))
    libGL.glShaderSource(shader, 1, ctypes.byref(src), ctypes.byref(length))
    libGL.glCompileShader(shader)
    ok = ctypes.c_int()
    libGL.glGetShaderiv(shader, GL_COMPILE_STATUS, ctypes.byref(ok))
    if not ok.value:
        log_text = _shader_log(shader)
        libGL.glDeleteShader(shader)
        raise RuntimeError(f"shader compile failed: {log_text}")
    return shader


def _shader_log(shader: int) -> str:
    length = ctypes.c_int()
    libGL.glGetShaderiv(shader, GL_INFO_LOG_LENGTH, ctypes.byref(length))
    if length.value <= 1:
        return ""
    buf = ctypes.create_string_buffer(length.value)
    written = ctypes.c_int()
    libGL.glGetShaderInfoLog(shader, length.value, ctypes.byref(written), buf)
    return buf.value.decode("utf-8", "replace")


def _check_program(program: int) -> None:
    ok = ctypes.c_int()
    libGL.glGetProgramiv(program, GL_LINK_STATUS, ctypes.byref(ok))
    if ok.value:
        return
    length = ctypes.c_int()
    libGL.glGetProgramiv(program, GL_INFO_LOG_LENGTH, ctypes.byref(length))
    buf = ctypes.create_string_buffer(max(1, length.value))
    written = ctypes.c_int()
    libGL.glGetProgramInfoLog(program, len(buf), ctypes.byref(written), buf)
    raise RuntimeError(f"program link failed: {buf.value.decode('utf-8', 'replace')}")


def check_environment(xid: int) -> int:
    display = libX11.XOpenDisplay(None)
    if not display:
        log.error("XOpenDisplay failed")
        return 1
    try:
        ev = ctypes.c_int()
        err = ctypes.c_int()
        if not libXcomposite.XCompositeQueryExtension(display, ctypes.byref(ev), ctypes.byref(err)):
            log.error("XComposite extension is not available")
            return 1
        major = ctypes.c_int()
        minor = ctypes.c_int()
        libXcomposite.XCompositeQueryVersion(display, ctypes.byref(major), ctypes.byref(minor))
        log.info("XComposite %d.%d available", major.value, minor.value)
        attrs = XWindowAttributes()
        if not libX11.XGetWindowAttributes(display, Window(xid), ctypes.byref(attrs)):
            log.error("cannot read window attributes for 0x%x", xid)
            return 1
        visualid = 0
        if attrs.visual:
            visualid = int(ctypes.cast(attrs.visual, ctypes.POINTER(Visual)).contents.visualid)
        log.info("source window size: %dx%d depth=%d visual=0x%x", attrs.width, attrs.height, attrs.depth, visualid)
        bind_addr = libGL.glXGetProcAddressARB(b"glXBindTexImageEXT")
        release_addr = libGL.glXGetProcAddressARB(b"glXReleaseTexImageEXT")
        log.info("glXBindTexImageEXT: %s", "available" if bind_addr else "missing")
        log.info("glXReleaseTexImageEXT: %s", "available" if release_addr else "missing")
        screen = libX11.XDefaultScreen(display)
        fb_attrs = (ctypes.c_int * 15)(
            GLX_DRAWABLE_TYPE, GLX_PIXMAP_BIT,
            GLX_RENDER_TYPE, GLX_RGBA_BIT,
            GLX_X_VISUAL_TYPE, GLX_TRUE_COLOR,
            GLX_BIND_TO_TEXTURE_RGBA_EXT, 1,
            GLX_RED_SIZE, 8,
            GLX_GREEN_SIZE, 8,
            GLX_BLUE_SIZE, 8,
            0,
        )
        count = ctypes.c_int()
        configs = libGL.glXChooseFBConfig(display, screen, fb_attrs, ctypes.byref(count))
        log.info("texture-from-pixmap FBConfigs: %d", count.value if configs else 0)
        return 0 if bind_addr and release_addr and configs and count.value > 0 else 1
    finally:
        libX11.XCloseDisplay(display)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Prototype XComposite + GL live window preview.")
    parser.add_argument("xid", type=parse_xid, help="X11 window id, decimal or hex")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--check", action="store_true",
                        help="only check XComposite/GLX support for this XID; do not redirect or preview")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _setup_ctypes()
    if args.check:
        return check_environment(args.xid)
    win = LivePreview(args.xid, args.fps)
    win.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
