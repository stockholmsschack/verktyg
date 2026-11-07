"""Explicit dispatch for historically reproducible tournament policies."""
from types import ModuleType

from . import trojanska_7132


def policy_for(tournament_id: int) -> ModuleType | None:
    return trojanska_7132 if int(tournament_id) == 7132 else None
