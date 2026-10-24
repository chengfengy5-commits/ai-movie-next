"""FastAPI adapter for the database-only storyboard save routes."""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Awaitable, Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from anyio import CancelScope
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.types import Receive, Scope, Send

from haoai_backend.series_access.errors import SeriesAccessDenied, SeriesNotFound
from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor

from . import application
from .domain import chapter_content_matches_expected
from .errors import (
    StoryboardSaveChapterNotFound,
    StoryboardSaveForeignAssetReference,
    StoryboardSaveSourceConflict,
    StoryboardSaveUnavailable,
)
from .non_stream import filter_and_save_characters as run_filter_and_save_characters
from .persistence import SqlAlchemyStoryboardSaveStore
from .presentation import encode_sse
from .schemas import ExpectedChapterContentBody, ExpectedSeriesIdBody

SessionFactory = Callable[[], Session]
ActorResolver = Callable[[Request], TrustedActor | Awaitable[TrustedActor]]
SaveWorkflow = Callable[..., Iterator[dict[str, Any]]]


class _ResourceOwnedStreamingResponse(StreamingResponse):
    """Keep route resources owned until the ASGI response call has fully exited."""

    def __init__(
        self,
        *args: Any,
        close_resources: Callable[[], Awaitable[None]],
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._close_resources = close_resources

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._close_resources()


_ROUTES = (
    (
        "/filter-and-save-characters-stream",
        "角色列表",
        "角色出场数据",
        "开始过滤角色并保存到数据库...",
        "过滤角色失败",
        "merged_characters",
        application.save_characters_stream,
    ),
    (
        "/filter-and-save-scenes-stream",
        "场景列表",
        "场景出场数据",
        "开始过滤场景并保存到数据库...",
        "过滤场景失败",
        "scenes",
        application.save_scenes_stream,
    ),
    (
        "/filter-and-save-props-stream",
        "道具列表",
        "道具出场数据",
        "开始过滤道具并保存到数据库...",
        "过滤道具失败",
        "props",
        application.save_props_stream,
    ),
)


def build_storyboard_save_router(
    *,
    session_factory: SessionFactory | None,
    resolve_actor: ActorResolver | None,
) -> APIRouter:
    """Build the four legacy save routes around public identity and SQL ports."""
    router = APIRouter(prefix="/api/storyboard", tags=["storyboard-save"])

    async def trusted_actor(request: Request) -> TrustedActor:
        if session_factory is None or resolve_actor is None:
            error = StoryboardSaveUnavailable()
            raise HTTPException(status_code=error.status_code, detail=error.detail)
        try:
            is_async_resolver = inspect.iscoroutinefunction(resolve_actor) or inspect.iscoroutinefunction(
                getattr(resolve_actor, "__call__", None)
            )
            if is_async_resolver:
                candidate = resolve_actor(request)
            else:
                candidate = await asyncio.to_thread(resolve_actor, request)
            actor = await candidate if inspect.isawaitable(candidate) else candidate
        except BusinessError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.detail,
                headers=getattr(exc, "headers", None),
            ) from exc
        if not isinstance(actor, TrustedActor) or not actor.user_id:
            error = StoryboardSaveUnavailable("可信身份端口未正确接线")
            raise HTTPException(status_code=error.status_code, detail=error.detail)
        return actor

    for route_path, list_detail, appearances_detail, start_message, failure_detail, candidates_field, workflow in _ROUTES:
        _register_save_route(
            router,
            route_path=route_path,
            list_detail=list_detail,
            appearances_detail=appearances_detail,
            start_message=start_message,
            failure_detail=failure_detail,
            candidates_field=candidates_field,
            workflow=workflow,
            session_factory=session_factory,
            trusted_actor=trusted_actor,
        )

    @router.post("/filter-and-save-characters")
    async def filter_and_save_characters(
        request: Request,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> dict[str, Any]:
        try:
            raw_body = await request.json()
            chapter_id = raw_body.get("chapter_id")
            characters = raw_body.get("characters", [])
            if not chapter_id:
                raise HTTPException(status_code=400, detail="缺少章节ID")
            if not characters:
                raise HTTPException(status_code=400, detail="缺少角色列表")
            appearances = raw_body.get("appearances", [])
            if not appearances:
                raise HTTPException(status_code=400, detail="缺少角色出场数据")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail="过滤角色失败") from exc

        if session_factory is None:
            error = StoryboardSaveUnavailable()
            raise HTTPException(status_code=error.status_code, detail=error.detail)

        loop = asyncio.get_running_loop()
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="storyboard-save")
        future = executor.submit(
            _run_non_stream_request,
            session_factory,
            actor,
            chapter_id,
            characters,
            appearances,
        )
        wrapped = asyncio.wrap_future(future, loop=loop)
        cleanup_task: asyncio.Task[None] | None = None

        async def drain_worker() -> None:
            try:
                await asyncio.to_thread(future.result)
            except BaseException:
                pass
            await asyncio.to_thread(executor.shutdown, wait=True)

        async def close_worker() -> None:
            nonlocal cleanup_task
            if cleanup_task is None:
                cleanup_task = asyncio.create_task(drain_worker())
            cancellation: asyncio.CancelledError | None = None
            while not cleanup_task.done():
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError as exc:
                    cancellation = cancellation or exc
            cleanup_task.result()
            if cancellation is not None:
                raise cancellation

        try:
            result = await asyncio.shield(wrapped)
        except asyncio.CancelledError as cancelled:
            try:
                await close_worker()
            except asyncio.CancelledError:
                pass
            raise cancelled
        except StoryboardSaveChapterNotFound as exc:
            await close_worker()
            raise HTTPException(status_code=404, detail="章节不存在") from exc
        except SeriesNotFound as exc:
            await close_worker()
            raise HTTPException(status_code=404, detail=exc.args[0]) from exc
        except SeriesAccessDenied as exc:
            await close_worker()
            raise HTTPException(status_code=403, detail=exc.detail) from exc
        except StoryboardSaveForeignAssetReference as exc:
            await close_worker()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            await close_worker()
            raise HTTPException(status_code=500, detail="过滤角色失败") from exc
        except BaseException:
            await close_worker()
            raise
        await close_worker()
        return result

    return router


def _register_save_route(
    router: APIRouter,
    *,
    route_path: str,
    list_detail: str,
    appearances_detail: str,
    start_message: str,
    failure_detail: str,
    candidates_field: str,
    workflow: SaveWorkflow,
    session_factory: SessionFactory | None,
    trusted_actor: Callable[..., Any],
) -> None:
    @router.post(route_path)
    async def save_stream(
        request: Request,
        actor: TrustedActor = Depends(trusted_actor),
    ) -> StreamingResponse:
        try:
            raw_body = await request.json()
            chapter_id = raw_body.get("chapter_id")
            candidates = raw_body.get(candidates_field, [])
            appearances = raw_body.get("appearances", [])
            target_frames = raw_body.get("target_frames")
            dry_run = bool(raw_body.get("dry_run"))
            expected_content = _expected_content_from_body(raw_body)
            expected_series_id = _expected_series_id_from_body(raw_body)
            if not chapter_id:
                raise HTTPException(status_code=400, detail="缺少章节ID")
            if not candidates:
                raise HTTPException(status_code=400, detail=f"缺少{list_detail}")
            if not appearances:
                raise HTTPException(status_code=400, detail=f"缺少{appearances_detail}")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=failure_detail) from exc

        if session_factory is None:
            error = StoryboardSaveUnavailable()
            raise HTTPException(status_code=error.status_code, detail=error.detail)

        loop = asyncio.get_running_loop()
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="storyboard-save")
        session: Session | None = None
        authorized_series_id: str | None = None
        workflow_iterator: Iterator[dict[str, Any]] | None = None
        active_worker_futures: set[Future[Any]] = set()
        worker_futures_lock = threading.Lock()
        resource_state_lock = threading.Lock()
        cleanup_task: asyncio.Task[None] | None = None

        def forget_worker(future: Future[Any]) -> None:
            with worker_futures_lock:
                active_worker_futures.discard(future)

        def wait_for_active_workers() -> None:
            with worker_futures_lock:
                futures = tuple(active_worker_futures)
            for future in futures:
                try:
                    future.result()
                except BaseException:
                    pass

        async def run_worker(
            function: Callable[..., Any],
            *args: Any,
            on_result: Callable[[Any], None] | None = None,
        ) -> Any:
            def invoke() -> Any:
                result = function(*args)
                if on_result is not None:
                    on_result(result)
                return result

            future = executor.submit(invoke)
            with worker_futures_lock:
                active_worker_futures.add(future)
            future.add_done_callback(forget_worker)
            wrapped = asyncio.wrap_future(future, loop=loop)
            try:
                return await asyncio.shield(wrapped)
            except asyncio.CancelledError as cancelled:
                try:
                    with CancelScope(shield=True):
                        await asyncio.shield(wrapped)
                except BaseException:
                    raise cancelled
                raise cancelled

        def remember_authorized_session(value: tuple[Session, str]) -> None:
            nonlocal session, authorized_series_id
            with resource_state_lock:
                session, authorized_series_id = value

        def remember_iterator(value: Iterator[dict[str, Any]]) -> None:
            nonlocal workflow_iterator
            with resource_state_lock:
                workflow_iterator = value

        async def finish_resource_cleanup() -> None:
            await asyncio.to_thread(wait_for_active_workers)
            with resource_state_lock:
                current_session = session
                current_iterator = workflow_iterator
            try:
                if current_session is not None:
                    await run_worker(
                        _close_request_resources,
                        current_iterator,
                        current_session,
                    )
            finally:
                await asyncio.to_thread(executor.shutdown, wait=True)

        async def close_resources() -> None:
            nonlocal cleanup_task
            if cleanup_task is None:
                cleanup_task = asyncio.create_task(finish_resource_cleanup())
            await asyncio.shield(cleanup_task)

        async def close_without_masking_failure() -> None:
            try:
                await close_resources_before_exit()
            except BaseException:
                pass

        async def close_resources_before_exit() -> None:
            with CancelScope(shield=True):
                await close_resources()

        try:
            await run_worker(
                _open_session_and_authorize,
                session_factory,
                actor,
                chapter_id,
                expected_content,
                expected_series_id,
                on_result=remember_authorized_session,
            )

            def make_workflow() -> Iterator[dict[str, Any]]:
                with resource_state_lock:
                    current_session = session
                    current_series_id = authorized_series_id
                if current_session is None or current_series_id is None:
                    raise RuntimeError("storyboard save Session was not initialized")
                store = SqlAlchemyStoryboardSaveStore(current_session)
                return workflow(
                    store,
                    actor,
                    chapter_id,
                    candidates,
                    appearances,
                    dry_run=dry_run,
                    target_frames=target_frames,
                    expected_content=expected_content,
                    expected_series_id=current_series_id,
                )

            await run_worker(
                make_workflow,
                on_result=remember_iterator,
            )

            async def event_stream():
                try:
                    yield encode_sse({"type": "start", "message": start_message})
                    while True:
                        try:
                            has_event, event = await run_worker(
                                _advance,
                                workflow_iterator,
                            )
                        except Exception as exc:
                            yield encode_sse({"type": "error", "message": str(exc)})
                            return
                        if not has_event:
                            break
                        yield encode_sse(event or {})
                    yield encode_sse({"type": "done"})
                finally:
                    await close_resources_before_exit()

            return _ResourceOwnedStreamingResponse(
                event_stream(),
                close_resources=close_resources_before_exit,
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
        except asyncio.CancelledError:
            await close_without_masking_failure()
            raise
        except StoryboardSaveChapterNotFound as exc:
            await close_without_masking_failure()
            raise HTTPException(status_code=404, detail="章节不存在") from exc
        except StoryboardSaveSourceConflict as exc:
            await close_without_masking_failure()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except SeriesNotFound as exc:
            await close_without_masking_failure()
            raise HTTPException(status_code=404, detail=exc.args[0]) from exc
        except SeriesAccessDenied as exc:
            await close_without_masking_failure()
            raise HTTPException(status_code=403, detail=exc.detail) from exc
        except Exception as exc:
            await close_without_masking_failure()
            raise HTTPException(status_code=500, detail=failure_detail) from exc
        except BaseException:
            await close_without_masking_failure()
            raise


def _open_session_and_authorize(
    session_factory: SessionFactory,
    actor: TrustedActor,
    chapter_id: str,
    expected_content: list[Any] | None = None,
    expected_series_id: str | None = None,
) -> tuple[Session, str]:
    session = session_factory()
    try:
        store = SqlAlchemyStoryboardSaveStore(session)
        chapter = store.load_chapter(chapter_id)
        if chapter is None:
            raise StoryboardSaveChapterNotFound()
        store.require_access(actor, chapter.series_id)
        if expected_series_id is not None and chapter.series_id != expected_series_id:
            raise StoryboardSaveSourceConflict()
        if expected_content is not None and not chapter_content_matches_expected(
            chapter.content, expected_content
        ):
            raise StoryboardSaveSourceConflict()
        return session, chapter.series_id
    except BaseException:
        try:
            session.rollback()
        finally:
            session.close()
        raise


def _expected_content_from_body(raw_body: Any) -> list[Any] | None:
    if not isinstance(raw_body, dict) or "expected_content" not in raw_body:
        return None
    try:
        return ExpectedChapterContentBody.model_validate(
            {"expected_content": raw_body["expected_content"]}
        ).expected_content
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail="expected_content 必须是有效的章节分镜数组",
        ) from exc


def _expected_series_id_from_body(raw_body: Any) -> str | None:
    if not isinstance(raw_body, dict) or "expected_series_id" not in raw_body:
        return None
    try:
        return ExpectedSeriesIdBody.model_validate(
            {"expected_series_id": raw_body["expected_series_id"]}
        ).expected_series_id
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail="expected_series_id 必须是非空字符串",
        ) from exc


def _run_non_stream_request(
    session_factory: SessionFactory,
    actor: TrustedActor,
    chapter_id: str,
    characters: Any,
    appearances: Any,
) -> dict[str, Any]:
    session = session_factory()
    try:
        store = SqlAlchemyStoryboardSaveStore(session)
        chapter = store.load_chapter(chapter_id)
        if chapter is None:
            raise StoryboardSaveChapterNotFound()
        store.require_access(actor, chapter.series_id)
        return run_filter_and_save_characters(
            store, actor, chapter_id, characters, appearances
        )
    finally:
        try:
            session.rollback()
        finally:
            session.close()


def _advance(iterator: Iterator[dict[str, Any]]) -> tuple[bool, dict[str, Any] | None]:
    try:
        return True, next(iterator)
    except StopIteration:
        return False, None


def _close_request_resources(
    iterator: Iterator[dict[str, Any]] | None,
    session: Session,
) -> None:
    try:
        close = getattr(iterator, "close", None)
        if callable(close):
            close()
    finally:
        try:
            session.rollback()
        finally:
            session.close()


def mount_storyboard_save_routes(
    app: FastAPI,
    *,
    session_factory: SessionFactory | None,
    resolve_actor: ActorResolver | None,
) -> None:
    app.include_router(
        build_storyboard_save_router(
            session_factory=session_factory,
            resolve_actor=resolve_actor,
        )
    )
