# SPDX-License-Identifier: Apache-2.0
"""Start a Luma app off Luma OS so it looks the way it does on Luma OS.

    python -m luma_appkit.platform_boot <module>[:<function>] [args...]

``luma port`` launchers run apps through this instead of ``python -m <module>``.
Before the app's own code runs it gives the process what Luma OS gives every app
through the system:

* **Faces.** Figtree (the interface face) and the kit's document faces are
  registered for this process only: CoreText on macOS, a private GDI font on
  Windows, fontconfig elsewhere. Directories come from ``LUMA_FONT_DIRS`` plus the
  kit's own ``fonts`` folder.
* **GTK settings Luma OS sets in dconf**: ``gtk-font-name`` "Figtree 11", the
  Prairie icon theme when it is shipped, and 96 dpi. GTK's macOS backend defaults
  to 72 dpi and ``.AppleSystemUIFont 12``, which makes every point-sized face a
  quarter smaller than on Luma OS.
* **Icon folders** from ``LUMA_ICON_DIRS`` join the icon theme search path.

On Linux nothing is changed unless asked: the system already provides all of it.
Overrides: ``LUMA_FONT_NAME``, ``LUMA_ICON_THEME``, ``LUMA_DPI``.
"""
from __future__ import annotations

import ctypes
import os
import runpy
import sys
from pathlib import Path

from . import platform as _platform

_FONT_SUFFIXES = (".ttf", ".otf", ".ttc")
_registered: list[str] = []


def font_dirs() -> list[Path]:
    dirs = [Path(p) for p in os.environ.get("LUMA_FONT_DIRS", "").split(os.pathsep) if p]
    dirs.append(Path(__file__).resolve().parent.parent / "fonts")
    return [d for d in dirs if d.is_dir()]


def _font_files() -> list[Path]:
    files: list[Path] = []
    for folder in font_dirs():
        files.extend(sorted(p for p in folder.rglob("*") if p.suffix.lower() in _FONT_SUFFIXES))
    return files


def _register_coretext(files: list[Path]) -> None:
    ct = ctypes.CDLL("/System/Library/Frameworks/CoreText.framework/CoreText")
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    cf.CFURLCreateFromFileSystemRepresentation.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                                           ctypes.c_long, ctypes.c_bool]
    cf.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    ct.CTFontManagerRegisterFontsForURL.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
    ct.CTFontManagerRegisterFontsForURL.restype = ctypes.c_bool
    for path in files:
        raw = os.fsencode(path)
        url = cf.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), False)
        if not url:
            continue
        try:
            if ct.CTFontManagerRegisterFontsForURL(url, 1, None):  # kCTFontManagerScopeProcess
                _registered.append(path.name)
        finally:
            cf.CFRelease(url)


def _register_gdi(files: list[Path]) -> None:
    gdi = ctypes.WinDLL("gdi32")
    gdi.AddFontResourceExW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_void_p]
    for path in files:
        if gdi.AddFontResourceExW(str(path), 0x10, None):  # FR_PRIVATE
            _registered.append(path.name)


def _register_fontconfig(files: list[Path]) -> None:
    import ctypes.util

    name = ctypes.util.find_library("fontconfig")
    if not name:
        return
    fc = ctypes.CDLL(name)
    fc.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    for path in files:
        if fc.FcConfigAppFontAddFile(None, os.fsencode(path)):
            _registered.append(path.name)


def register_font_files(files) -> None:
    """Register additional bundled faces before constructing application widgets."""
    files = [Path(path).resolve() for path in files if Path(path).is_file() and Path(path).name not in _registered]
    if not files:return
    directories = list(dict.fromkeys([*(str(path.parent) for path in files),
                                     *filter(None, os.environ.get("LUMA_FONT_DIRS", "").split(os.pathsep))]))
    os.environ["LUMA_FONT_DIRS"] = os.pathsep.join(directories)
    if _platform.is_windows():
        _register_gdi(files)
    elif _platform.is_macos():
        _register_coretext(files)
    else:
        _register_fontconfig(files)
    _fresh_font_map()


def register_fonts() -> list[str]:
    """Make the shipped faces visible to Pango in this process. Call before GTK draws."""
    if _registered:
        return list(_registered)
    files = _font_files()
    system = _platform.system()
    try:
        if system == "macos":
            _register_coretext(files)
        elif system == "windows":
            _register_gdi(files)
        elif system == "android" or os.environ.get("LUMA_FONT_DIRS"):
            _register_fontconfig(files)
    except (OSError, AttributeError) as error:
        print(f"luma_appkit: fonts not registered ({error})", file=sys.stderr)
    if _registered:
        _fresh_font_map()
    return list(_registered)


def _fresh_font_map() -> None:
    """Importing luma_appkit loads GTK, which may already have built Pango's default font
    map. CoreText's map keeps the family list it enumerated then, so a CSS family list
    ("Figtree, Inter, …, sans-serif") skips Figtree and lands on a system face 6% wider.
    A new default map enumerates again, now with the registered faces."""
    try:
        import gi

        gi.require_version("PangoCairo", "1.0")
        from gi.repository import PangoCairo

        fontmap = PangoCairo.FontMap.new()
        if _platform.system() == "windows" and hasattr(fontmap, "add_font_file"):
            for path in _font_files():
                fontmap.add_font_file(str(path))
        PangoCairo.FontMap.set_default(fontmap)
    except (ImportError, ValueError, TypeError) as error:
        print(f"luma_appkit: font map not refreshed ({error})", file=sys.stderr)


def tune_gtk() -> None:
    """Luma OS's interface settings, applied to this process's GtkSettings."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gdk, Gtk

    if not Gtk.is_initialized() and not Gtk.init_check():
        return
    settings = Gtk.Settings.get_default()
    display = Gdk.Display.get_default()
    if settings is None or display is None:
        return
    off_luma = not _platform.is_linux()
    font = os.environ.get("LUMA_FONT_NAME") or ("Figtree 11" if off_luma else "")
    if font:
        settings.set_property("gtk-font-name", font)
    dpi = os.environ.get("LUMA_DPI") or ("96" if off_luma else "")
    if dpi:
        settings.set_property("gtk-xft-dpi", int(float(dpi) * 1024))
    hint = os.environ.get("LUMA_HINT_FONT_METRICS", "")
    if hint:
        settings.set_property("gtk-hint-font-metrics", hint not in ("0", "false", "no"))
    if _platform.is_android():
        from .android_appearance import start
        start()
    theme = Gtk.IconTheme.get_for_display(display)
    for folder in os.environ.get("LUMA_ICON_DIRS", "").split(os.pathsep):
        if folder and Path(folder).is_dir():
            theme.add_search_path(folder)
    icon_theme = os.environ.get("LUMA_ICON_THEME") or ("Prairie" if off_luma else "")
    if icon_theme and _theme_present(theme, icon_theme):
        settings.set_property("gtk-icon-theme-name", icon_theme)
    if off_luma and not os.environ.get("LUMA_APPKIT_ICON_PATH"):
        # The kit reads its glyph files itself (media_style.glyph_path: filled stars, hearts, play):
        # on Luma OS they sit beside luma_appkit; a port ships them in the bundled Prairie theme.
        for folder in theme.get_search_path() or []:
            glyphs = Path(folder) / "Prairie" / "symbolic" / "actions"
            if glyphs.is_dir():
                os.environ["LUMA_APPKIT_ICON_PATH"] = str(glyphs)
                break


def _theme_present(theme, name: str) -> bool:
    folders = list(theme.get_search_path() or [])
    return any((Path(folder) / name / "index.theme").is_file() for folder in folders)


def bridge_content_types() -> None:
    """macOS: let MIME types work where GIO expects content types.

    On Luma OS a GIO content type *is* a MIME type, so apps and the kit hand Gio
    "application/pdf" directly. On macOS GIO's content types are UTIs ("com.adobe.pdf"):
    a MIME type finds no default app, no icon and no description. Map MIME types to UTIs
    at GIO's content-type entry points; anything already a UTI passes through unchanged.
    """
    if not _platform.is_macos():
        return
    from gi.repository import Gio

    if getattr(Gio, "_luma_mime_bridge", False):
        return

    def uti(value):
        if isinstance(value, str) and "/" in value and not value.startswith("dyn."):
            return Gio.content_type_from_mime_type(value) or value
        return value

    def one(fn):
        return lambda content_type, *rest: fn(uti(content_type), *rest)

    def two(fn):
        return lambda a, b: fn(uti(a), uti(b))

    def apps(fn, empty):
        # A MIME type macOS has no UTI for maps to dyn.*: no app handles it, and asking GIO only
        # logs "No default handler found" (12+ times per launch in Messages).
        def lookup(content_type, *rest):
            target = uti(content_type)
            if isinstance(target, str) and target.startswith("dyn."):
                return empty()
            return fn(target, *rest)
        return lookup

    for name in ("content_type_get_icon", "content_type_get_symbolic_icon", "content_type_get_description",
                 "content_type_get_generic_icon_name", "content_type_can_be_executable",
                 "content_type_is_unknown"):
        if hasattr(Gio, name):
            setattr(Gio, name, one(getattr(Gio, name)))
    for name in ("content_type_is_a", "content_type_equals"):
        setattr(Gio, name, two(getattr(Gio, name)))
    for name in ("get_default_for_type", "get_all_for_type", "get_recommended_for_type",
                 "get_fallback_for_type"):
        if hasattr(Gio.AppInfo, name):
            setattr(Gio.AppInfo, name, staticmethod(apps(getattr(Gio.AppInfo, name),
                                                         (lambda: None) if name == "get_default_for_type" else list)))
    # An app's icon on macOS is an .icns that gdk-pixbuf cannot decode (PNG/JPEG 2000
    # payloads): hand GTK a PNG made once with the system's sips.
    original_icon = Gio.AppInfo.get_icon

    def get_icon(info):
        icon = original_icon(info)
        if isinstance(icon, Gio.FileIcon):
            path = icon.get_file().get_path() or ""
            if path.endswith(".icns"):
                png = _icns_png(path)
                if png:
                    return Gio.FileIcon.new(Gio.File.new_for_path(png))
        return icon

    Gio.AppInfo.get_icon = get_icon
    Gio._luma_mime_bridge = True


def _icns_png(path: str) -> str | None:
    import hashlib
    import subprocess

    from gi.repository import GLib

    cache = Path(GLib.get_user_cache_dir()) / "luma-appkit" / "app-icons"
    target = cache / (hashlib.sha1(path.encode()).hexdigest()[:16] + ".png")
    if target.exists():
        return str(target)
    try:
        cache.mkdir(parents=True, exist_ok=True)
        subprocess.run(["/usr/bin/sips", "-s", "format", "png", "-Z", "128", path, "--out", str(target)],
                       check=True, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return str(target) if target.exists() else None


def instant_window_zoom() -> None:
    """macOS: maximize and unmaximize in one step, so the contents move with the window.

    GDK's macOS backend maximizes through -[NSWindow zoom:], which plays AppKit's resize
    animation (about 350 ms) and blocks GTK's main loop while it runs: the frame grew to full
    size and the contents followed when it ended, and on unmaximize the shrinking frame
    clipped contents GTK had no chance to redraw (first GTK frame 390 ms after the call;
    47 ms with this). The animation length is the NSWindowResizeTime default; registering it
    for this process only (the registration domain: volatile, lowest precedence, nothing
    written) makes the zoom a single step. LUMA_MAC_WINDOW_ANIMATION=1 keeps the animation.
    """
    if not _platform.is_macos() or os.environ.get("LUMA_MAC_WINDOW_ANIMATION") == "1":
        return
    try:
        objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        ctypes.CDLL("/System/Library/Frameworks/Foundation.framework/Foundation")
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]

        def send(receiver, selector: str, *args, argtypes=()):
            fn = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(
                ("objc_msgSend", objc))
            return fn(receiver, objc.sel_registerName(selector.encode()), *args)

        cls = objc.objc_getClass
        key = send(cls(b"NSString"), "stringWithUTF8String:", b"NSWindowResizeTime", argtypes=(ctypes.c_char_p,))
        value = send(cls(b"NSNumber"), "numberWithDouble:", 0.001, argtypes=(ctypes.c_double,))
        table = send(cls(b"NSDictionary"), "dictionaryWithObject:forKey:", value, key,
                     argtypes=(ctypes.c_void_p, ctypes.c_void_p))
        defaults = send(cls(b"NSUserDefaults"), "standardUserDefaults")
        send(defaults, "registerDefaults:", table, argtypes=(ctypes.c_void_p,))
    except (OSError, AttributeError, TypeError) as error:
        print(f"luma_appkit: window zoom left animated ({error})", file=sys.stderr)


def no_window_restoration() -> None:
    """macOS: never offer to "reopen windows" after a crash.

    GTK windows are not restorable, but AppKit keeps crash history for the app and, on the next
    launch, runs a modal NSAlert ("…unexpectedly quit while reopening windows…") inside
    g_application_run before GTK shows anything; it can open behind other windows, so the app
    just seems not to start. ApplePersistenceIgnoreState=YES and NSQuitAlwaysKeepsWindows=NO in
    the app's own defaults domain turn restoration off (the registration or argument domain is
    not enough: AppKit reads the app's persistent domain). Written only when they differ, and
    never into a bare interpreter's domain (org.python.*: a developer's direct run).
    """
    if not _platform.is_macos():
        return
    try:
        objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        ctypes.CDLL("/System/Library/Frameworks/Foundation.framework/Foundation")
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]

        def send(receiver, selector: str, *args, argtypes=(), restype=ctypes.c_void_p):
            fn = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(("objc_msgSend", objc))
            return fn(receiver, objc.sel_registerName(selector.encode()), *args)

        cls = objc.objc_getClass
        bundle = send(cls(b"NSBundle"), "mainBundle")
        ident = send(bundle, "bundleIdentifier") if bundle else None
        name = ctypes.c_char_p(send(ident, "UTF8String")).value if ident else b""
        if not name or name.startswith(b"org.python"):
            return
        defaults = send(cls(b"NSUserDefaults"), "standardUserDefaults")
        for key, wanted in ((b"ApplePersistenceIgnoreState", True), (b"NSQuitAlwaysKeepsWindows", False)):
            k = send(cls(b"NSString"), "stringWithUTF8String:", key, argtypes=(ctypes.c_char_p,))
            present = send(defaults, "objectForKey:", k, argtypes=(ctypes.c_void_p,))
            current = send(defaults, "boolForKey:", k, argtypes=(ctypes.c_void_p,), restype=ctypes.c_bool)
            if not present or current != wanted:
                send(defaults, "setBool:forKey:", wanted, k, argtypes=(ctypes.c_bool, ctypes.c_void_p),
                     restype=None)
    except (OSError, AttributeError, TypeError) as error:
        print(f"luma_appkit: window restoration left on ({error})", file=sys.stderr)


def defer_first_present() -> None:
    """Off Luma OS: a window's first ``present()`` runs after the idles the app queued while building it.

    On Luma OS (Wayland) a new window's first layout waits for the compositor's configure, so
    start-up idles (open the selected item, restore state) run before the first allocation and
    the breakpoints that come with it. macOS and Android GDK lay the window out right after
    ``present()``, so a phone-width breakpoint (ListFirst: open on the list) applied first and a
    late start-up idle then undid it. Deferring the first present to an idle behind the app's own
    restores Luma OS's order. Linux: unchanged. ``LUMA_PORT_IMMEDIATE_PRESENT=1`` turns it off.
    """
    if _platform.is_linux() or os.environ.get("LUMA_PORT_IMMEDIATE_PRESENT") == "1":
        return
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk

    present = Gtk.Window.present
    if getattr(present, "_luma_deferred", False):
        return

    def deferred_present(window, *args):
        # Only the first present, and only from inside a running main loop (activate, an idle):
        # called before Application.run or from a test that pumps by hand, it presents at once.
        if getattr(window, "_luma_presented", False) or GLib.main_depth() == 0:
            window._luma_presented = True
            return present(window, *args)
        window._luma_presented = True
        sources: list[int] = []

        def later():
            if sources:   # whichever of the two fires first presents; the other is dropped
                for source in sources:
                    GLib.source_remove(source)
                sources.clear()
                present(window, *args)
            return GLib.SOURCE_REMOVE
        # Behind the app's start-up idles; a short timeout too, so a loop whose idles are starved
        # by busier sources still shows its window.
        sources.append(GLib.idle_add(later, priority=GLib.PRIORITY_DEFAULT_IDLE))
        sources.append(GLib.timeout_add(100, later))

    deferred_present._luma_deferred = True
    Gtk.Window.present = deferred_present


_GENERIC_PARTS = {"prairie_apps", "application", "app", "main", "__main__", "luma", "src"}


def app_name(module: str = "") -> str:
    """The app's display name: ``LUMA_APP_NAME`` (luma port bakes the manifest's name into
    every package), else a guess from the module a source run starts
    (``prairie_apps.notes`` → Notes, ``charlie_luma`` → Charlie, ``luma_darkroom.application`` → Darkroom)."""
    name = os.environ.get("LUMA_APP_NAME", "").strip()
    if name:
        return name
    parts = [p for p in module.partition(":")[0].split(".") if p and p not in _GENERIC_PARTS]
    if not parts:
        return ""
    words = [w for w in parts[0].split("_") if w and w != "luma"] or [parts[0]]
    return " ".join(w.capitalize() for w in words[:1])


def _name_macos_process(name: str) -> None:
    """The bold app name in the macOS menu bar (and the Dock's, ⌘-Tab's) is the main bundle's
    CFBundleName, or the executable's name when there is no bundle — "Python" for a source run
    on Homebrew's python, whose Python.app carries its own Info.plist. Setting the name in the
    main bundle's info dictionary before NSApplication starts (GTK's macOS backend creates it
    in Gtk.init) makes every one of them say the app's name."""
    objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    ctypes.CDLL("/System/Library/Frameworks/Foundation.framework/Foundation")
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    send = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
    send_str = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p)(
        ("objc_msgSend", objc))
    send_set = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(
        ("objc_msgSend", objc))
    sel = objc.sel_registerName
    nsstring = objc.objc_getClass(b"NSString")

    def string(text: str):
        return send_str(nsstring, sel(b"stringWithUTF8String:"), text.encode())

    responds = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(
        ("objc_msgSend", objc))
    send_one = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
    bundle = send(objc.objc_getClass(b"NSBundle"), sel(b"mainBundle"))
    value, key = string(name), string("CFBundleName")
    for getter in (b"infoDictionary", b"localizedInfoDictionary"):
        info = send(bundle, sel(getter)) if bundle else None
        # Both are NSMutableDictionary instances in practice; anything immutable is left alone.
        if info and responds(info, sel(b"respondsToSelector:"), sel(b"setObject:forKey:")):
            send_set(info, sel(b"setObject:forKey:"), value, key)
    process = send(objc.objc_getClass(b"NSProcessInfo"), sel(b"processInfo"))
    if process:
        send_one(process, sel(b"setProcessName:"), value)


def _ls_display_name(name: str) -> bool:
    """Once NSApplication has checked in with LaunchServices (Gtk.init), its display name is
    what the menu bar shows; a source run checked in as "Python" before any Python ran."""
    ls = ctypes.CDLL("/System/Library/Frameworks/CoreServices.framework/CoreServices")
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    ls._LSGetCurrentApplicationASN.restype = ctypes.c_void_p
    ls._LSSetApplicationInformationItem.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                                    ctypes.c_void_p, ctypes.c_void_p]
    ls._LSSetApplicationInformationItem.restype = ctypes.c_int
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    asn = ls._LSGetCurrentApplicationASN()
    if not asn:
        return False
    key = ctypes.c_void_p.in_dll(ls, "_kLSDisplayNameKey").value
    value = cf.CFStringCreateWithCString(None, name.encode(), 0x08000100)  # kCFStringEncodingUTF8
    return ls._LSSetApplicationInformationItem(-2, asn, key, value, None) == 0  # kLSDefaultSessionID


def name_process(module: str = "") -> str:
    """Off Luma OS: the app's name where the platform shows one (macOS menu bar, GLib's
    application name for GTK's own menu items). Linux: unchanged (the .desktop file names it)."""
    if _platform.is_linux():
        return ""
    name = app_name(module)
    if not name:
        return ""
    try:
        from gi.repository import GLib

        if not GLib.get_application_name() or GLib.get_application_name() == GLib.get_prgname():
            GLib.set_application_name(name)
    except (ImportError, AttributeError):
        pass
    if _platform.is_macos():
        try:
            _name_macos_process(name)
        except (OSError, AttributeError, TypeError) as error:
            print(f"luma_appkit: app name not set ({error})", file=sys.stderr)
    return name


def hide_native_window_buttons() -> None:
    """macOS: a Luma window shows only Luma's own ─ ⤢ ✕, never AppKit's traffic lights as well.

    GDK's macOS backend gives a client-decorated window a titled NSWindow (for its shadow and
    rounded corners) with a transparent titlebar and the standard buttons hidden; it shows them
    again whenever it applies a layout it takes for server-decorated. Now and then (QA: 1 Darkroom
    launch in 7) a window kept them, greyed in a pill beside the kit's own controls, for the whole
    session. The kit hides the close/miniaturize/zoom buttons of every client-side-decorated window
    (GTK's `csd` class) when it maps, after every size or state change of its surface (layout,
    fullscreen, maximize, minimize) and when it gains or loses focus, so whatever GDK last applied,
    the window ends with them hidden.
    Server-decorated windows are left alone. LUMA_MAC_NATIVE_BUTTONS=1 turns it off.
    """
    if not _platform.is_macos() or os.environ.get("LUMA_MAC_NATIVE_BUTTONS") == "1":
        return
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk

    try:
        native_window = _native_window_lookup()
        objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        button_of = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long)(
            ("objc_msgSend", objc))
        is_hidden = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
        set_hidden = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool)(("objc_msgSend", objc))
        sel_button = objc.sel_registerName(b"standardWindowButton:")
        sel_is_hidden = objc.sel_registerName(b"isHidden")
        sel_set_hidden = objc.sel_registerName(b"setHidden:")
    except (OSError, AttributeError) as error:
        print(f"luma_appkit: native window buttons not managed ({error})", file=sys.stderr)
        return

    def hide(window) -> bool:
        if not window.get_mapped() or not window.has_css_class("csd"):
            return GLib.SOURCE_REMOVE
        surface = window.get_surface()
        ns = native_window(surface) if surface is not None else None
        if ns:
            for kind in (0, 1, 2):   # NSWindowCloseButton, MiniaturizeButton, ZoomButton
                button = button_of(ns, sel_button, kind)
                if button and not is_hidden(button, sel_is_hidden):
                    set_hidden(button, sel_set_hidden, True)
        return GLib.SOURCE_REMOVE

    def soon(window) -> None:
        # Now (after GDK's own pass in this frame) and once more after the next layout.
        GLib.idle_add(hide, window, priority=GLib.PRIORITY_HIGH_IDLE + 30)
        GLib.timeout_add(250, hide, window)

    def watch(window) -> None:
        if getattr(window, "_luma_native_buttons", False):
            return
        window._luma_native_buttons = True

        def mapped(*_args) -> None:
            soon(window)
            surface = window.get_surface()
            if surface is not None and not getattr(surface, "_luma_native_buttons", False):
                surface._luma_native_buttons = True
                for prop in ("notify::state", "notify::width", "notify::height"):
                    surface.connect(prop, lambda *_a: soon(window))

        window.connect("map", mapped)
        window.connect("notify::fullscreened", lambda *_a: soon(window))
        window.connect("notify::maximized", lambda *_a: soon(window))
        window.connect("notify::is-active", lambda *_a: soon(window))
        if window.get_mapped():
            mapped()

    toplevels = Gtk.Window.get_toplevels()

    def changed(model, position, _removed, added) -> None:
        for index in range(position, position + added):
            watch(model.get_item(index))

    toplevels.connect("items-changed", changed)
    changed(toplevels, 0, 0, toplevels.get_n_items())
    _keep.append(toplevels)


_keep: list = []


def _native_window_lookup():
    """NSWindow* of a GdkSurface, through GDK's C function (the GdkMacos typelib cannot be used:
    PyGObject has already wrapped the surface's type by the time it would load)."""
    capsule = ctypes.pythonapi.PyCapsule_GetPointer
    capsule.restype = ctypes.c_void_p
    capsule.argtypes = [ctypes.py_object, ctypes.c_char_p]
    dyld = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    dyld._dyld_image_count.restype = ctypes.c_uint32
    dyld._dyld_get_image_name.restype = ctypes.c_char_p
    dyld._dyld_get_image_name.argtypes = [ctypes.c_uint32]
    path = next((dyld._dyld_get_image_name(i) for i in range(dyld._dyld_image_count())
                 if (dyld._dyld_get_image_name(i) or b"").endswith(b"/libgtk-4.1.dylib")), None)
    if path is None:
        raise OSError("libgtk-4.1.dylib is not loaded")
    gtk = ctypes.CDLL(path.decode())
    lookup = gtk.gdk_macos_surface_get_native_window
    lookup.restype = ctypes.c_void_p
    lookup.argtypes = [ctypes.c_void_p]

    def native_window(surface):
        try:
            return lookup(capsule(surface.__gpointer__, None))
        except (TypeError, ValueError, AttributeError):
            return None
    return native_window


def prepare(module: str = "") -> None:
    name = name_process(module)
    register_fonts()
    bridge_content_types()
    instant_window_zoom()
    no_window_restoration()
    tune_gtk()
    if name and _platform.is_macos():
        try:
            _ls_display_name(name)
        except (OSError, AttributeError, ValueError) as error:
            print(f"luma_appkit: menu bar name not set ({error})", file=sys.stderr)
    hide_native_window_buttons()
    defer_first_present()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: python -m luma_appkit.platform_boot <module> [args...]", file=sys.stderr)
        return 2
    module, rest = argv[0], argv[1:]
    # Off Luma OS: the platform's own per-app folders behind XDG_*_HOME, before GLib caches them.
    _platform.adopt_user_dirs()
    # A windowless agent (`--agent`) must not load GTK; it gets fonts only.
    if "--agent" in rest:
        register_fonts()
    else:
        prepare(module)
    # "pkg.mod:func" imports the module under its own name and calls func(), as the app's
    # Linux launcher does (`from pkg.mod import main`). Running a module that registers
    # GTypes as __main__ would import it a second time when the app imports it by name.
    module, _, function = module.partition(":")
    sys.argv = [module, *rest]
    if function:
        import importlib

        result = getattr(importlib.import_module(module), function)()
        return result if isinstance(result, int) else 0
    runpy.run_module(module, run_name="__main__", alter_sys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
