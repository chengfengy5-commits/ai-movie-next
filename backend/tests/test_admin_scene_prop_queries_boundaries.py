from __future__ import annotations

from fastapi.routing import APIRoute

from haoai_backend.admin_scene_prop_queries.domain import AdminScenePropQueriesActor
from haoai_backend.admin_scene_prop_queries.http import (
    build_admin_scene_prop_queries_router,
)
from haoai_backend.admin_scene_prop_queries.schemas import (
    PropPayload,
    ScenePayload,
)
from haoai_backend.admin_scene_prop_queries.tables import (
    Base,
    PropRow,
    SceneRow,
    SeriesRow,
)


def test_private_orm_keeps_legacy_tables_columns_and_relationship_attributes() -> None:
    assert set(Base.metadata.tables) == {"series", "scenes", "props"}
    assert set(SeriesRow.__table__.columns.keys()) == {"id"}
    assert set(SceneRow.__table__.columns.keys()) == {
        "id",
        "series_id",
        "title",
        "description",
        "image_url",
        "created_at",
        "updated_at",
        "aliases",
        "canonical_key",
    }
    assert set(PropRow.__table__.columns.keys()) == {
        "id",
        "series_id",
        "name",
        "description",
        "image_url",
        "created_at",
        "updated_at",
        "aliases",
        "canonical_key",
    }
    assert SceneRow.__tablename__ == "scenes"
    assert PropRow.__tablename__ == "props"
    assert SceneRow.series.property.mapper.class_ is SeriesRow
    assert PropRow.series.property.mapper.class_ is SeriesRow
    assert next(iter(SceneRow.series_id.foreign_keys)).target_fullname == "series.id"
    assert next(iter(PropRow.series_id.foreign_keys)).target_fullname == "series.id"
    assert hasattr(SceneRow, "metadata")
    assert hasattr(SceneRow, "registry")
    assert hasattr(PropRow, "metadata")
    assert hasattr(PropRow, "registry")


def test_http_surface_is_only_the_four_legacy_get_routes_without_response_models() -> None:
    router = build_admin_scene_prop_queries_router(
        authentication_service=None,
        session_factory=None,
    )
    routes = [route for route in router.routes if isinstance(route, APIRoute)]

    assert {
        (route.path, tuple(sorted(route.methods or ())))
        for route in routes
    } == {
        ("/api/admin/scenes", ("GET",)),
        ("/api/admin/scenes/{scene_id}", ("GET",)),
        ("/api/admin/props", ("GET",)),
        ("/api/admin/props/{prop_id}", ("GET",)),
    }
    assert all(route.response_model is None for route in routes)


def test_public_dtos_omit_columns_not_returned_by_legacy_endpoints() -> None:
    assert set(ScenePayload.__annotations__) == {
        "id",
        "series_id",
        "title",
        "description",
        "image_url",
        "created_at",
    }
    assert set(PropPayload.__annotations__) == {
        "id",
        "series_id",
        "name",
        "description",
        "image_url",
        "created_at",
    }
    assert "aliases" not in ScenePayload.__annotations__
    assert "canonical_key" not in ScenePayload.__annotations__
    assert "updated_at" not in PropPayload.__annotations__


def test_actor_is_module_local_and_immutable() -> None:
    actor = AdminScenePropQueriesActor(user_id="admin", is_superuser=True)
    assert actor.user_id == "admin"
    assert actor.is_superuser is True
    try:
        actor.is_superuser = False
    except (AttributeError, TypeError):
        pass
    else:
        raise AssertionError("administrator actor must be immutable")
