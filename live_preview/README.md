# XComposite + OpenGL Live Preview Prototype

This is an isolated prototype for live X11 window previews. It does not integrate
with the D3 graph yet.

Check support for a window:

```bash
python3 live_preview/xcomposite_gl_preview.py --check 0x1000012
```

Run a live preview:

```bash
python3 live_preview/xcomposite_gl_preview.py 0x1000012 --fps 30
```

If running from the Snap VS Code terminal, clear Snap GTK/library variables first:

```bash
env -u LD_LIBRARY_PATH -u GI_TYPELIB_PATH -u GIO_MODULE_DIR -u GTK_PATH \
  python3 live_preview/xcomposite_gl_preview.py 0x1000012 --fps 30
```

Current scope:

- Redirects one X11 window with XComposite.
- Names the redirected window pixmap.
- Creates a GLX pixmap.
- Binds the GLX pixmap to an OpenGL texture.
- Draws it in a GTK `GLArea`.

Next integration step:

- Use this only for hovered/active card first.
- Position a native GTK overlay above the WebKit card.
- Keep all other cards on cached PNG thumbnails.
