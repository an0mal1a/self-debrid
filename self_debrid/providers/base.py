"""Provider protocol for Self-Debrid v2 discovery sources."""

from __future__ import annotations

from typing import Protocol, Sequence

from self_debrid.domain import MediaRequest, StreamCandidate


class ProviderError(Exception):
    """A credential-safe failure. Messages must never include remote response text."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Provider(Protocol):
    @property
    def name(self) -> str:
        """Stable provider identifier used in diagnostics and candidate metadata."""
        ...

    async def search(self, request: MediaRequest) -> Sequence[StreamCandidate]:
        """Return normalized candidates for one media request."""
        ...
