"""GTK-free session and split state for Luma Terminal."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class Pane:
    id: int
    cwd: str


@dataclass(frozen=True)
class Split:
    direction: str  # "row": split right; "col": split down
    first: "Node"
    second: "Node"


Node = Pane | Split


@dataclass
class Session:
    id: int
    root: Node
    home: str
    name: str | None = None

    @property
    def panes(self) -> tuple[Pane, ...]:
        return tuple(walk(self.root))

    @property
    def display_name(self) -> str:
        if self.name:
            return self.name
        folder = Path(self.panes[0].cwd)
        return "Home" if folder == Path(self.home) else folder.name or "/"


def walk(node: Node) -> Iterator[Pane]:
    if isinstance(node, Pane):
        yield node
    else:
        yield from walk(node.first)
        yield from walk(node.second)


def _replace(node: Node, pane_id: int, replacement: Node) -> Node:
    if isinstance(node, Pane):
        return replacement if node.id == pane_id else node
    return Split(node.direction, _replace(node.first, pane_id, replacement),
                 _replace(node.second, pane_id, replacement))


def _remove(node: Node, pane_id: int) -> Node | None:
    if isinstance(node, Pane):
        return None if node.id == pane_id else node
    first, second = _remove(node.first, pane_id), _remove(node.second, pane_id)
    if first is None:
        return second
    if second is None:
        return first
    return Split(node.direction, first, second)


def short_path(cwd: str, home: str | None = None) -> str:
    home = home or str(Path.home())
    if cwd == home:
        return "~"
    if cwd.startswith(home + "/"):
        return "~" + cwd[len(home):]
    return cwd


def history_suggestion(value: str, history: list[str]) -> str:
    """The suffix of the newest matching command, without repeating the input."""
    if not value:
        return ""
    return next((item[len(value):] for item in reversed(history)
                 if item.startswith(value) and item != value), "")


@dataclass(frozen=True)
class PhoneKey:
    """One key of the phone's keys row (v71 `tmBarPhone`).

    A control key (`field` False) goes to the shell as `send`; a symbol key
    (`field` True) is typed into the command field.
    """

    name: str  # v71's own aria-label for the key
    label: str
    send: str
    icon: str | None = None
    field: bool = False


PHONE_KEYS: tuple[PhoneKey, ...] = (
    PhoneKey("Escape", "esc", "\x1b"),
    PhoneKey("Tab", "tab", "\t"),
    PhoneKey("^C", "\u2303C", "\x03"),
    PhoneKey("ArrowUp", "", "\x1b[A", icon="arrow-up"),
    PhoneKey("ArrowDown", "", "\x1b[B", icon="arrow-down"),
    *(PhoneKey(symbol, symbol, symbol, field=True) for symbol in "|~/-*>&"),
)


def git_branch(cwd: str) -> str | None:
    """The branch of the repository holding `cwd`, read from `.git/HEAD` (nothing is written)."""
    folder = Path(cwd)
    for candidate in (folder, *folder.parents):
        try:
            head = (candidate / ".git" / "HEAD").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        prefix = "ref: refs/heads/"
        return head[len(prefix):] if head.startswith(prefix) else head[:7] or None
    return None


def fixture_branch(cwd: str) -> str | None:
    """v71's sample data: the Luma project folder is on `main`."""
    return "main" if "Projects/luma" in cwd else None


def session_subtitle(session: Session, home: str) -> str:
    """The line under a session's name: where it is, and how many panes it has when it has more than one."""
    panes = session.panes
    where = short_path(panes[0].cwd, home)
    return f"{where} \u00b7 {len(panes)} panes" if len(panes) > 1 else where


class TerminalModel:
    def __init__(self, cwd: str | None = None, *, home: str | None = None) -> None:
        self.home = home or str(Path.home())
        self.default_cwd = cwd or self.home
        self._next_session = 1
        self._next_pane = 1
        self.sessions: list[Session] = []
        self.current_id = 0
        self.active_pane_id = 0
        self.new_session(cwd)

    @property
    def current(self) -> Session:
        return next(session for session in self.sessions if session.id == self.current_id)

    @property
    def active(self) -> Pane:
        return next(pane for pane in self.current.panes if pane.id == self.active_pane_id)

    def new_session(self, cwd: str | None = None) -> Session:
        pane = Pane(self._next_pane, cwd or self.default_cwd)
        self._next_pane += 1
        session = Session(self._next_session, pane, self.home)
        self._next_session += 1
        self.sessions.append(session)
        self.current_id = session.id
        self.active_pane_id = pane.id
        return session

    def activate_session(self, session_id: int) -> None:
        session = next((item for item in self.sessions if item.id == session_id), None)
        if session is None:
            raise KeyError(session_id)
        self.current_id = session_id
        self.active_pane_id = session.panes[0].id

    def activate_pane(self, pane_id: int) -> None:
        if not any(pane.id == pane_id for pane in self.current.panes):
            raise KeyError(pane_id)
        self.active_pane_id = pane_id

    def rename_session(self, session_id: int, name: str) -> None:
        session = next((item for item in self.sessions if item.id == session_id), None)
        if session is None:
            raise KeyError(session_id)
        session.name = name.strip() or None

    def update_cwd(self, pane_id: int, cwd: str) -> None:
        for session in self.sessions:
            if any(pane.id == pane_id for pane in session.panes):
                session.root = _replace(session.root, pane_id, Pane(pane_id, cwd))
                return
        raise KeyError(pane_id)

    def split_active(self, direction: str) -> Pane:
        if direction not in ("row", "col"):
            raise ValueError(direction)
        active = self.active
        other = Pane(self._next_pane, active.cwd)
        self._next_pane += 1
        self.current.root = _replace(self.current.root, active.id, Split(direction, active, other))
        self.active_pane_id = other.id
        return other

    def close_pane(self, pane_id: int) -> None:
        if not any(pane.id == pane_id for pane in self.current.panes):
            raise KeyError(pane_id)
        root = _remove(self.current.root, pane_id)
        if root is None:
            self.close_session(self.current_id)
            return
        self.current.root = root
        self.active_pane_id = next(walk(root)).id

    def close_session(self, session_id: int) -> None:
        if not any(session.id == session_id for session in self.sessions):
            raise KeyError(session_id)
        index = next(index for index, session in enumerate(self.sessions) if session.id == session_id)
        self.sessions = [session for session in self.sessions if session.id != session_id]
        if not self.sessions:
            self.new_session()
        elif self.current_id == session_id:
            self.activate_session(self.sessions[min(index, len(self.sessions) - 1)].id)
