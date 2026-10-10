"""Side-effect-free app factory for the isolated backend modules."""

from __future__ import annotations

from collections.abc import Callable
from fastapi import FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from .asset_data.http import mount_asset_data_routes
from .chat_data.http import mount_chat_data_routes
from .chat_data.persistence import SqlAlchemyChatDataUnitOfWork
from .chat_data.ports import UnitOfWorkFactory as ChatDataUnitOfWorkFactory
from .download_links.compatibility import IdentityDownloadURLResolver
from .download_links.http import mount_download_links_routes
from .download_links.ports import DownloadURLResolver
from .canvas_data.http import mount_canvas_data_routes
from .canvas_data.persistence import SqlAlchemyCanvasDataUnitOfWork
from .canvas_data.ports import UnitOfWorkFactory as CanvasDataUnitOfWorkFactory
from .chapter_asset_replacement.http import mount_chapter_asset_replacement_routes
from .chapter_asset_replacement.persistence import SqlAlchemyChapterAssetReplacementUnitOfWork
from .chapter_asset_replacement.ports import UnitOfWorkFactory as ChapterAssetReplacementUnitOfWorkFactory
from .asset_data.persistence import SqlAlchemyAssetDataUnitOfWork
from .asset_data.ports import AssetDataUnitOfWorkFactory
from .authentication.configuration import AuthenticationRuntime
from .authentication.errors import AuthenticationError
from .authentication.http import mount_authentication_routes
from .authentication.persistence import SqlAlchemyAuthenticationUnitOfWork
from .authentication.ports import UnitOfWorkFactory as AuthenticationUnitOfWorkFactory
from .personal_production.notes.http import mount_personal_production_notes_routes
from .personal_production.notes.persistence import SqlAlchemyNotesUnitOfWork
from .personal_production.rough_cut.http import ActorResolver, mount_rough_cut_routes
from .personal_production.rough_cut.persistence import (
    SeriesAccessCheck,
    SqlAlchemyRoughCutUnitOfWork,
)
from .series_data.http import mount_series_data_routes
from .series_data.persistence import SqlAlchemySeriesDataUnitOfWork
from .series_data.ports import UnitOfWorkFactory as SeriesDataUnitOfWorkFactory
from .teams import JoinQuota, build_teams_routers, create_team_uow_factory
from .teams.management.persistence import create_management_uow_factory
from .teams.series.persistence import create_series_uow_factory
from .task_observation.authentication import (
    resolve_account_actor as resolve_task_account_actor,
    resolve_active_actor as resolve_task_active_actor,
)
from .task_observation.cancellation import SqlTaskCancellationWriter
from .task_observation.http import build_task_observation_router
from .task_observation.persistence import task_observation_unit_of_work_factory
from .task_observation.ports import CancellationConnectionFactory, CancellationSignalRegistry

SessionFactory = Callable[[], Session]


def create_app(
    *,
    session_factory: SessionFactory | None = None,
    resolve_actor: ActorResolver | None = None,
    series_access_policy: SeriesAccessCheck | None = None,
    authentication_runtime: AuthenticationRuntime | None = None,
    download_url_resolver: DownloadURLResolver | None = None,
    team_join_quota: JoinQuota | None = None,
    resolve_task_account: ActorResolver | None = None,
    resolve_task_active: ActorResolver | None = None,
    task_cancellation_connection_factory: CancellationConnectionFactory | None = None,
    task_cancellation_signals: CancellationSignalRegistry | None = None,
) -> FastAPI:
    """Compose 11 authentication and 80 business methods without startup I/O."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    authentication_uow_factory: AuthenticationUnitOfWorkFactory | None = None
    if authentication_runtime is not None and callable(authentication_runtime.session_factory):
        def make_authentication_uow():
            session = authentication_runtime.session_factory()
            return SqlAlchemyAuthenticationUnitOfWork(session)

        authentication_uow_factory = make_authentication_uow
    authentication_service = mount_authentication_routes(
        app,
        authentication_runtime,
        unit_of_work_factory=authentication_uow_factory,
    )

    effective_session_factory = session_factory
    if effective_session_factory is None and authentication_runtime is not None:
        effective_session_factory = authentication_runtime.session_factory

    effective_resolver = resolve_actor
    if effective_resolver is None and authentication_runtime is not None:
        async def resolve_configured_actor(request: Request):
            try:
                return await run_in_threadpool(
                    authentication_service.resolve_business_actor,
                    request.headers.get("authorization", ""),
                )
            except AuthenticationError as exc:
                raise HTTPException(
                    status_code=exc.status_code,
                    detail=exc.detail,
                    headers=exc.headers,
                ) from exc

        effective_resolver = resolve_configured_actor

    if (
        effective_session_factory is None
        or effective_resolver is None
        or series_access_policy is None
    ):
        rough_uow_factory = None
        notes_uow_factory = None
    else:
        def make_rough_uow() -> SqlAlchemyRoughCutUnitOfWork:
            return SqlAlchemyRoughCutUnitOfWork(
                session=effective_session_factory(),
                series_access_check=series_access_policy,
            )

        def make_notes_uow() -> SqlAlchemyNotesUnitOfWork:
            return SqlAlchemyNotesUnitOfWork(
                session=effective_session_factory(),
                series_access_check=series_access_policy,
            )

        rough_uow_factory = make_rough_uow
        notes_uow_factory = make_notes_uow

    mount_rough_cut_routes(
        app,
        uow_factory=rough_uow_factory,
        resolve_actor=effective_resolver,
    )
    mount_personal_production_notes_routes(
        app,
        uow_factory=notes_uow_factory,
        resolve_actor=effective_resolver,
    )

    series_data_uow_factory: SeriesDataUnitOfWorkFactory | None = None
    if effective_session_factory is not None and effective_resolver is not None:
        def make_series_data_uow() -> SqlAlchemySeriesDataUnitOfWork:
            return SqlAlchemySeriesDataUnitOfWork(effective_session_factory())

        series_data_uow_factory = make_series_data_uow
    mount_series_data_routes(
        app,
        uow_factory=series_data_uow_factory,
        resolve_actor=effective_resolver,
    )

    asset_data_uow_factory: AssetDataUnitOfWorkFactory | None = None
    if effective_session_factory is not None and effective_resolver is not None:
        def make_asset_data_uow() -> SqlAlchemyAssetDataUnitOfWork:
            return SqlAlchemyAssetDataUnitOfWork(effective_session_factory())

        asset_data_uow_factory = make_asset_data_uow
    mount_asset_data_routes(
        app,
        uow_factory=asset_data_uow_factory,
        resolve_actor=effective_resolver,
    )

    chat_data_uow_factory: ChatDataUnitOfWorkFactory | None = None
    if effective_session_factory is not None and effective_resolver is not None:
        def make_chat_data_uow() -> SqlAlchemyChatDataUnitOfWork:
            return SqlAlchemyChatDataUnitOfWork(effective_session_factory())

        chat_data_uow_factory = make_chat_data_uow
    mount_chat_data_routes(
        app,
        uow_factory=chat_data_uow_factory,
        resolve_actor=effective_resolver,
    )

    canvas_data_uow_factory: CanvasDataUnitOfWorkFactory | None = None
    if effective_session_factory is not None and effective_resolver is not None:
        def make_canvas_data_uow() -> SqlAlchemyCanvasDataUnitOfWork:
            return SqlAlchemyCanvasDataUnitOfWork(effective_session_factory())

        canvas_data_uow_factory = make_canvas_data_uow
    mount_canvas_data_routes(
        app,
        uow_factory=canvas_data_uow_factory,
        resolve_actor=effective_resolver,
    )

    mount_download_links_routes(
        app,
        resolve_actor=effective_resolver,
        url_resolver=(
            download_url_resolver
            if download_url_resolver is not None
            else IdentityDownloadURLResolver()
        ),
    )
    replacement_uow_factory: ChapterAssetReplacementUnitOfWorkFactory | None = None
    if (
        effective_session_factory is not None
        and effective_resolver is not None
        and series_access_policy is not None
    ):
        def make_chapter_asset_replacement_uow() -> SqlAlchemyChapterAssetReplacementUnitOfWork:
            return SqlAlchemyChapterAssetReplacementUnitOfWork(
                session=effective_session_factory(),
                series_access_check=series_access_policy,
            )

        replacement_uow_factory = make_chapter_asset_replacement_uow

    mount_chapter_asset_replacement_routes(
        app,
        uow_factory=replacement_uow_factory,
        resolve_actor=effective_resolver,
    )

    management_team_uow_factory = None
    series_team_uow_factory = None
    reporting_team_uow_factory = None
    if effective_session_factory is not None and effective_resolver is not None:
        team_uow_factory = create_team_uow_factory(effective_session_factory)
        management_team_uow_factory = create_management_uow_factory(team_uow_factory)
        series_team_uow_factory = create_series_uow_factory(effective_session_factory)
        reporting_team_uow_factory = team_uow_factory

    for router in build_teams_routers(
        management_uow_factory=management_team_uow_factory,
        series_uow_factory=series_team_uow_factory,
        reporting_uow_factory=reporting_team_uow_factory,
        resolve_actor=effective_resolver,
        join_quota=team_join_quota,
    ):
        app.include_router(router)

    task_uow_factory = (
        task_observation_unit_of_work_factory(effective_session_factory)
        if effective_session_factory is not None
        else None
    )
    account_actor_resolver = resolve_task_account
    if account_actor_resolver is None:
        async def resolve_configured_task_account(request: Request):
            return await run_in_threadpool(
                resolve_task_account_actor,
                authentication_service,
                request.headers.get("authorization", ""),
            )

        account_actor_resolver = resolve_configured_task_account

    active_actor_resolver = resolve_task_active
    if active_actor_resolver is None:
        active_actor_resolver = effective_resolver
    if active_actor_resolver is None:
        async def resolve_configured_task_active(request: Request):
            return await run_in_threadpool(
                resolve_task_active_actor,
                authentication_service,
                request.headers.get("authorization", ""),
            )

        active_actor_resolver = resolve_configured_task_active

    task_cancellation_writer = (
        SqlTaskCancellationWriter(task_cancellation_connection_factory)
        if task_cancellation_connection_factory is not None
        else None
    )
    app.include_router(
        build_task_observation_router(
            uow_factory=task_uow_factory,
            resolve_account_actor=account_actor_resolver,
            resolve_active_actor=active_actor_resolver,
            cancellation_signals=task_cancellation_signals,
            cancellation_writer=task_cancellation_writer,
        )
    )
    return app
