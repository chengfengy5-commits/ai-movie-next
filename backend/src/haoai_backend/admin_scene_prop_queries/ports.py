"""Persistence contracts for administrator scene and prop reads."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

from .domain import PropRecord, SceneRecord


class AdminScenePropQueriesUnitOfWork(Protocol):
    def list_scenes(
        self,
        *,
        sort_field: str,
        sort_order: str,
        skip: int,
        limit: int,
    ) -> tuple[Sequence[SceneRecord], int]: ...

    def get_scene(self, scene_id: str) -> SceneRecord | None: ...

    def list_props(
        self,
        *,
        sort_field: str,
        sort_order: str,
        skip: int,
        limit: int,
    ) -> tuple[Sequence[PropRecord], int]: ...

    def get_prop(self, prop_id: str) -> PropRecord | None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


UnitOfWorkFactory = Callable[[], AdminScenePropQueriesUnitOfWork]
