# SPDX-License-Identifier: Apache-2.0
"""Keep Messages' established transports while its window uses LumaUI."""

from __future__ import annotations

from gi.repository import GLib

from .messages import MessagesWindow
from .messages_port import MessagesSurface
from .messages_preferences import ConversationPreferences


class LumaUIMessagesWindow(MessagesWindow):
    """The existing message controller with one LumaUI view over its records.

    Messages' network, modem, scroll and delivery code remains the authority.
    This class only observes those updates and presents them through kit parts.
    Fixture mode replaces every service and store before the controller starts.
    """

    def __init__(self, application) -> None:
        self.surface: MessagesSurface | None = None
        self._pending_unread: dict[tuple[str, str], object] = {}
        super().__init__(application)
        fixture = self.store if self._fixture_mode else None
        self.surface = MessagesSurface(self, fixture)
        self.split = self.surface.split

    def _reload_threads(self) -> None:
        if self.surface is None:
            super()._reload_threads()
            return
        if getattr(self, "_opening_surface_thread", False) or self.closed:
            return
        if self.surface is not None:
            current_id = self.surface.current["id"] if self.surface.current else None
            was_request = bool(self.surface.current and self.surface.current.get("request"))
            self.surface.render_sidebar()
            if current_id in self.surface._entry_by_id:
                previous = self.surface.current
                self.surface.current = self.surface._entry_by_id[current_id]
                if (previous is None or previous.get("name") != self.surface.current.get("name") or
                        previous.get("person_data") != self.surface.current.get("person_data")):
                    self.surface._present_entry(self.surface.current)
                    self.surface.render_details()
                if was_request != bool(self.surface.current.get("request")):
                    self.surface.render_thread()
                    self.surface.render_details()
                    self.surface._composer()

    def _render_messages(self, *, to_end: bool = False) -> None:
        if self.surface is None:
            super()._render_messages(to_end=to_end)
        elif not getattr(self, "_opening_surface_thread", False):
            self.surface.render_thread(to_end=to_end)

    def _mark_opened_read(self, service, address: str, received: tuple[str, ...]) -> None:
        if self._fixture_mode:
            super()._mark_opened_read(service, address, received)
            return
        if (service.id, address) in self._pending_unread:
            # A previous queued Unread may not be reflected in this connection
            # yet. A later open still wins, scoped to what it displayed now.
            received = tuple(message.uid for message in service.store.thread(address)
                             if message.direction == "incoming")
        if received:
            self._persist_read_state(service, lambda writer: writer.mark_received_read(received))

    def _mark_thread_unread(self, record, service) -> None:
        if self._fixture_mode:
            super()._mark_thread_unread(record, service)
        else:
            # An explicit later unread action must follow any pending open/read.
            address = record.address
            key, intent = (service.id, address), object()
            self._pending_unread[key] = intent

            def finished():
                if self._pending_unread.get(key) is intent:
                    self._pending_unread.pop(key, None)

            self._persist_read_state(service, lambda writer: writer.mark_unread(address), finished)

    def _mark_thread_read(self, record, service) -> None:
        if self._fixture_mode:
            super()._mark_thread_read(record, service)
            return
        received = tuple(message.uid for message in service.store.thread(record.address)
                         if message.direction == "incoming" and message.state == "received")
        self._mark_opened_read(service, record.address, received)

    def _persist_read_state(self, service, write, on_finished=None) -> None:
        def persist():
            error = False
            try:
                writer = service.writer()
                try:
                    write(writer)
                finally:
                    writer.close()
            except Exception:
                error = True
            GLib.idle_add(finished, error)

        def finished(error):
            if on_finished is not None:
                on_finished()
            if not self.closed:
                if error:
                    self._notice("Could not save the conversation's read status. Try the action again.")
                else:
                    # Refresh badges, never restore the selection captured by this
                    # task: the person may already be reading another conversation.
                    self._reload_threads()
            return GLib.SOURCE_REMOVE

        self.content_worker.submit(persist)

    def _open_thread(self, record, *, reveal: bool, service=None) -> None:
        # The controller owns read state, drafts and provider notifications. Its
        # render hooks are deferred until the new surface entry is selected;
        # rebuilding the retired widgets also used to re-enter thread selection.
        self._opening_surface_thread = True
        try:
            super()._open_thread(record, reveal=reveal, service=service)
        finally:
            self._opening_surface_thread = False
        if self.surface is None:
            return
        service = service or self.service
        entry = next((item for item in self.surface._entries()
                      if item["address"] == record.address and
                      (self.surface.fixture is not None or item["service"] is service)), None)
        if entry is None:
            if self.surface.fixture is not None:
                return
            key = service.key(record.address)
            preference = self.surface.preferences.flags(key)
            flags = self._luma_flags(service, record)
            entry = {"id": key, "name": record.display_name, "address": record.address,
                     "record": record, "service": service, "person": None, "group": (),
                     "sms": service.native, "unread": 0, "pinned": preference["pinned"],
                     "muted": preference["muted"], "preview": "", "when": "", "hue": None,
                     "meta": flags, "request": bool(flags.get("request"))}
            self.surface.pending = entry
        if self.surface.current is None or self.surface.current["id"] != entry["id"]:
            self.surface._remember_draft()
            self.surface.selected_message = None
            self.surface.reply = None
            self.surface.details.show(open=False, subject=entry["id"])
        self.surface.current = entry
        self.surface._present_entry(entry)
        self.surface.render_sidebar()
        self.surface.render_thread()
        self.surface.render_details()
        self.surface._composer()
        self.surface.split.set_show_content(True)
        if self.surface._forward_text and self.surface.bar_entry is not None:
            self.surface.bar_entry.set_text(self.surface._forward_text)
            self.surface._drafts[entry["id"]] = self.surface._forward_text
            self.surface._forward_text = None

    def _resolve_sent_conversation(self, thread, *, service) -> None:
        surface = self.surface
        if surface is not None and surface.current is not None:
            old_key = surface.current["id"]
            new_key = service.key(thread.address)
            # Identity resolution is the same conversation. Preserve the next
            # draft, rich text, selected/replied message and current pane state.
            surface._remember_draft()
            for drafts in (surface._drafts, surface._rich_drafts):
                if old_key in drafts:
                    drafts[new_key] = drafts.pop(old_key)
            entry = next((item for item in surface._entries() if item["id"] == new_key), None)
            if entry is None:
                entry = dict(surface.current, id=new_key, address=thread.address,
                             record=thread, service=service, name=thread.display_name)
            if surface.pending is surface.current:
                surface.pending = entry
            surface.current = entry
        super()._resolve_sent_conversation(thread, service=service)
        if surface is not None:
            surface._present_entry(surface.current)
            surface.render_details()
            # Rebind on_change to the new key without clearing the next draft
            # or treating its reply/selection as a newly selected conversation.
            surface._composer()

    def _show_new_message(self, *_args) -> None:
        if self.surface is not None:
            self.surface.new_message()
        else:
            super()._show_new_message(*_args)

    def _recipient_activated(self, recipient_list, row) -> None:
        super()._recipient_activated(recipient_list, row)
        if self.surface is not None and self.surface.bar_entry is not None:
            self.surface.bar_entry.focus()

    def _show_nothing_selected(self) -> None:
        super()._show_nothing_selected()
        if self.surface is not None:
            self.surface.current = None
            self.surface.render_thread()
            self.surface.render_details()
            self.surface._composer()

    def _notice(self, message: str) -> None:
        if self.surface is not None:
            self.surface._notice(message)
        else:
            super()._notice(message)

    def _conversation_muted(self, key: str) -> bool:
        try:
            preferences = self.surface.preferences if self.surface is not None else ConversationPreferences()
            return preferences.flags(key)["muted"]
        except (OSError, ValueError):
            return False

    def _notify_arrival(self, address: str, body: str) -> None:
        canonical = self.native_service.store.canonical_address(address)
        if self._conversation_muted(canonical):
            return
        super()._notify_arrival(address, body)

    def _notify_service_arrival(self, service, address: str, name: str, body: str) -> None:
        if self._conversation_muted(service.key(address)):
            if self.is_active() and service is self.service and address == self.current_address:
                if hasattr(service.provider, "conversation_seen"):
                    service.provider.conversation_seen(address)
            return
        super()._notify_service_arrival(service, address, name, body)
