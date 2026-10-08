"""One command model for menus, context menus, shortcuts, and semantics."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable


Predicate = Callable[[], bool]
Handler = Callable[[], None]


@dataclass
class Command:
    id: str
    label: str
    execute: Handler
    icon: str | None = None
    # A second line under the label, saying what the choice means: "Name /
    # Alphabetical". A submenu of choices reads as a list of answers rather
    # than a list of words with it.
    description: str = ""
    shortcut: tuple[str, ...] = ()
    visible: Predicate = lambda: True
    enabled: Predicate = lambda: True
    checked: Predicate | None = None
    destructive: bool = False
    children: tuple["Command", ...] = ()
    # How many things the choice shows (a filter's "Favourites  12"), drawn as
    # a LumaUI count badge at the row's end. None draws nothing.
    count: int | None = None

    def __post_init__(self) -> None:
        # Older applications pass the shortcut fifth, where the description now sits:
        # Command(id, label, run, icon, ("Ctrl", "Q")). A tuple or list there is a shortcut.
        if isinstance(self.description, (tuple, list)):
            if not self.shortcut:
                self.shortcut = tuple(self.description)
            self.description = ""
        if isinstance(self.shortcut, str):
            self.shortcut = (self.shortcut,)
        elif isinstance(self.shortcut, list):
            self.shortcut = tuple(self.shortcut)


@dataclass(frozen=True)
class CommandGroup:
    label: str | None
    commands: tuple[Command, ...] = field(default_factory=tuple)
    quick_actions: bool = False
    #: False: the group's commands keep their shortcuts and stay invocable, but the app's menu does not
    #: list them (v71 Charlie's menu is its accounts alone).
    in_menu: bool = True


class CommandRegistry:
    """Authoritative command collection used by every invocation surface."""

    def __init__(self, groups: Iterable[CommandGroup] = ()) -> None:
        self.groups = tuple(groups)
        self._commands: dict[str, Command] = {}
        for group in self.groups:
            for command in group.commands:
                self._index(command)

    def _index(self, command: Command) -> None:
        if command.id in self._commands:
            raise ValueError(f"duplicate command id: {command.id}")
        self._commands[command.id] = command
        for child in command.children:
            self._index(child)

    def get(self, command_id: str) -> Command:
        return self._commands[command_id]

    def invoke(self, command_id: str) -> bool:
        command = self.get(command_id)
        if not command.visible() or not command.enabled():
            return False
        command.execute()
        return True

    def visible_groups(self, *, menu: bool = False) -> tuple[CommandGroup, ...]:
        """The groups with something to show; `menu=True` leaves out those kept off the menu."""
        result = []
        for group in self.groups:
            if menu and not group.in_menu:
                continue
            commands = tuple(command for command in group.commands if command.visible())
            if commands:
                result.append(CommandGroup(group.label, commands, group.quick_actions, group.in_menu))
        return tuple(result)
