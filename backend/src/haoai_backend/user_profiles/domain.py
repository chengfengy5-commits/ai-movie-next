"""Framework-independent records and compatibility rules for user profiles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class UserProfileActor:
    user_id: str
    username: str
    email: str
    bio: str | None


@dataclass(frozen=True, slots=True)
class UserProfileRecord:
    id: str
    username: str
    email: str
    bio: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class TeamRecord:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class TeamMembership:
    role: str | None


@dataclass(frozen=True, slots=True)
class SeriesRecord:
    id: str
    name: str
    team_id: str | None


@dataclass(frozen=True, slots=True)
class SeriesAssetCounts:
    characters: int
    scenes: int
    props: int
    storyboard: int

    def as_dict(self) -> dict[str, int]:
        return {
            "characters": self.characters,
            "scenes": self.scenes,
            "props": self.props,
            "storyboard": self.storyboard,
        }


def mask_email(email: str | None) -> str:
    """Apply the legacy mask while preserving the original casing and domain."""
    if not email or "@" not in email:
        return email or ""

    local, domain = email.split("@", 1)
    if len(local) <= 1:
        masked_local = local + "***"
    else:
        masked_local = local[0] + "***" + local[-1]
    return f"{masked_local}@{domain}"
