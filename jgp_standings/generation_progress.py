"""Report generation stages through `ProgressReporter`.

The CLI prints stages; the web adapter records them as operation events.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol


class ProgressReporter(Protocol):
    """Receives lifecycle events for long-running pipeline operations."""

    def begin_phase(self, description: str, total: int, unit: str) -> None: ...

    def start(self, operation: str) -> None: ...

    def complete(self) -> None: ...


@contextmanager
def operation(
    progress: ProgressReporter | None,
    description: str,
) -> Iterator[None]:
    """Report an operation as complete only when its body succeeds."""
    if progress is not None:
        progress.start(description)
    yield
    if progress is not None:
        progress.complete()
