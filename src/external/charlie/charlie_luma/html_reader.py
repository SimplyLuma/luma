# SPDX-License-Identifier: Apache-2.0
"""Locked-down HTML message reader; the only web surface in Charlie."""
from __future__ import annotations

from html import escape
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk


def available() -> bool:
    try:
        gi.require_version("WebKit", "6.0")
        from gi.repository import WebKit  # noqa: F401
    except (ImportError, ValueError):
        return False
    return True


def _measure_document(view, callback: Callable[[int], None]) -> None:
    """Report full rendered height through WebKit's native snapshot API."""

    from gi.repository import WebKit

    def measured(web_view, result, _data=None) -> None:
        try:
            texture = web_view.get_snapshot_finish(result)
            scale = max(1, web_view.get_scale_factor())
            height = (texture.get_height() + scale - 1) // scale
        except (AttributeError, GLib.Error, TypeError, ValueError, ZeroDivisionError):
            return
        callback(max(1, height))

    view.get_snapshot(
        WebKit.SnapshotRegion.FULL_DOCUMENT,
        WebKit.SnapshotOptions.TRANSPARENT_BACKGROUND,
        None,
        measured,
        None,
    )


def set_preview_mode(view: Gtk.Widget, preview: bool) -> None:
    """Keep scrolling owned by the conversation, never by sender HTML."""

    # The document is created with overflow hidden. Expansion resizes that same
    # surface to its full native measurement instead of introducing a second
    # scroll owner, so no document mutation is required here.
    return


def reader(
    html: str,
    *,
    allow_remote: bool = False,
    preview: bool = False,
    on_content_height: Callable[[int], None] | None = None,
) -> Gtk.Widget:
    if not available():
        label = Gtk.Label(
            label="This formatted message needs WebKitGTK 6.",
            wrap=True,
            xalign=0,
        )
        label.add_css_class("dim-label")
        return label

    from gi.repository import WebKit

    view = WebKit.WebView()
    view.set_background_color(Gdk.RGBA(0.0, 0.0, 0.0, 0.0))
    settings = view.get_settings()
    settings.set_enable_javascript(False)
    # WebKitGTK 6.0 removed the legacy plug-in preference entirely. Keep the
    # hardening call for older supported runtimes without breaking newer ones.
    if hasattr(settings, "set_enable_plugins"):
        settings.set_enable_plugins(False)
    settings.set_enable_page_cache(False)
    settings.set_enable_html5_local_storage(False)
    settings.set_enable_offline_web_application_cache(False)

    def decide(_view, decision, decision_type):
        if decision_type == WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            action = decision.get_navigation_action()
            if action.get_navigation_type() == WebKit.NavigationType.LINK_CLICKED:
                uri = action.get_request().get_uri()
                # A formatted preview is one activation surface: clicking it
                # opens Charlie's complete-message window, where links regain
                # their ordinary external-navigation behavior.
                if not preview and uri.startswith(("https://", "http://", "mailto:")):
                    Gio.AppInfo.launch_default_for_uri(uri, None)
                decision.ignore()
                return True
        return False

    view.connect("decide-policy", decide)
    if on_content_height is not None:
        def loaded(web_view, event) -> None:
            if event == WebKit.LoadEvent.FINISHED:
                _measure_document(web_view, on_content_height)

        view.connect("load-changed", loaded)
    image_policy = "https: http: cid: data:" if allow_remote else "cid: data:"
    overflow = "hidden" if preview else "auto"
    wrapper = f"""<!doctype html><html><head>
      <meta charset=\"utf-8\">
      <meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src {escape(image_policy)}; style-src 'unsafe-inline'\">
      <meta name=\"color-scheme\" content=\"light\">
      <meta name=\"supported-color-schemes\" content=\"light\">
      <style>
        html,body{{margin:0;min-height:100%;max-width:100%;overflow-wrap:anywhere}}
        html{{overflow:{overflow};color-scheme:only light;background:Canvas}}
        body{{box-sizing:border-box;padding:16px 18px 24px;border-radius:10px;background:Canvas;color:CanvasText;font:15px/1.48 system-ui,sans-serif}}
        img{{max-width:100%;height:auto}} table{{max-width:100%}} pre{{white-space:pre-wrap}}
        a{{color:LinkText}}
      </style>
      </head><body>{html}</body></html>"""
    view.load_html(wrapper, "about:blank")
    return view
