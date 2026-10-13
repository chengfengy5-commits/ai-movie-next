"""Framework-independent records and response projections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Generic, TypeVar


@dataclass(frozen=True, slots=True)
class AdminChapterCharacterActor:
    user_id: str
    is_superuser: bool


@dataclass(frozen=True, slots=True)
class ChapterRecord:
    id: str
    series_id: str
    title: str
    order: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CharacterRecord:
    id: str
    series_id: str
    name: str
    gender: str | None
    age: str | None
    role: str | None
    appearance: str | None
    description: str | None
    image_url: str | None
    created_at: datetime


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    data: tuple[T, ...]
    total: int


def chapter_payload(chapter: ChapterRecord) -> dict[str, object]:
    return {
        "id": chapter.id,
        "series_id": chapter.series_id,
        "title": chapter.title,
        "order": chapter.order,
        "created_at": chapter.created_at,
    }


def character_list_payload(character: CharacterRecord) -> dict[str, object]:
    return {
        "id": character.id,
        "series_id": character.series_id,
        "name": character.name,
        "gender": character.gender,
        "age": character.age,
        "role": character.role,
        "created_at": character.created_at,
    }


def character_detail_payload(character: CharacterRecord) -> dict[str, object]:
    return {
        **character_list_payload(character),
        "appearance": character.appearance,
        "description": character.description,
        "image_url": character.image_url,
    }
