"""Operation-driven progress and cooperative cancellation, never timers."""
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Event, Lock
from .errors import InstallerError


class Cancelled(InstallerError):
    pass


@dataclass(frozen=True)
class Progress:
    label: str
    fraction: float
    cancellable: bool
    # True when the fraction is measured work rather than a stage marker, so a
    # surface can show a real bar instead of implying precision it does not have.
    measured: bool = False


class Aggregate:
    """One monotonic fraction for a job a backend splits into many operations.

    Installing one application can mean a dozen backend operations: a runtime,
    its extensions, the application itself. Each of those reports its own 0→100,
    and forwarding them unchanged shows the person their install restarting over
    and over. This collapses the whole set into a single fraction that only ever
    moves forward, plus the running byte totals for the whole job.

    Operation identities are opaque keys; nothing here displays them.
    """

    def __init__(self, start: float = 0.0, end: float = 1.0) -> None:
        self.start = float(start)
        self.end = float(end)
        self.expected = 1
        self.declared_bytes = 0
        self.highest = self.start
        self._within: dict[object, float] = {}
        self._transferred: dict[object, int] = {}
        self._size: dict[object, int] = {}

    def expect(self, count: int, total_bytes: int = 0) -> None:
        """Record how much work the backend resolved, once it knows.

        The count never shrinks: an operation already seen is work already
        counted, and forgetting it would make the fraction jump.
        """
        self.expected = max(self.expected, int(count or 0), len(self._within))
        self.declared_bytes = max(self.declared_bytes, int(total_bytes or 0))

    def report(self, key: object, within: float, transferred: int = 0, size: int = 0) -> float:
        """Fold one operation's own progress in and return the job's fraction."""
        if key not in self._within:
            self._within[key] = 0.0
            self.expected = max(self.expected, len(self._within))
        bounded = min(1.0, max(0.0, float(within)))
        # An operation that already reached a point never un-reaches it, even if
        # the backend restarts its counter mid-operation.
        self._within[key] = max(self._within[key], bounded)
        self._transferred[key] = max(self._transferred.get(key, 0), int(transferred or 0))
        if size:
            self._size[key] = max(self._size.get(key, 0), int(size))
        done = sum(self._within.values()) / max(1, self.expected)
        value = self.start + (self.end - self.start) * min(1.0, done)
        self.highest = max(self.highest, value)
        return self.highest

    def finish(self) -> float:
        for key in self._within:
            self._within[key] = 1.0
        self.highest = max(self.highest, self.end)
        return self.highest

    @property
    def transferred_bytes(self) -> int:
        return sum(self._transferred.values())

    @property
    def total_bytes(self) -> int:
        # Never announce a total smaller than what has already arrived, and
        # never let an unstarted operation shrink the number.
        return max(self.declared_bytes, sum(self._size.values()), self.transferred_bytes)


class Transaction:
    def __init__(self, callback=lambda _event: None, title=""):
        self.callback = callback
        self.cancelled = Event()
        self.lock = Lock()
        self.cancellable = True
        self.cancel_callback = None
        # One install is one job. The title, when a caller sets it, is the only
        # name the person sees; per-dependency labels stay inside the backend.
        self.title = title
        self.highest = 0.0

    def cancel(self):
        with self.lock:
            if not self.cancellable:
                return False
            self.cancelled.set()
            if self.cancel_callback:
                self.cancel_callback()
            return True

    def _advance(self, fraction):
        # Progress within one transaction never runs backwards.
        fraction = max(float(fraction), self.highest)
        self.highest = fraction
        return fraction

    def phase(self, label, fraction, cancellable=False):
        with self.lock:
            if self.cancelled.is_set():
                raise Cancelled("Installation cancelled.")
            self.cancellable = cancellable
            fraction = self._advance(fraction)
        self.callback(Progress(self.title or label, fraction, cancellable))

    def report(self, label, fraction):
        """Progress from inside a backend callback: clamped, and never raises.

        A backend signal handler is not a place to unwind a Python exception,
        so cancellation is observed by the backend's own cancellable instead.
        """
        with self.lock:
            fraction = self._advance(fraction)
            cancellable = self.cancellable
        self.callback(Progress(self.title or label, fraction, cancellable, True))


current = ContextVar("luma_install_transaction", default=None)


def phase(label, fraction, cancellable=False):
    transaction = current.get()
    if transaction is not None:
        transaction.phase(label, fraction, cancellable)
