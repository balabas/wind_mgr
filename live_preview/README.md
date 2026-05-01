# XComposite + OpenGL Live Preview

This module contains the XComposite/OpenGL preview implementation used for experiments and optional popup previews.

The main app now defaults to SVG thumbnail streaming instead of native popup previews. That preserves SVG overlays such as active-window blinking, hulls, labels, links, and drag feedback. Native popup preview can still be enabled with `live_preview_enabled = true` in `config.ini`, but it renders as a separate GTK window above WebKit/SVG.

## What It Does

- Redirects an X11 window with XComposite.
- Names the redirected window pixmap.
- Creates a GLX pixmap.
- Binds the GLX pixmap to an OpenGL texture.
- Draws it in a GTK `GLArea`.
- Can save the current rendered frame with OpenGL readback for thumbnail updates.

## Check Support

```bash
python3 live_preview/xcomposite_gl_preview.py --check 0x1000012
```

Use a real window XID. You can get one with `xwininfo`.

## Run Standalone Preview

```bash
python3 live_preview/xcomposite_gl_preview.py 0x1000012 --fps 30
```

If running from the Snap VS Code terminal, clear Snap GTK/library variables first:

```bash
env -u LD_LIBRARY_PATH -u GI_TYPELIB_PATH -u GIO_MODULE_DIR -u GTK_PATH \
  python3 live_preview/xcomposite_gl_preview.py 0x1000012 --fps 30
```

## Current Role In App

- Optional native popup preview when `live_preview_enabled = true`.
- GL readback support for preserving a streamed frame as a thumbnail.
- Prototype area for future lower-overhead streaming work.

## Limitations

- X11-only.
- Native popup previews are separate top-level windows, so they cannot be layered inside the SVG scene.
- High-rate updates for many cards are expensive if each frame requires readback and PNG encoding.
