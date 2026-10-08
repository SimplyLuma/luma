# SPDX-License-Identifier: Apache-2.0
"""Depot's LumaUI page composition. The window owns loading and actions.

The app supplies catalogue facts and editorial layout. Shared chrome, cards,
category colour, counts, tables, dialogs and feedback belong to the kit.
"""

from __future__ import annotations

import os
import textwrap
from dataclasses import replace
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from luma_appkit import (AppIcon, BarAction, Card, CategoryPill, Column, CountBadge,
                        ContentLitCard, ContentLitHeader, ProgressLine, TableHeader,
                        Toast, TextButton, apply_type, icons)
from luma_appkit.structure_adapt import WidthWatch
from luma_appkit.action_center import make_control
from luma_installer import depot_icons

from .v70_data import Listing, installed_sorted, search
from .home_selection import featured_app, luma_shelf_apps, popular_apps


def _text(value: str, role: str = "body", *, wrap: bool = False, muted: bool = False,
          weight: int | None = None) -> Gtk.Label:
    label = Gtk.Label(label=value, xalign=0, wrap=wrap, wrap_mode=Pango.WrapMode.WORD_CHAR)
    return apply_type(label, role, muted=muted, weight=weight)


def _column(*children: Gtk.Widget, spacing: int = 4) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    for child in children:
        box.append(child)
    return box


def _row(*children: Gtk.Widget, spacing: int = 8) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing)
    for child in children:
        box.append(child)
    return box


def _clickable(widget: Gtk.Widget, label: str, activate) -> Gtk.Widget:
    widget.set_accessible_role(Gtk.AccessibleRole.BUTTON)
    widget.set_focusable(True)
    widget.update_property([Gtk.AccessibleProperty.LABEL], [label])
    click = Gtk.GestureClick()
    def on_release(_gesture, _count, x, y):
        target = widget.pick(x, y, Gtk.PickFlags.DEFAULT)
        while target is not None and target is not widget:
            if isinstance(target, Gtk.Button):
                return
            target = target.get_parent()
        activate()
    click.connect("released", on_release)
    widget.add_controller(click)
    keys = Gtk.EventControllerKey()
    def on_key(_controller, key, _code, _modifiers):
        if widget.has_focus() and key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            activate()
            return True
        return False
    keys.connect("key-pressed", on_key)
    widget.add_controller(keys)
    return widget


def _button(label: str, clicked, *, primary: bool = False) -> Gtk.Button:
    glyph = "chevron-left" if label == "Discover" else ""
    button = TextButton(label, icon=glyph or None, style="key" if primary else "raised", on_click=clicked)
    if label == "What’s new":
        button.set_child(_row(Gtk.Label(label=label), icons.image("chevron-down"), spacing=5))
    elif label == "Read the full release notes":
        button.set_child(_row(Gtk.Label(label=label), icons.image("chevron-right"), spacing=4))
    button.set_halign(Gtk.Align.START)
    return button


def _more_button(window, listing: Listing, *, big: bool = False) -> Gtk.Button:
    button = make_control(BarAction("ellipsis", tooltip=f"More for {listing.name}",
                                    on_activate=lambda: window._show_uninstall_menu(listing, button)),
                          size="bar" if big else "tool")
    button.set_halign(Gtk.Align.START)
    if big:
        button.set_size_request(44, 44)
    return button


def _open(window, listing: Listing) -> None:
    window.go("app", listing.id)


def _icon(window, listing: Listing, size: int) -> Gtk.Widget:
    # The installed desktop/theme identity is authoritative. Catalogue media
    # and legacy editorial artwork are fallbacks for apps absent locally.
    # Curated identities (catalog:notes) are not desktop IDs. The installed
    # provider already carries the actual launcher's serialized GIcon.
    picture = None
    record = window.installed.get(listing.id)
    owned = record.app if record is not None else None
    display = Gdk.Display.get_default()
    if owned is not None and display is not None:
        theme = Gtk.IconTheme.get_for_display(display)
        choice = depot_icons.choose(name=listing.name, icon_name=owned.icon_name,
                                   theme_has=theme.has_icon)
        if choice.drawn:
            gicon = (Gio.FileIcon.new(Gio.File.new_for_path(choice.value))
                     if choice.kind == 'file' else Gio.ThemedIcon.new(choice.value))
            picture = theme.lookup_by_gicon(gicon, size, max(1, window.get_scale_factor()),
                                           Gtk.TextDirection.NONE, Gtk.IconLookupFlags.PRELOAD)
    identity = AppIcon(app_id=listing.id, picture=picture, name=listing.name, size=size,
                       category=listing.category.casefold(),
                       hue=float(listing.mono[1]) if len(listing.mono) > 1 else None)
    if identity.paintable is not None:
        identity.set_halign(Gtk.Align.START)
        return identity
    root = Path(os.environ.get("LUMA_DEPOT_ICON_ROOT", str(
        Path(__file__).resolve().parents[3] / "assets/icon-theme/Prairie/scalable/apps")))
    path = root / f"luma-v3-{listing.icon}.svg"
    picture = None
    app = window.catalogue.find(listing.id) if window.catalogue and not window.fixture else None
    if app and app.icon_sha256:
        cached = window.media.path(app.icon_url, app.icon_sha256)
        if cached:
            try:
                picture = Gdk.Texture.new_from_filename(cached)
            except GLib.Error:
                pass
    if listing.icon and path.is_file():
        display = Gdk.Display.get_default()
        if picture is None and display is not None:
            picture = Gtk.IconTheme.get_for_display(display).lookup_by_gicon(
                Gio.FileIcon.new(Gio.File.new_for_path(str(path))), size, 1,
                Gtk.TextDirection.NONE, Gtk.IconLookupFlags.PRELOAD)
    icon = AppIcon(app_id=listing.id if picture is None else None, picture=picture,
                   name=listing.name, size=size, category=listing.category.casefold(),
                   hue=float(listing.mono[1]) if len(listing.mono) > 1 else None)
    icon.set_halign(Gtk.Align.START)
    return icon


def _listing(window, app) -> Listing:
    if window.fixture:
        listing = window.fixture_listings[app.app_id]
        if window._installed_ready:
            record = window.installed.get(app.app_id)
            return replace(listing, installed=record is not None,
                           update=record.update_version if record else "")
        return listing
    raw_category = next((category for category in app.categories
                         if category.casefold() in {"create", "work", "media", "play", "tools"}), "Tools")
    record = window.installed.get(app.app_id)
    if app.system_state:
        kind = "system"
    elif record and not record.managed:
        kind = "layered"
    else:
        kind = "flatpak"
    size = (f"{round(app.download_bytes / 1_000_000)} MB" if app.download_bytes else "—")
    return Listing(app.app_id, app.name, app.tagline or app.summary, raw_category.lower(),
                   size=size, rating=app.rating, reviews=app.rating_count,
                   luma=app.tier == "luma", icon=app.icon_name.removeprefix("luma-v3-").removesuffix("-symbolic"),
                   installed=record is not None, update=record.update_version if record else "",
                   package=kind)


def _listings(window) -> tuple[Listing, ...]:
    return tuple(_listing(window, app) for app in window.catalogue.apps) if window.catalogue else ()


def _asset(window, listing: Listing, *, light: bool = False, index: int = 0) -> str:
    if window.fixture and listing.shot:
        root = Path(os.environ.get("LUMA_DEPOT_STUDIO_ROOT", str(Path.home() / "Documents/LumaDesign/studio/mockups")))
        file = root / "depot" / f"{listing.shot}-{'light' if light else 'dark'}.webp"
        return str(file) if file.is_file() else ""
    app = window.catalogue.find(listing.id) if window.catalogue else None
    if app and index < len(app.screenshots):
        shot = app.screenshots[index]
        if shot.url.startswith("file://"):
            path = Path(shot.url.removeprefix("file://"))
            return str(path) if path.is_file() else ""
        return window.media.path(shot.url, shot.sha256)
    return ""


def _picture(path: str, *, width: int = -1, height: int = -1) -> Gtk.Widget:
    if path:
        try:
            # Gtk.Picture's file loader is asynchronous. The conformance
            # capture can take its first frame before a WebP becomes visible.
            picture = Gtk.Picture.new_for_paintable(Gdk.Texture.new_from_filename(path))
            picture.set_content_fit(Gtk.ContentFit.COVER)
            picture.set_can_shrink(True)
        except GLib.Error:
            path = ""
    if not path:
        picture = Gtk.Box()
        picture.add_css_class("depot-image-empty")
    picture.set_size_request(width, height)
    return picture


def _get_button(window, listing: Listing, *, big: bool = False) -> Gtk.Button:
    app = window.catalogue.find(listing.id)
    job = window.jobs.get(listing.id)
    record = window.installed.get(listing.id)
    if job is not None and job.failed:
        button = _button("Try again", lambda: window._update(listing.id, approve=False,
                                                            expected_commit=record.update_commit if record else "",
                                                            expected_installed_commit=record.commit if record else "")
                         if job.kind == "update" else window._confirm_install(app))
    elif job is not None:
        progress = round(job.progress.fraction * 100) if job.progress else 0
        verb = "Updating" if job.kind == "update" else "Installing"
        button = _button(f"{verb} {progress}%", lambda: window._cancel(listing.id))
        button.set_tooltip_text("Cancel update" if job.kind == "update" else "Cancel installation")
        button.set_size_request(132, -1)
        def update_label(update):
            label = f"{verb} {round(update.fraction * 100)}%"
            button.label_widget.set_label(label)
            button.update_property([Gtk.AccessibleProperty.LABEL], [label])
        window._progress_views.setdefault(listing.id, []).append(update_label)
    elif listing.update:
        if record is not None and window._asks_for_more(record):
            button = _button("Review", lambda: _open(window, listing), primary=True)
        else:
            button = _button("Update", lambda: window._update(listing.id, approve=False,
                            expected_commit=record.update_commit if record else "",
                            expected_installed_commit=record.commit if record else ""), primary=True)
    elif listing.installed:
        button = _button("Open", lambda: window._open_installed(app), primary=big)
    elif not app.installable:
        button = _button("Details", lambda: _open(window, listing))
    else:
        button = _button("Get", lambda: window._confirm_install(app), primary=big)
    button.add_css_class("depot-get-big" if big else "depot-get")
    return button


def _section(title: str, body: Gtk.Widget, *, subtitle: str = "", more=None,
             more_label: str = "See all", gap: int = 12) -> Gtk.Widget:
    if not title and not subtitle and more is None:
        return body
    title_label = _text(title, "guidance", wrap=True)
    wide_title = _text(title, "title-1", wrap=True)
    wide_title.set_visible(False)
    heading = _row(title_label, wide_title, spacing=10)
    heading.add_css_class("depot-section-heading")
    subtitle_label = None
    if subtitle:
        subtitle_label = _text(subtitle, "body", wrap=True, muted=True)
        subtitle_label.set_hexpand(True)
        heading.append(subtitle_label)
    else:
        heading.append(Gtk.Box(hexpand=True))
    if more is not None:
        more_button = _button(more_label, more)
        more_button.add_css_class("depot-section-link")
        heading.append(more_button)
    # A title can wrap independently of whether its section has a subtitle.
    # Let GTK measure it instead of clipping two lines into a fixed-height bin.
    def adapt(_width):
        width = heading.get_width()
        compact = width <= 720
        title_label.set_visible(width <= 1400)
        wide_title.set_visible(width > 1400)
        title_label.set_max_width_chars(12 if compact else -1)
        title_label.set_label("Made for\nLuma" if compact and title == "Made for Luma" else title)
        if subtitle_label is not None:
            subtitle_label.set_max_width_chars(18 if compact else -1)
        heading.set_spacing(6 if compact else 10)
    heading._depot_width_watch = WidthWatch(heading, adapt)
    section = _column(heading, body, spacing=gap)
    section.add_css_class("depot-section")
    return section


def _card(window, listing: Listing, *, category: bool = False, wide: bool = False) -> Gtk.Widget:
    icon = Gtk.Overlay()
    icon.set_child(_icon(window, listing, 64 if wide else 56))
    icon.set_margin_bottom(6)
    category_pill = CategoryPill(listing.category)
    category_pill.set_halign(Gtk.Align.END)
    category_pill.set_valign(Gtk.Align.START)
    icon.add_overlay(category_pill)
    tagline = _text(listing.tagline, "body" if wide else "meta", wrap=True)
    tagline.set_max_width_chars(28 if wide else 20)
    tagline.set_size_request(-1, 40 if wide else 35)
    action = _get_button(window, listing)
    action.set_margin_top(11)
    contents = _column(icon,
                       _text(listing.name, "title-1" if wide else "list-title", weight=650),
                       tagline,
                       action, spacing=0)
    contents.set_margin_top(4 if category else 7)
    contents.add_css_class("depot-app-card-content")
    card = Card(contents)
    card.add_css_class("depot-app-card")
    if wide:
        card.set_size_request(236, 232)
        card.set_hexpand(True)
        return _clickable(card, listing.name, lambda: _open(window, listing))
    card.set_size_request(196, 202)
    return _clickable(card, listing.name, lambda: _open(window, listing))


def _shelf_layout(window, listings, *, cards: bool, category: bool, wide: bool) -> Gtk.Widget:
    if not cards:
        content = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                              min_children_per_line=1, max_children_per_line=4 if wide else 2,
                              row_spacing=8, column_spacing=20 if wide else 16)
        content.add_css_class("depot-list-grid")
        for side in ("start", "end", "top", "bottom"):
            getattr(content, "set_margin_" + side)(4)
        for listing in listings:
            content.insert(_list_row(window, listing, wide=wide), -1)
        return content
    content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16 if wide else 12)
    if wide and len(listings) <= 5:
        content.set_hexpand(True)
        content.set_homogeneous(True)
    for listing in listings:
        content.append(_card(window, listing, category=category, wide=wide))
    if cards:
        for side in ("start", "end", "top", "bottom"):
            getattr(content, "set_margin_" + side)(4)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                   vscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(content)
        scroll.set_propagate_natural_height(True)
        scroll.add_css_class("depot-shelf-scroll")
        if wide:
            scroll.add_css_class("depot-wide-shelf")
        if category:
            scroll.add_css_class("depot-category-shelf")
        return scroll
    return content


def _shelf(window, listings, *, cards: bool = True, category: bool = False) -> Gtk.Widget:
    # Let FlowBox report its actual height-for-width. A fixed breakpoint-bin
    # height based on two columns clips rows when this viewport fits only one.
    return _shelf_layout(window, listings, cards=cards, category=category, wide=False)



def _list_row(window, listing: Listing, *, wide: bool = False) -> Gtk.Widget:
    tagline = _text(listing.tagline, "body" if wide else "meta")
    tagline.set_ellipsize(Pango.EllipsizeMode.END)
    tagline.set_max_width_chars(40 if wide else 30)
    facts = _row(CategoryPill(listing.category))
    if window.fixture or listing.rating > 0 and listing.reviews > 0:
        stars = _text("★★★★★", "meta", weight=400)
        stars.add_css_class("depot-rating-stars")
        facts.append(stars)
    copy = _column(_text(listing.name, "title-2" if wide else "list-title"), tagline, facts)
    copy.set_hexpand(True)
    row = _row(_icon(window, listing, 64 if wide else 48), copy,
               _get_button(window, listing, big=wide), spacing=12)
    row.add_css_class("depot-list-row")
    if wide:
        row.add_css_class("depot-list-row-wide")
    card = Card(row)
    card.add_css_class("depot-explore-card")
    card.set_hexpand(True)
    return _clickable(card, listing.name, lambda: _open(window, listing))


def _update_row(window, listing: Listing) -> Gtk.Widget:
    version = listing.update or "the latest version"
    version_label = _text(f"1.0.159 → {version} · {listing.size}", "caption")
    detail_label = _text("Faster start-up and a fix for screen sharing", "caption")
    for label in (version_label, detail_label):
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(21)
    copy = _column(_text(listing.name, "title-2"), version_label, detail_label, spacing=2)
    copy.set_hexpand(True)
    row = _row(_icon(window, listing, 48), copy, _get_button(window, listing), spacing=14)
    row.add_css_class("depot-update-row")
    adaptive = Adw.BreakpointBin()
    adaptive.set_child(_clickable(Card(row), listing.name, lambda: _open(window, listing)))
    adaptive.set_size_request(0, 86)
    phone = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720px"))
    phone.add_setter(copy, "spacing", 4)
    phone.add_setter(row, "margin-top", 7)
    phone.add_setter(row, "margin-bottom", 7)
    phone.add_setter(adaptive, "height-request", 104)
    adaptive.add_breakpoint(phone)
    return adaptive


def _feature(window, listing: Listing) -> Gtk.Widget:
    # Depot's app-only editorial composition, built over the shared lit card.
    if window.fixture:
        title = _column(_text("Your music,", "editorial"), _text("from everywhere", "editorial"),
                        _text("you keep it.", "editorial"), spacing=0)
        description = _text("Tide plays the files on this computer and your own music server, lit by whatever you’re listening to.",
                            "lead", wrap=True, weight=400)
        eyebrow = "Made for Luma · Editor’s choice"
    else:
        title = _column(_text(listing.name, "editorial"), spacing=0)
        description = _text(listing.tagline, "lead", wrap=True, weight=400)
        eyebrow = "Made for Luma" if listing.luma else "Featured app"
    description.set_max_width_chars(38)
    heading = _column(_text(eyebrow, "label", weight=650), title, description)
    record = window.installed.get(listing.id)
    review = record is not None and record.has_update and window._asks_for_more(record)
    actions = _row() if review else _row(_get_button(window, listing, big=True))
    if listing.rating > 0 and listing.reviews > 0:
        rating_stars = _text("★★★★★", "caption", weight=400)
        rating_stars.add_css_class("depot-rating-stars")
        actions.append(rating_stars)
        actions.append(_text(f"{listing.rating:g}", "meta", weight=400))
    heading.append(actions)
    heading.add_css_class("depot-feature-copy")
    heading.set_hexpand(True)
    for side in ("start", "end", "top", "bottom"):
        getattr(heading, "set_margin_" + side)(24)
    shot_path = _asset(window, listing)
    shot = _picture(shot_path, height=180) if shot_path else _icon(window, listing, 108)
    shot.set_hexpand(bool(shot_path))
    shot.set_halign(Gtk.Align.FILL if shot_path else Gtk.Align.CENTER)
    shot.set_valign(Gtk.Align.FILL if shot_path else Gtk.Align.CENTER)
    shot_frame = Gtk.Box(hexpand=True)
    shot_frame.append(shot)
    for side in ("start", "end", "top", "bottom"):
        getattr(shot_frame, "set_margin_" + side)(16)
    layout = _row(heading, shot_frame, spacing=0)
    layout.set_hexpand(True)
    feature = ContentLitCard(layout, padded=False)
    feature.add_css_class("depot-feature")
    def adapt(width):
        compact = width <= 720
        layout.set_orientation(Gtk.Orientation.VERTICAL if compact else Gtk.Orientation.HORIZONTAL)
        if shot_path:
            shot.set_size_request(0, 148 if compact else 210)
        heading.set_margin_bottom(0 if compact else 24)
    feature._depot_width_watch = WidthWatch(feature, adapt, threshold=720)
    return _clickable(feature, eyebrow + ": " + listing.name, lambda: _open(window, listing))


def _story(window, *, key: str, eyebrow: str, title: str, summary: str, app_ids: tuple[str, ...]) -> Gtk.Widget:
    copy = _column(_text(eyebrow, "label"), _text(title, "title-1", wrap=True),
                   _text(summary, "body", wrap=True))
    apps = tuple(app for app_id in app_ids if (
        app := window.catalogue.find(app_id) or window.catalogue.find("catalog:" + app_id)))
    icons_row = _row(*(_icon(window, _listing(window, app), 52) for app in apps))
    copy.append(icons_row)
    story_body = copy
    if not window.fixture:
        large_icons = _row(*(_icon(window, _listing(window, app), 108) for app in apps), spacing=16)
        large_icons.set_valign(Gtk.Align.CENTER)
        large_icons.set_visible(False)
        visual_layout = _row(copy, Gtk.Box(hexpand=True), large_icons)
        responsive = Adw.BreakpointBin()
        responsive.set_child(visual_layout)
        responsive.set_size_request(1, 122)
        wide = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("min-width: 580px"))
        wide.add_setter(icons_row, "visible", False)
        wide.add_setter(large_icons, "visible", True)
        responsive.add_breakpoint(wide)
        story_body = responsive
    card = Gtk.Box()
    card.set_hexpand(True)
    card.set_overflow(Gtk.Overflow.HIDDEN)
    card.add_css_class("depot-story")
    card.add_css_class(key)
    surface = ContentLitCard(story_body)
    surface.set_hexpand(True)
    surface.set_overflow(Gtk.Overflow.HIDDEN)
    card.append(surface)
    return _clickable(card, title, lambda: window.go("category:" + key.title()))


def render_home(window) -> None:
    listings = _listings(window)
    if not listings:
        window._render_loading()
        return
    by_id = {listing.id: listing for listing in listings}
    featured = by_id.get("tide") if window.fixture else (
        _listing(window, chosen) if (chosen := featured_app(window.catalogue)) else None)
    if featured:
        window.content.append(_feature(window, featured))
    luma = (tuple(listing for listing in listings if listing.luma)[:10] if window.fixture else
            tuple(_listing(window, app) for app in luma_shelf_apps(window.catalogue)))
    window.content.append(_section("Made for Luma", _shelf(window, luma),
                                   subtitle="Free, private, and they work together",
                                   more=lambda: window.go("category:luma")))
    stories = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                          min_children_per_line=1, max_children_per_line=2,
                          row_spacing=12, column_spacing=12)
    stories.set_homogeneous(True)
    stories.insert(_story(window, key="create", eyebrow="Start a studio", title="Make music on Luma",
                          summary=("Record in Sessions, clean up in Audacity, and master anywhere. No subscriptions."
                                   if window.fixture else "Record in Audacity, then listen in Tide. Keep your own files."),
                          app_ids=("sessions", "audacity", "tide") if window.fixture else ("audacity", "tide")), -1)
    stories.insert(_story(window, key="play", eyebrow="Game night", title="Your games already work here",
                          summary="Steam, Epic and GOG libraries run as they are. Stream the rest from your PC.",
                          app_ids=("steam", "heroic", "moonlight")), -1)
    stories.set_hexpand(True)
    stories.add_css_class("depot-stories")
    window.content.append(stories)
    if window.fixture:
        popular_ids = ("blender", "obsidian", "spotify", "signal", "vlc", "steam", "gimp", "discord", "libre")
        popular = tuple(by_id[key] for key in popular_ids if key in by_id)
        popular_measured = True
    else:
        popular_apps_live, popular_measured = popular_apps(window.catalogue)
        popular = tuple(_listing(window, app) for app in popular_apps_live)
    if popular:
        window.content.append(_section("Popular on Luma" if popular_measured else "Explore apps",
                                       _shelf(window, popular, cards=False),
                                       subtitle=("What people install first" if popular_measured else
                                                 "Apps available in this catalogue")))
    category_list = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                                min_children_per_line=1, max_children_per_line=3,
                                row_spacing=12, column_spacing=12)
    category_list.set_hexpand(True)
    category_list.set_homogeneous(True)
    category_list.add_css_class("depot-category-grid")
    for side in ("start", "end", "top", "bottom"):
        getattr(category_list, "set_margin_" + side)(4)
    categories = (("create", "Create", "Make pictures, films and music"),
                  ("work", "Work", "Write, plan and talk"),
                  ("media", "Media", "Watch, listen and look"),
                  ("play", "Play", "Games and the places they live"),
                  ("tools", "Tools", "Keep the computer tidy"))
    for key, name, subtitle in categories:
        badge = CountBadge(sum(item.category == key for item in listings))
        category_icon = Gtk.CenterBox(width_request=48, height_request=48)
        category_icon.add_css_class("depot-category-icon")
        category_icon.add_css_class(key)
        category_icon.set_center_widget(icons.image({
            "create": "palette", "work": "briefcase", "media": "clapperboard",
            "play": "gamepad-2", "tools": "wrench"}[key], pixel_size=22))
        line = _row(category_icon,
                    _column(_text(name, "title-2"), _text(subtitle, "caption", wrap=True)),
                    Gtk.Box(hexpand=True), badge)
        line.add_css_class("depot-category-row")
        card = Card(line)
        card.add_css_class("depot-category-card")
        category_list.insert(_clickable(card, name, lambda k=key: window.go("category:" + k.title())), -1)
    window.content.append(_section("Categories", category_list))


def render_category(window, key: str) -> None:
    listings = tuple(item for item in _listings(window) if item.category == key.lower() or key == "luma" and item.luma)
    title = "Made for Luma" if key == "luma" else key.title()
    subtitles = {"create": "Make pictures, films and music", "work": "Write, plan and talk",
                 "media": "Watch, listen and look", "play": "Games and the places they live",
                 "tools": "Keep the computer tidy", "luma": "Free, private, and they work together"}
    glyphs = {"create": "palette", "work": "briefcase", "media": "clapperboard",
              "play": "gamepad-2", "tools": "wrench", "luma": "sparkles"}
    back = _button("Discover", lambda: window.go_back() if window.history else window.go("home"))
    icon_box = Gtk.CenterBox(halign=Gtk.Align.START, valign=Gtk.Align.CENTER)
    icon_box.set_size_request(56, 56)
    icon_box.add_css_class("depot-category-icon")
    icon_box.add_css_class(key.lower())
    category_glyph = icons.image(glyphs[key.lower()])
    category_glyph.set_pixel_size(26)
    category_glyph.set_halign(Gtk.Align.CENTER)
    category_glyph.set_valign(Gtk.Align.CENTER)
    icon_box.set_center_widget(category_glyph)
    heading = _row(icon_box, _column(_text(title, "hero"),
                                   _text(f"{subtitles[key.lower()]} · {len(listings)} apps", "caption")), spacing=16)
    window.content.append(_column(back, heading, spacing=14))
    luma = tuple(item for item in listings if item.luma)
    other = tuple(item for item in listings if not item.luma)
    if luma:
        featured_section = _section("Made for Luma" if key != "luma" else "",
                                    _shelf(window, luma, category=True), gap=10)
        if other:
            featured_section.set_margin_bottom(13)
        window.content.append(featured_section)
    if other:
        window.content.append(_section("From other developers" if luma else f"Everything in {title}",
                                       _shelf(window, other, cards=False), gap=4))


def render_search(window) -> None:
    listings = search(_listings(window), window.view_argument)
    title = f"{len(listings)} result" + ("" if len(listings) == 1 else "s") if listings else "No results"
    window.content.append(_column(_text(title, "hero"), _text(f"for “{window.view_argument}”", "body", muted=True)))
    if listings:
        window.content.append(_shelf(window, listings, cards=False))
    else:
        window.content.append(_text("Try a different word, or browse a category.", "body"))


def _sort_mine(window, key: str, direction: str) -> None:
    window.mine_sort = (key, direction)
    window.render()


def _mine_row(window, header: TableHeader, listing: Listing) -> Gtk.Widget:
    name_label = _text(listing.name, "body")
    name_label.set_valign(Gtk.Align.CENTER)
    name = _row(_icon(window, listing, 28), name_label)
    category = CategoryPill(listing.category)
    kind = _text(listing.kind_label, "body", muted=True)
    size = _text(listing.size, "body", muted=True)
    size.set_xalign(1)
    kind.set_valign(Gtk.Align.CENTER)
    size.set_valign(Gtk.Align.CENTER)
    more = _more_button(window, listing)
    window._uninstall_buttons[listing.id] = more
    actions = _row(_get_button(window, listing), more)
    row = _row(name, category, kind, size, actions, spacing=0)
    row.add_css_class("depot-table-row")
    header.align(row)
    return _clickable(row, listing.name, lambda: _open(window, listing))


def render_mine(window) -> None:
    listings = _listings(window)
    installed = tuple(item for item in listings if item.installed)
    window.content.append(_column(_text("Your apps", "hero"),
                                   _text(f"{len(installed)} on this computer. Manage app updates in Updates. System updates are listed separately.",
                                         "body", wrap=True, muted=True)))
    key, direction = getattr(window, "mine_sort", ("name", "ascending"))
    header = TableHeader([Column("name", "Name", expand=True), Column("cat", "Category", width=84),
                          Column("kind", "Installed as", width=121), Column("size", "Size", width=70, end=True),
                          Column(None, "", width=134)], sort=(key, direction),
                         on_sort=lambda k, d: _sort_mine(window, k, d), density="regular")
    header.set_name("dp-table-header")
    header.add_css_class("depot-installed-header")
    table = _column(header, spacing=0)
    table.set_hexpand(True)
    rows = []
    order = {"ascending": 1, "descending": -1}[direction]
    for item in installed_sorted(installed, key, order):
        row = _mine_row(window, header, item)
        table.append(row)
        rows.append(row)
    def adapt(width):
        visible = width > 900
        for cell in (header.cells[1], header.cells[2]):
            cell.set_visible(visible)
        for row in rows:
            cell = row.get_first_child().get_next_sibling()
            cell.set_visible(visible)
            cell.get_next_sibling().set_visible(visible)
    table._depot_width_watch = WidthWatch(table, adapt, threshold=900)
    # Direct content height allows all installed rows to participate in the
    # outer vertical adjustment instead of being clipped inside a bin.
    window.content.append(table)



def render_updates(window) -> None:
    fixture = bool(window.fixture)
    system_state = window.system.state if window.system is not None else None
    ready = fixture or bool(system_state and (system_state.available or system_state.staged or
                                             system_state.restart_required))
    if fixture:
        title = "Luma 1.0 · nightly September 22"
        summary = "Adjustable scroll speed for mice and touchpads, plus 14 more changes."
    elif system_state is None:
        title, summary = "Checking this computer…", "Looking for the system update service."
    elif not system_state.service:
        title, summary = "System updates are not set up", "Luma’s update service is not installed here."
    elif not system_state.managed and not ready:
        title, summary = "Not following a Luma update channel", "Choose a channel to receive system updates."
    elif system_state.restart_required and not system_state.staged:
        title, summary = "Restart to finish", "A system change is waiting for a restart."
    elif ready:
        title = (system_state.offered_name or system_state.offered_version or "Luma update")
        summary = system_state.available_summary or ("An update is ready to install." if system_state.staged
                                                      else "An update is available.")
    else:
        title, summary = "Luma is up to date", "There is no system update waiting."
    title_label = _text(title, "title-1", wrap=True, weight=750)
    title_label.set_max_width_chars(22)
    summary_label = _text(summary, "body", wrap=True)
    summary_label.set_max_width_chars(30)
    identity = _column(_text("SYSTEM UPDATE", "label"), title_label, summary_label)
    identity.set_hexpand(True)
    identity.add_css_class("depot-system-update-copy")
    system_icon = (_picture(str(Path(os.environ.get("LUMA_DEPOT_STUDIO_ROOT", str(
        Path.home() / "Documents/LumaDesign/studio/mockups"))) / "assets/luma-favicon.svg"),
                            width=64, height=64) if fixture else
                   _icon(window, Listing("depot", "Luma", "", "work", icon="app-store"), 64))
    system_icon.set_halign(Gtk.Align.START)
    system_icon.set_valign(Gtk.Align.START)
    system_icon.add_css_class("depot-system-icon")
    header = _column(_row(system_icon,
                          identity, spacing=18), spacing=18)
    phase = getattr(window, "fixture_system_phase", "ready") if fixture else (
        "done" if system_state and (system_state.staged or system_state.restart_required) else
        "run" if system_state and system_state.downloading else "ready")
    if phase == "run":
        progress = getattr(window, "fixture_system_progress", 0) / 100 if fixture else system_state.progress
        actions = _column(ProgressLine(progress, size="hero"),
                          _text(f"Downloading {round(progress * 100)}% · you can keep working. Luma asks before it restarts.",
                                "caption", wrap=True))
    elif phase == "done":
        check = icons.image("check", pixel_size=14)
        check.add_css_class("depot-ready-check")
        status = _row(check, _text("Ready. Restart whenever you like.", "meta"), spacing=6)
        actions = _row(_button("Restart now", window._begin_system_update, primary=True),
                       status)
    elif not fixture and (system_state is None or not system_state.service or not system_state.managed):
        actions = Gtk.Box()
    else:
        action_buttons = _row(_button("Update now" if fixture else "Download" if ready else "Check again",
                                      window._begin_system_update, primary=True))
        if fixture:
            action_buttons.append(_button("What’s new", lambda: window._toggle_whats_new()))
            actions = _row(action_buttons, _text("312 MB · you can keep working", "caption"))
        else:
            if system_state and system_state.notes_url:
                action_buttons.append(_button("What’s new", window._toggle_whats_new))
            actions = action_buttons
    header.append(actions)
    expanded_news = (fixture and window.show_whats_new or not fixture and
                     bool(system_state and system_state.notes_url and window.show_whats_new))
    if expanded_news:
        header.set_spacing(14)
        if fixture:
            notes = (("Feature", "Adjustable scroll speed", "Set how fast mice and touchpads scroll, in Settings › Mouse and touchpad."),
                     ("Improvement", "Filer’s tray remembers", "What you were carrying is still there after a restart."),
                     ("Improvement", "Tide fades between tracks", "Choose how long in Tide’s settings, or turn it off."),
                     ("Improvement", "Faster wake from sleep", "Opening the lid gets you back to work about a second sooner."),
                     ("Fix", "11 fixes", "For things you told us about, from Calendar to Quick Options."),
                     ("Security", "2 security fixes", "Both in system libraries. Nothing you need to do."))
        else:
            release = (window.system_release_notes if window.system_release_notes_url == system_state.notes_url
                       else None)
            notes = tuple((section.key.title(), note.summary, note.details)
                          for section in release.sections for note in section.notes) if release else ()
            if not window.show_all_system_notes:
                notes = notes[:6]
        note_grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                                min_children_per_line=3, max_children_per_line=3,
                                homogeneous=False,
                                row_spacing=8, column_spacing=8)
        note_grid.add_css_class("depot-note-grid")
        actions.set_size_request(0, 40)
        actions.set_margin_top(4)
        note_grid.set_margin_top(4)
        for kind, headline, detail in notes:
            glyph = ({"Adjustable scroll speed": "sparkles", "Filer’s tray remembers": "folder",
                      "Tide fades between tracks": "music-2", "Faster wake from sleep": "zap",
                      "11 fixes": "wrench", "2 security fixes": "shield-check"}[headline]
                     if fixture else {"Feature": "sparkles", "Improvement": "zap",
                                      "Fix": "wrench", "Security": "shield-check"}.get(kind, "file-text"))
            detail_label = _text(detail, "small", wrap=True)
            detail_label.set_max_width_chars(26)
            detail_label.set_ellipsize(Pango.EllipsizeMode.END)
            detail_label.set_lines(3)
            note_icon = Gtk.CenterBox(width_request=32, height_request=32,
                                      valign=Gtk.Align.START, halign=Gtk.Align.START)
            glyph_image = icons.image(glyph)
            note_icon.set_center_widget(glyph_image)
            note = ContentLitCard(_row(note_icon,
                                       _column(_text(kind.upper(), "label"),
                                               _text(headline, "list-title", weight=650),
                                               detail_label, spacing=2), spacing=12))
            note.set_size_request(272, -1)
            note_grid.insert(note, -1)
        if notes:
            header.append(note_grid)
            if fixture:
                header.append(_button("Read the full release notes", lambda: Toast.show(
                    window, "The full notes open in Viola")))
            elif window.system_release_notes.count > len(notes):
                header.append(_button("Read the full release notes", window._show_all_system_notes))
        elif not fixture:
            header.append(_text(window.system_release_notes_error or "Loading release notes…", "body"))
    update = Gtk.Overlay()
    update.set_child(Gtk.Box())
    header.set_hexpand(True)
    content = header
    if expanded_news:
        adaptive = Adw.BreakpointBin()
        adaptive.set_child(header)
        adaptive.set_size_request(0, 429 if fixture else -1)
        phone = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720px"))
        phone.add_setter(actions, "orientation", Gtk.Orientation.VERTICAL)
        if notes:
            phone.add_setter(note_grid, "min-children-per-line", 1)
            phone.add_setter(note_grid, "max-children-per-line", 1)
        adaptive.add_breakpoint(phone)
        content = adaptive
    content.set_margin_top(20)
    content.set_margin_bottom(20)
    content.set_margin_start(28)
    content.set_margin_end(28)
    update.add_overlay(content)
    update.set_measure_overlay(content, True)
    update.set_overflow(Gtk.Overflow.HIDDEN)
    update.add_css_class("depot-system-update")
    update.set_name("dp-system-update")
    window.content.append(update)
    # The same real offers counted by the sidebar must be visible here.
    # These existing provider-owned blocks retain their retry, safety and
    # authentication paths while the surrounding page uses shared chrome.
    if not fixture:
        if window.system is not None and window._os_needs_attention() and not ready:
            window.content.append(window._system_block())
        if window.firmware is not None and (window.firmware.updates or window.firmware.failures):
            window.content.append(window._hardware_block())
        failed, held, manual, running, paused = window._app_attention()
        if failed or held or running or paused:
            window.content.append(window._apps_attention_block(failed, held, manual, running, paused))
    changed = tuple(item for item in _listings(window) if item.installed and item.update)
    app_rows = _column(*(_update_row(window, item) for item in changed)) if changed else _text("Every app is up to date", "body")
    ready_apps = tuple(window.installed[item.id] for item in changed
                       if item.id in window.installed and not window._asks_for_more(window.installed[item.id]))
    app_section = _section("Apps", app_rows, subtitle=f"{len(changed)} update" +
                           ("" if len(changed) == 1 else "s") if changed else "",
                           more=(lambda: [window._update(item.app_id, approve=False, expected_commit=item.update_commit,
                                                        expected_installed_commit=item.commit) for item in ready_apps]) if ready_apps else None,
                           more_label="Update all")
    window.content.append(app_section)
    if fixture:
        history = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                              min_children_per_line=1, max_children_per_line=2,
                              row_spacing=4, column_spacing=16)
        history_compact = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720px"))
        for app_id, name, version, when in (
            ("discord", "Discord", "1.0.158 → 1.0.159", "Yesterday"),
            ("obsidian", "Obsidian", "1.7.4 → 1.7.5", "Sep 18"),
            ("spotify", "Spotify", "1.2.47 → 1.2.48", "Sep 18")):
            listing = next(item for item in _listings(window) if item.id == app_id)
            app_icon = _icon(window, listing, 40)
            app_icon.set_margin_start(7)
            detail = _text(f"Updated automatically · {version} · {when}", "caption")
            detail.set_max_width_chars(40)
            detail.set_ellipsize(Pango.EllipsizeMode.END)
            history_compact.add_setter(detail, "max-width-chars", 28)
            history.insert(_row(app_icon,
                                _column(_text(name, "title-2"), detail),
                                Gtk.Box(hexpand=True), _button("Go back", lambda name=name: Toast.show(
                                    window, f"Went back to the previous version of {name}")), spacing=10), -1)
        history_bin = Adw.BreakpointBin()
        history_bin.set_child(history)
        history_bin.set_size_request(0, 120)
        history_compact.add_setter(history_bin, "height-request", 175)
        history_bin.add_breakpoint(history_compact)
        window.content.append(_section("Recently updated", history_bin))
        fact_rows = []
        for key, value in (("Running", "Luma 1.0 · nightly September 16"),
                           ("Channel", "Nightly"), ("Updates from", "dl.simplyluma.com"),
                           ("Signature", "Verified"), ("Last checked", "6 hours ago")):
            fact = _row(_text(key, "caption"), Gtk.Box(hexpand=True), _text(value, "body"))
            fact.add_css_class("depot-computer-fact")
            fact_rows.append(fact)
        facts = Card(_column(*fact_rows, spacing=0))
        window.content.append(_section("This computer", facts))
    else:
        # Keep the real update owners reachable in the shared Luma layout.
        # Preview history never supplies an installed app's rollback action.
        window.content.append(window._history_block())
        facts = window._system_facts_block()
        if facts is not None:
            window.content.append(facts)
        channels = window._channel_block()
        if channels is not None:
            window.content.append(channels)
        window.content.append(window._automatic_updates_block())


def render_app(window) -> None:
    app = window.catalogue.find(window.view_argument) if window.catalogue else None
    if app is None:
        window._render_failure()
        return
    listing = _listing(window, app)
    window.content.append(_button("Discover", lambda: window.go_back() if window.history else window.go("home")))
    record = window.installed.get(listing.id)
    review = record is not None and record.has_update and window._asks_for_more(record)
    actions = _row() if review else _row(_get_button(window, listing, big=True))
    if listing.installed:
        more = _more_button(window, listing, big=True)
        actions.append(more)
        installed_mark = icons.image("check")
        installed_mark.add_css_class("depot-installed-mark")
        actions.append(_row(installed_mark,
                            _text("Installed with Luma" if listing.luma else "Installed", "caption"), spacing=5))
    else:
        actions.append(_text(listing.size, "caption"))
    byline = _row(_text("Luma" if listing.luma else "Community package", "caption"))
    if listing.luma:
        verified_mark = icons.image("badge-check")
        verified_mark.add_css_class("depot-verified-mark")
        byline.append(verified_mark)
    byline.append(CategoryPill(listing.category))
    tagline = _text(listing.tagline, "body", wrap=True)
    header_copy = _column(_text(listing.name, "app-title"), byline, tagline, actions)
    header_copy.set_hexpand(True)
    app_icon = _icon(window, listing, 108)
    header = _row(app_icon, header_copy, spacing=24)
    facts = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True)
    rating_fact = ((f"{listing.rating:g}", f"{listing.reviews:,} ratings"),) if (
        window.fixture or listing.rating > 0 and listing.reviews > 0) else (("—", "No ratings yet"),)
    for value, label in rating_fact + (
        (listing.size, "Size"),
        ("Luma" if listing.luma else "Open source", "Made by" if listing.luma else "Licence"),
        (listing.kind_label, "Package")):
        stars = _text("★★★★★", "meta", weight=400)
        stars.add_css_class("depot-rating-stars")
        fact = _column(_text(value, "title-2"),
                       *([stars] if label.endswith("ratings") else []),
                       _text(label, "caption"))
        fact.set_halign(Gtk.Align.CENTER)
        facts.append(fact)
    body = _column(header, Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), facts, spacing=20)
    header.set_vexpand(True)
    facts.set_valign(Gtk.Align.END)
    adaptive = Adw.BreakpointBin()
    adaptive.set_child(body)
    adaptive.set_size_request(0, -1)
    adaptive.set_margin_top(30)
    adaptive.set_margin_start(32)
    adaptive.set_margin_end(32)
    adaptive.set_margin_bottom(22)
    phone = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720px"))
    phone.add_setter(actions, "orientation", Gtk.Orientation.VERTICAL)
    phone.add_setter(actions, "margin-top", 32)
    phone.add_setter(tagline, "label", "\n".join(textwrap.wrap(
        listing.tagline, width=20, break_long_words=False, break_on_hyphens=False)))
    phone.add_setter(tagline, "halign", Gtk.Align.START)
    phone.add_setter(adaptive, "height-request", 392)
    adaptive.add_breakpoint(phone)
    card = Gtk.Overlay()
    card.set_child(Gtk.Box())
    wash = ContentLitHeader(picture=app_icon.paintable,
                            hue=220 if window.fixture and listing.id == "tide" else None,
                            tone=listing.category.casefold(), name=listing.name)
    wash.set_valign(Gtk.Align.FILL)
    card.add_overlay(wash)
    card.add_overlay(adaptive)
    card.set_measure_overlay(adaptive, True)
    card.set_overflow(Gtk.Overflow.HIDDEN)
    card.add_css_class("depot-app-header")
    card.add_css_class(listing.category)
    card.set_name("dp-app-header")
    card.set_size_request(-1, 314)
    phone.add_setter(card, "height-request", 444)
    window.content.append(card)
    if review:
        window.content.append(window._permission_notice(app, record))
    job = window.jobs.get(listing.id)
    if job is not None and job.failed:
        window.content.append(window._detail_actions(app))
    paths = tuple(path for index in range(3) if (
        path := _asset(window, listing, light=index == 1, index=index)))
    if paths:
        pictures = [_picture(path, width=220, height=180) for path in paths]
        shots = _row(*pictures, spacing=12)
        shots.set_homogeneous(True)
        shots.add_css_class("depot-screenshots")
        shot_bin = Adw.BreakpointBin()
        shot_bin.set_child(shots)
        shot_bin.set_size_request(0, 180)
        shot_bin.set_margin_bottom(4)
        shot_phone = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720px"))
        for picture in pictures:
            shot_phone.add_setter(picture, "width-request", 0)
            shot_phone.add_setter(picture, "height-request", 70)
        shot_phone.add_setter(shot_bin, "height-request", 70)
        shot_bin.add_breakpoint(shot_phone)
        window.content.append(shot_bin)
    columns = _row(spacing=30)
    description_text = _text(listing.tagline + (". It comes with Luma, updates with Luma, and works with the rest of your apps: share from anywhere, find it in search, and pick up where you left off on your phone."
                                                if listing.luma else ". It’s packaged for Luma and kept up to date automatically. Updates are checked and signed before they reach you.")
                             if window.fixture else app.description or app.summary, "body", wrap=True)
    description_text.set_max_width_chars(60)
    description = _column(_text("Description", "title-2"), description_text,
                          _text("Ratings and reviews", "title-2"))
    if window.fixture:
        for name, review in (("Priya", "Finally, my Navidrome server and my laptop in one place. The album colour thing is gorgeous."),
                             ("Theo", "Gapless playback that actually works. The queue survives a restart.")):
            review_text = _text(review, "body", wrap=True)
            review_text.set_max_width_chars(60)
            review_stars = _text("★★★★★", "meta", weight=400)
            review_stars.add_css_class("depot-rating-stars")
            description.append(Card(_column(_text(name, "title-2"), review_stars, review_text)))
    description.set_size_request(486, -1)
    columns.append(description)
    details = _column(_text("Details", "title-2"))
    details.set_hexpand(True)
    values = (("Version", "1.0 (nightly 0922)" if listing.luma else "2.4.1"),
              ("Updated", "Sep 22, 2026"), ("Size", listing.size),
              ("Made by", "Luma" if listing.luma else f"{listing.name} developers"),
              ("Licence", "Apache 2.0" if listing.luma else "GPL 3.0"),
              ("Package", listing.kind_label)) if window.fixture else (
                  ("Version", window.installed.get(app.app_id).version if app.app_id in window.installed else ""),
                  ("Updated", app.releases[0].date if app.releases else ""), ("Size", listing.size),
                  ("Made by", app.developer), ("Licence", app.licence), ("Package", listing.kind_label))
    for key, value in values:
        if value:
            details.append(_row(_text(key, "caption"), Gtk.Box(hexpand=True), _text(value, "body")))
    columns.append(details)
    columns.add_css_class("depot-detail-columns")
    column_bin = Adw.BreakpointBin()
    column_bin.set_child(columns)
    column_bin.set_size_request(0, 500)
    column_bin.set_margin_top(16)
    column_phone = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720px"))
    column_phone.add_setter(columns, "orientation", Gtk.Orientation.VERTICAL)
    column_phone.add_setter(description, "width-request", 0)
    column_bin.add_breakpoint(column_phone)
    window.content.append(column_bin)
    more = tuple(item for item in _listings(window) if item.id != listing.id and item.category == listing.category)[:4]
    if more:
        window.content.append(_section("More from Luma" if listing.luma else "You might also like", _shelf(window, more)))
