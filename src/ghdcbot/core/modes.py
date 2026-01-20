from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RunMode(str, Enum):
    DRY_RUN = "dry-run"
    OBSERVER = "observer"
    ACTIVE = "active"


@dataclass(frozen=True)
class MutationPolicy:
    mode: RunMode
    github_write_allowed: bool
    discord_write_allowed: bool

    @property
    def allow_github_mutations(self) -> bool:
        if self.mode != RunMode.ACTIVE:
            return False
        return self.github_write_allowed

    @property
    def allow_discord_mutations(self) -> bool:
        if self.mode != RunMode.ACTIVE:
            return False
        return self.discord_write_allowed
