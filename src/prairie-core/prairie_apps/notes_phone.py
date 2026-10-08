# SPDX-License-Identifier: Apache-2.0
"""Notes at phone width (simulator v71, `?phone=1&app=notes`).

Under 560 px Notes is list first, like Memos and Messages:

- The home is the list: a large "Notes" title with the folder you're in as a
  small dropdown beside it (it grows All notes, the folder tree with counts and
  New folder from the bar), then Pinned and Notes as cards: the title and the
  people in it, when, two lines of its text, its folder. The bar is Search and
  New.
- Opening a note pushes it. Its title island names where Back goes (the
  folder), with who is here and when it was edited under it; tapping it grows
  the note's details: Pin, Copy link, Move, Duplicate; the people; Export and
  Delete under a hairline. The in-page folder chip, the edited line and the
  empty "Add icon" slot go; the title starts 20 under the island.
- The bar is for writing: Aa (formatting grows from it), Checklist, Add a
  photo, a hairline, then New note as the primary.

Structure belongs to LumaUI: ListFirst (the push and Back), TitleIsland, the
action center's grown panels and search, the tier watch. The large title stays
the app's own row because the folder dropdown sits beside it.
Nothing here writes data of its own; every action is one Notes already had.
"""
from __future__ import annotations

import os

from gi.repository import GLib, Gtk, Pango

from luma_appkit import (
    ActionCenter, AvatarStack, BarAction, BarSearch, BarTile, BarTiles, CountBadge, Mark, MarkValue, PanelHeading, PanelRow,
    PersonAvatar, ScrollView, SEPARATOR, RULE, TitleIsland, Toast, ToastHost, apply_type, icons, panel_list,
)
from luma_appkit.action_bubble import MenuItem
from luma_appkit.structure_adapt import WidthWatch, window_tier, window_width

#: v71 Notes folds its sidebar at 720 (@container win (max-width: 720px) { .nside { display: none } }).
COMPACT_MAX = 720


def _when(raw_at, modified_at):
    """A card's time: v71 drops "Today, " (10:40 AM) and keeps Yesterday and dates."""
    if raw_at:
        return raw_at.replace('Today, ', '')
    from datetime import datetime
    try:
        edited = datetime.fromisoformat(modified_at).astimezone()
    except (TypeError, ValueError):
        return ''
    now = datetime.now().astimezone()
    if (now - edited).total_seconds() < 60:
        return 'Just now'
    if edited.date() == now.date():
        return edited.strftime('%-I:%M %p')
    return edited.strftime('%b %-d')


class NotesPhone:
    """Mixed into NotesLumaWindow: the phone shape, and redrawing when a window crosses 560."""

    # ── setup ─────────────────────────────────────────────────────────────

    def _install_phone(self):
        self.phone = False
        self.phone_page = 'list'
        self.phone_folder = None
        self.phone_query = ''
        self._build_phone_list()
        self.lists.append(self.phone_list_host)
        self.phone_list_host.set_visible(False)
        self.phone_island = TitleIsland('Notes', '', lead='back', lead_label='All notes',
                                        on_lead=self._phone_back, grow=self._phone_details, grows='details')
        self.phone_island.set_name('nt-title-island')
        self.phone_island.float_over(self.canvas)
        self.layout.attach_island(self.phone_island)
        self.phone_island.connect('notify::visible', lambda *_a: self._phone_island_shown())
        self._tier = window_tier(self)
        self._tier.connect('tier-changed', lambda _w, tier: (self._set_phone(tier == 'phone'), self._fold_compact()))
        self._tier.schedule()
        # v71 folds Notes' sidebar at 720, inside the compact tier.
        self._compact_watch = WidthWatch(self, lambda _width: self._fold_compact(), threshold=COMPACT_MAX)

    def _fold_compact(self):
        """At 720 and under the sidebar folds away; wider, it comes back. F9 still toggles it.

        Never at phone width, where the toggle's own answer would be a drawer."""
        if self.phone:
            return
        width = window_width(self)
        if width <= 0:
            return
        want = width > COMPACT_MAX
        if self.sidebar_toggle.get_active() != want:
            self.sidebar_toggle.set_active(want)

    def _set_phone(self, phone):
        if phone == self.phone:
            return
        self.phone = phone
        if phone and self.fixture and not getattr(self, '_phone_fixture_done', False):
            GLib.idle_add(lambda: (self._phone_fixture_state(), False)[1])
        # The desktop folder tree is not a phone drawer (v71: list first); F9 is the desktop's.
        if getattr(self, 'sidebar_toggle', None) is not None:
            self.sidebar_toggle.set_sensitive(not phone)
        self._sidebar_room()
        if not phone:
            self.phone_page = 'list'
        self._show_phone_page()
        self._render_tools()
        if self.current is not None:
            self._render_subject()

    def _phone_fixture_state(self):
        """Fixture only (the gate's phone states): open the note page, then grow one thing."""
        if getattr(self, '_phone_fixture_done', False) or not self.phone:
            return
        self._phone_fixture_done = True
        if os.environ.get('LUMA_NOTES_PHONE_OPEN') == '1' and self.current is not None:
            self.phone_page = 'note'
            self._show_phone_page()
            self.set_focus(None)
        grow = os.environ.get('LUMA_NOTES_PHONE_GROW', '')
        if grow == 'folders' and self.phone_page == 'list':
            self._phone_folders()
        elif grow == 'island' and self.phone_page == 'note':
            self.phone_island.grow_into()
        elif grow == 'format' and self.phone_page == 'note':
            item = next(i for i in self.phone_tool_items if isinstance(i, BarAction) and i.key == 'format')
            self.format_toolbar.grow('format', item.panel)
        elif grow == 'move' and self.phone_page == 'note':
            self._phone_move()

    # ── the list ──────────────────────────────────────────────────────────

    def _build_phone_list(self):
        self.phone_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.phone_list.set_name('nt-phone-list')
        head = Gtk.Box(valign=Gtk.Align.START)
        head.set_name('nt-phone-head')
        title = apply_type(Gtk.Label(label='Notes', xalign=0, hexpand=True), 'page-title')
        title.set_name('nt-phone-title')
        title.set_valign(Gtk.Align.BASELINE)
        head.append(title)
        self.phone_folder_button = Gtk.Button(valign=Gtk.Align.END)
        self.phone_folder_button.set_name('nt-phone-folder')
        self.phone_folder_button.add_css_class('nt-phone-folder')
        self.phone_folder_button.connect('clicked', lambda _b: self._phone_folders())
        head.append(self.phone_folder_button)
        self.phone_list.append(head)
        self.phone_cards = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.phone_cards.set_name('nt-phone-cards')
        self.phone_list.append(self.phone_cards)
        self.phone_list_scroll = ScrollView(self.phone_list, vexpand=True)
        self.phone_list_scroll.set_name('nt-phone-scroll')
        self.phone_list_host = ToastHost(self.phone_list_scroll)
        self.phone_list_host.set_hexpand(True)
        self.phone_list_host.set_name('nt-phone-home')
        self.phone_list_bar = ActionCenter().attach(self.phone_list_host)
        self.phone_list_bar.set_name('nt-list-bar')

    def _phone_list_bar(self):
        """Search (the well that takes the spare width) and New, the primary (v71 `.nlbar`)."""
        search = BarSearch('Search', text=self.phone_query, opens=True, on_change=self._phone_search)
        new = BarAction('square-pen', 'New', primary=True, keep_label=True, on_activate=self.create_note)
        self.phone_list_bar.show_bar([search, new], fill=True)
        for child in _children(self.phone_list_bar.bar_row):
            item = getattr(child, 'bar_item', None)
            if item is search:
                child.set_name('nt-list-search')
            elif item is new:
                child.set_name('nt-list-new')

    def _phone_search(self, text):
        self.phone_query = text or ''
        self._render_phone_cards()

    def _phone_notes(self):
        notes = self.store.list_notes(search=self.phone_query)
        if self.phone_folder:
            parents = self.store.folder_parents()

            def inside(folder_id):
                while folder_id:
                    if folder_id == self.phone_folder:
                        return True
                    folder_id = parents.get(folder_id)
                return False
            notes = tuple(n for n in notes if inside(n.folder_id))
        return notes

    def _render_phone_list(self):
        name = self.store.get_folder(self.phone_folder).name if self.phone_folder else 'All notes'
        face = Gtk.Box()
        face.add_css_class('nt-phone-folder-face')
        if self.phone_folder:
            face.append(Mark(value=MarkValue('dot', self._folder_hue(self.phone_folder)), density='filter'))
        face.append(Gtk.Label(label=name))
        face.append(icons.image('chevron-down'))
        self.phone_folder_button.set_child(face)
        self.phone_folder_button.update_property([Gtk.AccessibleProperty.LABEL], [f'Folder: {name}'])
        self._render_phone_cards()

    def _render_phone_cards(self):
        _clear(self.phone_cards)
        notes = self._phone_notes()
        pinned = [n for n in notes if n.favorite]
        rest = [n for n in notes if not n.favorite]
        if pinned:
            self.phone_cards.append(self._phone_section('Pinned', 'pin'))
            for note in pinned:
                self.phone_cards.append(self._phone_card(note))
        if rest:
            if pinned:
                section = self._phone_section('Notes')
                section.add_css_class('after-cards')
                self.phone_cards.append(section)
            for note in rest:
                self.phone_cards.append(self._phone_card(note))
        if not notes:
            hint = apply_type(Gtk.Label(label='No notes match.' if self.phone_query else 'No notes here yet.',
                                        xalign=0), 'body', muted=True)
            hint.set_name('nt-phone-empty')
            self.phone_cards.append(hint)

    def _phone_section(self, label, icon=None):
        row = Gtk.Box()
        row.add_css_class('nt-phone-section')
        if icon:
            row.append(icons.image(icon))
        row.append(Gtk.Label(label=label, xalign=0))
        return row

    def _phone_card(self, note):
        raw = self.store.raw_notes.get(note.id, {}) if self.fixture else {}
        card = Gtk.Button()
        card.add_css_class('nt-phone-card')
        card.set_name('nt-card-' + note.id)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        top = Gtk.Box()
        top.add_css_class('nt-phone-card-head')
        top.append(Gtk.Label(label=note.display_title, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
        shares = raw.get('share', [])
        if shares:
            people = self._fixture_people()
            top.append(AvatarStack([people[k] for k in shares], size='small'))
        box.append(top)
        text = ' '.join(note.plain_text.replace('￼', '').split())[:90] or 'No text yet'
        when = _when(raw.get('at'), note.modified_at)
        if raw.get('live') and os.environ.get('LUMA_NOTES_LIVE_SETTLED') == '1':
            when = 'Just now'
        line = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, lines=2,
                         ellipsize=Pango.EllipsizeMode.END)
        esc = GLib.markup_escape_text
        line.set_markup(f'<span weight="600">{esc(when)}</span>  {esc(text)}' if when else esc(text))
        line.add_css_class('nt-phone-card-text')
        box.append(line)
        folder = Gtk.Box()
        folder.add_css_class('nt-phone-card-folder')
        folder.append(Mark(value=MarkValue('dot', self._folder_hue(note.folder_id) if note.folder_id else 20), density='card'))
        fname = self.store.get_folder(note.folder_id).name if note.folder_id else 'Notes'
        folder.append(Gtk.Label(label=fname, xalign=0))
        box.append(folder)
        card.set_child(box)
        card.update_property([Gtk.AccessibleProperty.LABEL], [f'{note.display_title}, {when}, {fname}'])
        card.connect('clicked', lambda _b, n=note: self._phone_open(n))
        return card

    def _phone_folders(self):
        """The folder dropdown grows the list bar: All notes, the tree with counts, New folder."""
        notes = self.store.list_notes()
        parents = self.store.folder_parents()
        rows = [PanelRow('All notes', icon='notebook', count=len(notes), current=self.phone_folder is None,
                         on_activate=lambda: self._phone_pick_folder(None))]

        def tree(parent, depth):
            for folder in self.store.list_folders():
                if parents.get(folder.id) != parent:
                    continue
                row = PanelRow(folder.name, lead=Mark(value=MarkValue('dot', self._folder_hue(folder.id))),
                               count=self._folder_count(folder.id, notes), current=self.phone_folder == folder.id,
                               on_activate=lambda f=folder.id: self._phone_pick_folder(f))
                row.set_name('nt-phone-folder-' + folder.id)
                row.set_margin_start(18 * depth)
                rows.append(row)
                tree(folder.id, depth + 1)
        tree(None, 0)
        rows.append(PanelRow('New folder', icon='folder-plus', on_activate=self._new_folder_form))
        self.phone_list_bar.grow('folders', panel_list(rows, label='Folders'), anchor=self.phone_folder_button)

    def _phone_pick_folder(self, folder_id):
        self.phone_folder = folder_id
        self._render_phone_list()

    def _phone_open(self, note):
        if self._open_note(self.store.get_note(note.id)):
            self.phone_page = 'note'
            self._show_phone_page()

    def _phone_back(self):
        if not self._can_leave_document():
            return
        self.phone_page = 'list'
        self._show_phone_page()

    # ── the note ──────────────────────────────────────────────────────────

    def _phone_island_text(self):
        note = self.current
        raw = self.store.raw_notes.get(note.id, {}) if self.fixture else {}
        folder = self.store.get_folder(note.folder_id).name if note.folder_id else 'Notes'
        if self.fixture:
            at = raw.get('at', '')
            settled = raw.get('live') and os.environ.get('LUMA_NOTES_LIVE_SETTLED') == '1'
            edited = 'Edited just now' if settled or at.startswith(('Today', 'Just now')) else f'Edited {at}'
        else:
            when = _when('', note.modified_at)
            edited = 'Edited just now' if when in ('Just now', '') or ':' in when else f'Edited {when}'
        shares = [k for k in raw.get('share', []) if k != 'me']
        if raw.get('live'):
            return folder, f"{self.store.people[raw['live']]['n'].split()[0]} is here · {edited}", True
        if shares:
            return folder, f'Shared with {len(shares)} · {edited}', False
        return folder, edited, False

    def _render_phone_island(self):
        if self.current is None:
            return
        folder, sub, live = self._phone_island_text()
        self.phone_island.set_title(folder, sub)
        # v71 names the lead "All notes" (it returns to the list) and the title "<folder>. Note details".
        self.phone_island.set_lead('back', 'All notes')
        self.phone_island.title_button.update_property([Gtk.AccessibleProperty.LABEL], [f'{folder}. Note details'])
        self.phone_island.set_status("here" if live else None)
        self._phone_island_shown()

    def _phone_island_shown(self):
        # The island is the note page's: the list has its own large title.
        want = self.phone and self.phone_page == 'note' and self.current is not None
        if self.phone_island.get_visible() != want:
            if not want and self.phone_island.grown:
                self.phone_island.fold()
            self.phone_island.set_visible(want)

    def _phone_details(self):
        """What the island grows into (v71 `nIsl`): tiles, the people, then Export and Delete."""
        note = self.current
        raw = self.store.raw_notes.get(note.id, {}) if self.fixture else {}
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_name('nt-island-details')
        box.add_css_class('nt-island-details')
        box.append(BarTiles([
            BarTile('pin', 'Pinned' if note.favorite else 'Pin', lambda: self._phone_folded(
                lambda: self._favorite_note(self.current)), on=note.favorite, name='Pin'),
            BarTile('link', 'Copy link', lambda: self._phone_folded(self._phone_copy_link)),
            BarTile('folder', 'Move', lambda: self._phone_folded(self._phone_move)),
            BarTile('copy', 'Duplicate', lambda: self._phone_folded(self.duplicate_note)),
        ], columns=4, chip=True))
        shares = [k for k in raw.get('share', []) if k != 'me']
        people = self._fixture_people() if self.fixture else {}
        heading = PanelHeading('People')
        heading.label.set_hexpand(False)
        from luma_appkit.lumaui_tokens import STRUCTURE
        heading.set_spacing(STRUCTURE['details']['count_gap'])
        heading.append(CountBadge(len(shares) + 1))
        heading.append(Gtk.Box(hexpand=True))
        rows = [heading]
        for key in ['me'] + shares:
            person = people.get(key)
            name = 'You' if key == 'me' else (person.name if person else key)
            face = PersonAvatar(person.name if person else 'Nick', 34,
                                picture=getattr(person, 'picture', None), hue=getattr(person, 'hue', None))
            rows.append(PanelRow(name, lead=face, closes=False, appearance='person', live=raw.get('live') == key,
                                 subtitle='Owner' if key == 'me' else 'Here now' if raw.get('live') == key else 'Can edit'))
        rows.append(PanelRow('Add people', icon='user-plus', appearance='person', subtitle='By name, @username or email',
                             on_activate=lambda: self._phone_folded(lambda: self._share(self.phone_island))))
        rows.append(None)
        rows.append(PanelRow('Export as Markdown', icon='download',
                             on_activate=lambda: self._phone_folded(lambda: self._export_note(self.current))))
        rows.append(PanelRow('Delete note', icon='trash-2', danger=True,
                             on_activate=lambda: self._phone_folded(self.delete_current)))
        box.append(panel_list(rows, label='Note'))
        return box

    def _phone_folded(self, run):
        self.phone_island.fold()
        run()

    def _phone_copy_link(self):
        if self.fixture:
            Toast.show(self.document_host, 'Link copied', icon='link')
        else:
            # A note's link is the share sheet's to give; it already knows how (Connect).
            self._share(self.phone_island)

    def _phone_more(self):
        """Return from the folder picker to the note actions (v71 data-npx=more)."""
        note = self.current
        if note is None:
            return
        def run(callback):
            self.format_toolbar.fold()
            callback()
        rows = [
            PanelRow('Unpin' if note.favorite else 'Pin to the top', icon='pin',
                     on_activate=lambda: run(lambda: self._favorite_note(self.current))),
            PanelRow('Move to folder', icon='folder', submenu=True,
                     on_activate=self._phone_move, closes=False),
            PanelRow('Duplicate', icon='copy', on_activate=lambda: run(self.duplicate_note)),
            PanelRow('Export as Markdown', icon='download',
                     on_activate=lambda: run(lambda: self._export_note(self.current))),
            PanelRow('Delete note', icon='trash-2', danger=True,
                     on_activate=lambda: run(self.delete_current)),
        ]
        self.format_toolbar.grow('more', panel_list(rows, label='Note actions'))

    def _phone_move(self):
        self._move_menu(anchor=self.phone_island)

    # ── the writing bar ───────────────────────────────────────────────────

    def _phone_tool_items(self, block=()):
        return [
            BarAction('', 'Aa', tooltip='Formatting', text_glyph=True, panel=self._phone_format_panel, key='format'),
            BarAction('list-checks', tooltip='Checklist', active='checklist' in block,
                      on_activate=lambda: self._tool('checklist')),
            BarAction('image', tooltip='Add a photo', on_activate=self._choose_picture_file),
            RULE,
            BarAction('square-pen', tooltip='New note', primary=True, on_activate=self.create_note),
        ]

    def _phone_format_panel(self):
        """Aa: Heading, Text, Quote; B I S, highlight, link; bulleted and numbered (v71 `.nfmt`)."""
        from luma_appkit.action_center import make_control
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.set_name('nt-format-panel')
        panel.add_css_class('nt-format-panel')
        block = self.page.block_of_line(self.page.cursor_line())
        current = 'heading' if 'heading' in block else 'quote' if 'quote' in block else 'text'
        from luma_appkit import PanelChoices
        styles = PanelChoices([('heading', 'Heading'), ('text', 'Text'), ('quote', 'Quote')],
                              selected=current, on_choose=self._set_block_style, document_style=True)
        styles.set_name('nt-format-styles')
        for style, chip in styles.buttons.items():
            chip.set_name('nt-format-' + style)
        panel.append(styles)
        inline = self.page.inline_state()
        grid = Gtk.Grid(column_homogeneous=True, row_spacing=10)
        grid.add_css_class('nt-format-grid')
        for column, (style, icon, label) in enumerate((
                ('bold', 'bold', 'Bold'), ('italic', 'italic', 'Italic'),
                ('strike', 'strikethrough', 'Strikethrough'), ('highlight', 'highlighter', 'Highlight'),
                ('link', 'link', 'Link'))):
            button = make_control(BarAction(icon, tooltip=label, active=bool(inline.get(style)),
                                            on_activate=lambda s=style: self._tool(s)))
            button.set_name('nt-format-' + style)
            grid.attach(button, column, 0, 1, 1)
        for column, (style, icon, label) in enumerate((('bulleted', 'list', 'Bulleted list'),
                                                       ('numbered', 'list-ordered', 'Numbered list'))):
            button = make_control(BarAction(icon, tooltip=label, active=style in block,
                                            on_activate=lambda s=style: self._tool(s)))
            button.set_name('nt-format-' + style)
            grid.attach(button, column, 1, 1, 1)
        panel.append(grid)
        return panel

    # ── the two shapes ────────────────────────────────────────────────────

    def _sidebar_room(self):
        self.sidebar.set_visible(getattr(self, '_sidebar_shown', True) and not getattr(self, 'phone', False))

    def _show_phone_page(self):
        listing = self.phone and self.phone_page == 'list'
        self.phone_list_host.set_visible(self.phone)
        # On a phone the list is v71's #n-side; the folder tree steps aside under another name.
        self.sidebar.set_name('nt-tree' if self.phone else 'nt-sidebar')
        self.lists.set_name('nt-sidebar' if self.phone else 'nt-lists')
        self.layout.show_list() if listing else self.layout.show_detail()
        if listing:
            self._phone_list_bar()
            self._render_phone_list()
        self._render_phone_island()
        self._phone_island_shown()


def _children(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


def _clear(box):
    while (child := box.get_first_child()) is not None:
        box.remove(child)
