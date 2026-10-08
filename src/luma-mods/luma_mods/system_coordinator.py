"""Journaled coordinator for the fixed rpm-ostree system backend."""

from __future__ import annotations

from .errors import TransactionError
from .privileged import SystemCompositionRequest
from .system_backend import RpmOstreeBackend
from .system_state import SystemStateStore


class SystemTransactionCoordinator:
    def __init__(
        self,
        backend: RpmOstreeBackend | None = None,
        state: SystemStateStore | None = None,
        *,
        boot_attempt_limit: int = 2,
    ) -> None:
        self.backend = backend or RpmOstreeBackend()
        self.state = state or SystemStateStore()
        self.boot_attempt_limit = boot_attempt_limit

    def recover_incomplete_staging(self) -> bool:
        pending = self.state.read()["pending"]
        if pending is None or pending["phase"] not in {"staging", "staged"}:
            return False
        candidate = pending["candidate_checksum"]
        self.backend.cancel_staged(candidate)
        self.state.cancel("recovered")
        return True

    def stage(self, request: SystemCompositionRequest) -> str:
        self.recover_incomplete_staging()
        known_good = self.backend.booted_checksum()
        transaction_id = self.state.prepare(
            request,
            known_good,
            boot_attempt_limit=self.boot_attempt_limit,
        )
        try:
            candidate = self.backend.stage(request)
            self.state.mark_staged(transaction_id, candidate)
            return candidate
        except Exception:
            # Preserve the original exception, but make the backend/state
            # recovery best effort. A later service start repeats it safely.
            try:
                self.backend.cancel_staged()
                self.state.cancel("recovered")
            except Exception:
                pass
            raise

    def activate(self, candidate_checksum: str) -> None:
        self.state.request_activation(candidate_checksum)
        self.backend.activate(candidate_checksum)

    def observe_boot(self) -> str:
        booted = self.backend.booted_checksum()
        result = self.state.observe_boot(booted)
        if result == "rollback":
            pending = self.state.read()["pending"]
            if pending is None or pending["candidate_checksum"] != booted:
                raise TransactionError("rollback candidate disappeared from the journal")
            self.backend.rollback(booted)
        return result

    def promote_booted_candidate(self) -> bool:
        booted = self.backend.booted_checksum()
        pending = self.state.read()["pending"]
        if pending is None:
            return False
        if pending["phase"] != "candidate-booted":
            raise TransactionError("candidate deployment has not passed boot observation")
        self.state.promote(booted)
        return True

    def rollback(self, candidate_checksum: str) -> None:
        pending = self.state.read()["pending"]
        if pending is None or pending["candidate_checksum"] != candidate_checksum:
            raise TransactionError("rollback candidate is not journaled")
        self.backend.rollback(candidate_checksum)
