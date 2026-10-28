"""Side-effect-free app factory for the isolated backend modules."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from haoai_backend.generation_credentials.application import CredentialRegistry
from haoai_backend.generation_credentials.domain import CredentialCrypto, HmacProofAuthority

from .admin_purchase_packages.http import build_admin_purchase_packages_router
from .admin_purchase_packages.persistence import purchase_packages_uow_factory
from .admin_series.http import build_admin_series_router
from .admin_smtp_accounts.crypto import LegacyFernet as SmtpLegacyFernet
from .admin_smtp_accounts.daily_reset import DailyResetState, SqlAlchemyDailyReset
from .admin_smtp_accounts.http import build_admin_smtp_accounts_router
from .admin_statistics.http import build_admin_statistics_router
from .admin_users.http import build_admin_users_router
from .admin_system_models.crypto import LegacyFernet
from .admin_system_models.http import build_admin_system_models_router
from .admin_system_models.persistence import SqlAlchemyAdminSystemModelUnitOfWork
from .admin_system_config.persistence import SqlAlchemyAdminSystemConfigUnitOfWork
from .admin_model_configs.http import build_admin_model_configs_router
from .model_configs.connection import ConnectionTester
from .model_configs.http import build_model_configs_router
from .model_configs.persistence import model_configs_unit_of_work_factory
from .redemption_codes.http import build_redemption_codes_router
from .upload_files.authentication import create_upload_file_identity_resolver
from .upload_files.domain import UploadFileActor
from .upload_files.http import build_upload_files_router
from .upload_files.local_files import LocalApprovedFileReader
from .upload_files.persistence import upload_directory_provider
from .admin_prompt_configs.http import build_admin_prompt_configs_router
from .admin_chapter_character_queries.http import build_admin_chapter_character_queries_router
from .admin_scene_prop_queries.http import build_admin_scene_prop_queries_router
from .admin_storyboard_chat_queries.http import build_admin_storyboard_chat_queries_router
from .admin_system_prompts.http import build_admin_system_prompts_router
from .admin_system_config.http import build_admin_system_config_router
from .admin_credit_queries.http import build_admin_credit_queries_router
from .admin_redemption_codes.http import build_admin_redemption_codes_router
from .admin_tasks.http import build_admin_task_router
from .admin_tasks.persistence import admin_task_unit_of_work_factory
from .asset_data.http import mount_asset_data_routes
from .chat_data.http import mount_chat_data_routes
from .chat_data.persistence import SqlAlchemyChatDataUnitOfWork
from .chat_data.ports import UnitOfWorkFactory as ChatDataUnitOfWorkFactory
from .download_links.compatibility import IdentityDownloadURLResolver
from .download_links.http import mount_download_links_routes
from .download_links.ports import DownloadURLResolver
from .generic_tasks.http import build_generic_tasks_router
from .generic_tasks.persistence import create_generic_task_uow_factory
from .canvas_data.http import mount_canvas_data_routes
from .canvas_data.persistence import SqlAlchemyCanvasDataUnitOfWork
from .canvas_data.ports import UnitOfWorkFactory as CanvasDataUnitOfWorkFactory
from .canvas_sources.http import build_canvas_sources_router
from .canvas_sources.persistence import SqlAlchemyCanvasSourceUnitOfWork
from .canvas_sources.ports import CanvasSourceUnitOfWorkFactory
from .chapter_asset_replacement.http import mount_chapter_asset_replacement_routes
from .chapter_asset_replacement.persistence import SqlAlchemyChapterAssetReplacementUnitOfWork
from .chapter_asset_replacement.ports import UnitOfWorkFactory as ChapterAssetReplacementUnitOfWorkFactory
from .chapter_locks.authentication import resolve_chapter_lock_actor as resolve_authenticated_chapter_lock_actor
from .chapter_locks.http import ChapterLockActorResolver, build_chapter_locks_router
from .chapter_locks.persistence import SqlAlchemyChapterLocksUnitOfWork
from .asset_data.persistence import SqlAlchemyAssetDataUnitOfWork
from .asset_data.ports import AssetDataUnitOfWorkFactory
from .authentication import (
    AuthenticationError,
    AuthenticationRuntime,
    create_task_identity_resolvers,
)
from .authentication.errors import unavailable
from .authentication.http import mount_authentication_routes
from .authentication.persistence import SqlAlchemyAuthenticationUnitOfWork
from .authentication.ports import UnitOfWorkFactory as AuthenticationUnitOfWorkFactory
from .smtp_delivery import (
    SmtpDeliveryService,
    SmtpDeliveryUnknown,
    SmtpDeliveryUnavailable,
    SqlAlchemySmtpDeliveryRepository,
    StdlibSmtpTransport,
)
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
from .series_exchange.http import mount_series_exchange_routes
from .series_exchange.persistence import series_exchange_uow_factory
from .storyboard_save.http import mount_storyboard_save_routes
from .media_uploads import MediaUploadApplication, build_media_uploads_router
from .media_uploads.authentication import create_media_upload_identity_resolver
from .fused_generation import (
    FusedGenerationImageStorage,
    FusedGenerationPlannedMediaStorage,
    FusedGenerationRuntime,
    FusedGenerationStorage,
)
from .fused_generation.http import build_fused_generation_router
from .fused_generation.persistence import fused_generation_uow_factory
from .fused_generation.execution import FusedGenerationExecution
from .fused_generation.recovery_worker import FusedGenerationRecoveryWorker
from .storyboard_generation import StoryboardGenerationRuntime
from .storyboard_generation.http import build_storyboard_generation_router
from .storyboard_generation.persistence import storyboard_generation_uow_factory
from .storyboard_asset_generation.http import build_storyboard_asset_generation_router
from .storyboard_asset_generation.ports import StoryboardAssetRuntime
from .storyboard_asset_generation.persistence import storyboard_asset_uow_factory
from .chat_media_admission import ChatMediaAdmissionConfiguration
from .chat_media_admission.http import build_chat_media_admission_router
from .chat_text_generation import ChatTextGenerationConfiguration
from .chat_text_generation.http import build_chat_text_generation_router
from .frame_optimize_http import build_frame_optimize_router
from .generation_core.admission_staging import build_media_snapshot_producer
from .generation_core.haoai_group.cancellation import (
    SqlAlchemyHaoaiGroupCancellation,
)
from .generation_core.frame_optimize_cancellation import (
    SqlAlchemyFrameOptimizeCancellation,
)
from .generation_contracts.errors import GenerationError
from .generation_runtime.assembly import (
    GenerationWorkerConfiguration,
    build_generation_worker_runtime,
)
from .generation_runtime.image_batch_assembly import (
    ImageBatchWorkerConfiguration,
    build_image_batch_worker_runtime,
)
from .generation_runtime.video_single_assembly import (
    VideoSingleWorkerConfiguration,
    build_video_single_worker_runtime,
)
from .generation_runtime.frame_review_assembly import (
    FrameReviewWorkerConfiguration,
    build_frame_review_worker_runtime,
)
from .generation_runtime.frame_optimize_assembly import (
    FrameOptimizeWorkerConfiguration,
    build_frame_optimize_worker_runtime,
)
from .generation_runtime.worker_service import GenerationWorkerService
from .generation_runtime.worker_capacity import (
    WorkerCapacityBootstrap,
    WorkerExecutionCapacity,
)
from .provider_status import ProviderStatusRuntime
from .runtime_status.authentication import create_account_only_current_user_resolver
from .runtime_status.configuration import RuntimeStatusConfiguration
from .runtime_status.http import build_runtime_status_router
from .runtime_status.image_fetcher import build_aiohttp_public_image_fetcher
from .runtime_status.ports import (
    CurrentUserResolver,
    DatabaseReadinessProbe,
    ObjectStorageReadinessProbe,
    PublicImageFetchResult,
    PublicImageFetcher,
)
from .series_maintenance.http import mount_series_maintenance_routes
from .series_maintenance.persistence import series_maintenance_uow_factory
from .provider_quota.crypto import LegacyFernetCipher as ProviderQuotaLegacyFernetCipher
from .provider_quota.http import build_provider_quota_router
from .provider_quota.ports import ProviderQuotaTransport
from .provider_quota.transport import AiohttpProviderQuotaTransport
from .zhangyuge.crypto import LegacyFernet as ZhangyugeLegacyFernet
from .zhangyuge.http import build_zhangyuge_router
from .zhangyuge.ports import BalanceTransport as ZhangyugeBalanceTransport
from .zhangyuge.transport import AiohttpBalanceTransport
from .aistarslab.http import build_aistarslab_router
from .aistarslab.ports import AistarslabTransport
from .aistarslab.provider import AioHttpTransport
from .aistarslab.readers import build_sqlalchemy_aistarslab_legacy_reader_factory
from .user_preferences.http import mount_user_preferences_routes
from .public_site_config.http import build_public_site_config_router
from .public_site_config.persistence import public_site_config_unit_of_work_factory
from .prompts.authentication import create_prompt_identity_resolvers
from .prompts.domain import PromptActor
from .prompts.http import PromptActorResolver, build_prompts_router
from .prompts.persistence import prompts_unit_of_work_factory
from .user_profiles.http import build_user_profiles_router
from .user_preferences.persistence import SqlAlchemyUserPreferencesUnitOfWork
from .teams import JoinQuota, build_teams_routers, create_team_uow_factory
from .teams.management.persistence import create_management_uow_factory
from .teams.series.persistence import create_series_uow_factory
from .shared.identity import TrustedActor
from .task_observation.authentication import (
    resolve_account_actor as resolve_task_account_actor,
    resolve_active_actor as resolve_task_active_actor,
)
from .task_observation.cancellation import SqlTaskCancellationWriter
from .task_observation.canvas_result_http import build_canvas_result_http_router
from .task_observation.canvas_result_discovery_http import (
    build_canvas_result_discovery_http_router,
)
from .task_observation.frame_review_cancellation import (
    build_frame_review_cancellation_facade,
)
from .task_observation.http import build_task_observation_router
from .task_observation.persistence import task_observation_unit_of_work_factory
from .task_observation.ports import (
    CancellationConnectionFactory,
    CancellationSignalRegistry,
)

SessionFactory = Callable[[], Session]
_GENERATION_WORKER_POLL_INTERVAL_SECONDS = 0.25


class _LazyAiohttpPublicImageFetcher:
    """Construct the HTTP client only when an authenticated proxy request uses it."""

    def __init__(self) -> None:
        self._fetcher: PublicImageFetcher | None = None

    async def fetch(
        self,
        url: str,
        *,
        max_bytes: int,
        timeout_seconds: int,
        allowed_content_prefixes: Sequence[str],
    ) -> PublicImageFetchResult:
        fetcher = self._fetcher
        if fetcher is None:
            fetcher = build_aiohttp_public_image_fetcher()
            self._fetcher = fetcher
        return await fetcher.fetch(
            url,
            max_bytes=max_bytes,
            timeout_seconds=timeout_seconds,
            allowed_content_prefixes=allowed_content_prefixes,
        )


def _public_task_actor_resolver(
    resolve_identity: Callable[[str], TrustedActor],
) -> ActorResolver:
    async def resolve(request: Request) -> TrustedActor:
        try:
            return await run_in_threadpool(
                resolve_identity,
                request.headers.get("authorization", ""),
            )
        except AuthenticationError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.detail,
                headers=exc.headers,
            ) from exc

    return resolve


def create_app(
    *,
    session_factory: SessionFactory | None = None,
    resolve_actor: ActorResolver | None = None,
    resolve_chapter_lock_actor: ChapterLockActorResolver | None = None,
    series_access_policy: SeriesAccessCheck | None = None,
    authentication_runtime: AuthenticationRuntime | None = None,
    download_url_resolver: DownloadURLResolver | None = None,
    team_join_quota: JoinQuota | None = None,
    resolve_task_account: ActorResolver | None = None,
    resolve_task_active: ActorResolver | None = None,
    task_cancellation_connection_factory: CancellationConnectionFactory | None = None,
    task_cancellation_signals: CancellationSignalRegistry | None = None,
    resolve_generic_task_active: ActorResolver | None = None,
    resolve_admin_task_actor: ActorResolver | None = None,
    provider_status_runtime: ProviderStatusRuntime | None = None,
    resolve_user_preferences_account: ActorResolver | None = None,
    resolve_prompt_active_actor: PromptActorResolver | None = None,
    resolve_prompt_administrator: PromptActorResolver | None = None,
    model_connection_tester: ConnectionTester | None = None,
    upload_directory: str | Path | None = None,
    provider_quota_transport: ProviderQuotaTransport | None = None,
    zhangyuge_balance_transport: ZhangyugeBalanceTransport | None = None,
    aistarslab_transport: AistarslabTransport | None = None,
    runtime_status_configuration: RuntimeStatusConfiguration | None = None,
    runtime_status_image_fetcher: PublicImageFetcher | None = None,
    canvas_result_image_fetcher: PublicImageFetcher | None = None,
    resolve_runtime_status_account: CurrentUserResolver | None = None,
    runtime_status_database_probe: DatabaseReadinessProbe | None = None,
    runtime_status_storage_probe: ObjectStorageReadinessProbe | None = None,
    media_upload_application: MediaUploadApplication | None = None,
    fused_generation_runtime: FusedGenerationRuntime | None = None,
    fused_generation_storage: FusedGenerationStorage | None = None,
    fused_generation_inline_image_storage: FusedGenerationImageStorage | None = None,
    fused_generation_planned_media_storage: FusedGenerationPlannedMediaStorage | None = None,
    fused_generation_credential_registry: CredentialRegistry | None = None,
    fused_generation_credential_crypto: CredentialCrypto | None = None,
    fused_generation_proof_authority: HmacProofAuthority | None = None,
    storyboard_generation_runtime: StoryboardGenerationRuntime | None = None,
    storyboard_asset_generation_runtime: StoryboardAssetRuntime | None = None,
    chat_media_admission_configuration: ChatMediaAdmissionConfiguration | None = None,
    chat_text_generation_configuration: ChatTextGenerationConfiguration | None = None,
    generation_worker_configuration: GenerationWorkerConfiguration | None = None,
    image_batch_worker_configuration: ImageBatchWorkerConfiguration | None = None,
    frame_review_worker_configuration: FrameReviewWorkerConfiguration | None = None,
    frame_optimize_worker_configuration: FrameOptimizeWorkerConfiguration | None = None,
    worker_capacity_bootstrap: WorkerCapacityBootstrap | None = None,
    video_single_worker_configuration: VideoSingleWorkerConfiguration | None = None,
) -> FastAPI:
    """Compose routes; explicitly configured generation workers start in lifespan."""
    if authentication_runtime is not None:
        authentication_runtime = replace(authentication_runtime)
    effective_session_factory = session_factory
    if effective_session_factory is None and authentication_runtime is not None:
        effective_session_factory = authentication_runtime.session_factory
    smtp_daily_reset = (
        SqlAlchemyDailyReset(
            effective_session_factory,
            state=DailyResetState(),
        )
        if effective_session_factory is not None
        else None
    )
    smtp_delivery_service: SmtpDeliveryService | None = None
    if authentication_runtime is not None:
        smtp_repository = (
            SqlAlchemySmtpDeliveryRepository(
                effective_session_factory,
                smtp_daily_reset,
            )
            if effective_session_factory is not None and smtp_daily_reset is not None
            else None
        )
        smtp_delivery_service = SmtpDeliveryService(
            smtp_repository,
            StdlibSmtpTransport(),
            SmtpLegacyFernet(authentication_runtime.secret_key),
        )

        if authentication_runtime.verification_email_sender is None:
            def send_verification_email(email: str, code: str) -> bool:
                assert smtp_delivery_service is not None
                try:
                    return smtp_delivery_service.send_verification_code(email, code)
                except SmtpDeliveryUnknown:
                    raise AuthenticationError(
                        502,
                        "验证码发送失败，请稍后再试",
                    ) from None
                except SmtpDeliveryUnavailable:
                    raise unavailable() from None

            authentication_runtime.verification_email_sender = send_verification_email

        if authentication_runtime.password_reset_sender is None:
            def send_password_reset_email(
                email: str,
                subject: str,
                html_body: str,
            ) -> bool:
                assert smtp_delivery_service is not None
                try:
                    return smtp_delivery_service.send(
                        email,
                        subject=subject,
                        html_body=html_body,
                    )
                except (SmtpDeliveryUnknown, SmtpDeliveryUnavailable):
                    return False

            authentication_runtime.password_reset_sender = send_password_reset_email
            if authentication_runtime.frontend_url is None:
                authentication_runtime.frontend_url = smtp_delivery_service.frontend_base_url
            if authentication_runtime.password_reset_preflight is None:
                def preflight_password_reset() -> None:
                    assert smtp_delivery_service is not None
                    try:
                        smtp_delivery_service.ensure_sender_ready()
                    except SmtpDeliveryUnavailable:
                        raise unavailable() from None

                authentication_runtime.password_reset_preflight = preflight_password_reset

    frame_optimize_session_bound = _frame_optimize_session_bound(
        frame_optimize_worker_configuration,
        effective_session_factory,
        authentication_runtime,
    )
    if frame_optimize_worker_configuration is not None and not frame_optimize_session_bound:
        raise ValueError(
            "frame.optimize dependencies must share the app authentication Session factory"
        )
    image_batch_session_bound = _image_batch_session_bound(
        image_batch_worker_configuration,
        effective_session_factory,
        authentication_runtime,
    )
    if image_batch_worker_configuration is not None and not image_batch_session_bound:
        raise ValueError(
            "image.batch dependencies must share the app authentication Session factory"
        )
    video_single_session_bound = _video_single_session_bound(
        video_single_worker_configuration,
        effective_session_factory,
        authentication_runtime,
    )
    if (
        video_single_worker_configuration is not None
        and not video_single_session_bound
    ):
        raise ValueError(
            "video.single dependencies must share the app authentication Session factory"
        )
    worker_capacity_needed = bool(
        generation_worker_configuration is not None
        or image_batch_worker_configuration is not None
        or video_single_worker_configuration is not None
        or frame_review_worker_configuration is not None
        or (
            frame_optimize_worker_configuration is not None
            and frame_optimize_worker_configuration.enabled
            and frame_optimize_session_bound
        )
    )
    worker_capacity = (
        WorkerExecutionCapacity() if worker_capacity_needed else None
    )

    def add_worker_capacity(configuration):
        if configuration is None or worker_capacity is None:
            return configuration
        return replace(
            configuration,
            runtime=replace(
                configuration.runtime,
                worker_capacity=worker_capacity,
            ),
        )

    effective_generation_worker_configuration = add_worker_capacity(
        generation_worker_configuration
    )
    effective_image_batch_worker_configuration = add_worker_capacity(
        image_batch_worker_configuration
    )
    effective_video_single_worker_configuration = add_worker_capacity(
        video_single_worker_configuration
    )
    effective_frame_review_worker_configuration = add_worker_capacity(
        frame_review_worker_configuration
    )
    effective_frame_optimize_worker_configuration = (
        add_worker_capacity(frame_optimize_worker_configuration)
        if frame_optimize_worker_configuration is not None
        and frame_optimize_worker_configuration.enabled
        else frame_optimize_worker_configuration
    )
    generation_worker_runtime = (
        build_generation_worker_runtime(effective_generation_worker_configuration)
        if effective_generation_worker_configuration is not None
        else None
    )
    image_batch_worker_runtime = (
        build_image_batch_worker_runtime(effective_image_batch_worker_configuration)
        if effective_image_batch_worker_configuration is not None
        else None
    )
    video_single_worker_runtime = (
        build_video_single_worker_runtime(effective_video_single_worker_configuration)
        if effective_video_single_worker_configuration is not None
        and video_single_session_bound
        else None
    )
    frame_review_worker_runtime = (
        build_frame_review_worker_runtime(effective_frame_review_worker_configuration)
        if effective_frame_review_worker_configuration is not None
        else None
    )
    frame_optimize_worker_runtime = (
        build_frame_optimize_worker_runtime(effective_frame_optimize_worker_configuration)
        if effective_frame_optimize_worker_configuration is not None
        and frame_optimize_session_bound
        else None
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        worker_service: GenerationWorkerService | None = None
        image_batch_service: GenerationWorkerService | None = None
        video_single_service: GenerationWorkerService | None = None
        frame_review_service: GenerationWorkerService | None = None
        frame_optimize_service: GenerationWorkerService | None = None
        active_fused_recovery_worker: FusedGenerationRecoveryWorker | None = None
        try:
            if worker_capacity is not None:
                bootstrap = worker_capacity_bootstrap or WorkerCapacityBootstrap()
                worker_capacity.configure(await bootstrap.resolve())
            if fused_recovery_worker is not None:
                fused_recovery_worker.start()
                active_fused_recovery_worker = fused_recovery_worker
                application.state.fused_generation_recovery_worker = (
                    fused_recovery_worker
                )
            if (
                generation_worker_runtime is not None
                and generation_worker_configuration is not None
            ):
                worker_service = GenerationWorkerService(
                    generation_worker_runtime,
                    task_type="image-single",
                    poll_interval_seconds=_GENERATION_WORKER_POLL_INTERVAL_SECONDS,
                    shutdown_timeout_seconds=(
                        generation_worker_configuration.runtime.shutdown_timeout_seconds
                    ),
                )
                application.state.generation_worker_service = worker_service
                worker_service.start()
            if (
                image_batch_worker_runtime is not None
                and effective_image_batch_worker_configuration is not None
            ):
                image_batch_service = GenerationWorkerService(
                    image_batch_worker_runtime,
                    task_type="batch-image",
                    poll_interval_seconds=_GENERATION_WORKER_POLL_INTERVAL_SECONDS,
                    shutdown_timeout_seconds=(
                        effective_image_batch_worker_configuration.runtime.shutdown_timeout_seconds
                    ),
                )
                application.state.image_batch_worker_service = image_batch_service
                image_batch_service.start()
            if (
                video_single_worker_runtime is not None
                and effective_video_single_worker_configuration is not None
            ):
                video_single_service = GenerationWorkerService(
                    video_single_worker_runtime,
                    task_type="video-single",
                    poll_interval_seconds=_GENERATION_WORKER_POLL_INTERVAL_SECONDS,
                    shutdown_timeout_seconds=(
                        effective_video_single_worker_configuration.runtime.shutdown_timeout_seconds
                    ),
                )
                application.state.video_single_worker_service = video_single_service
                video_single_service.start()
            if (
                frame_review_worker_runtime is not None
                and frame_review_worker_configuration is not None
            ):
                frame_review_service = GenerationWorkerService(
                    frame_review_worker_runtime,
                    task_type="ai-review",
                    poll_interval_seconds=_GENERATION_WORKER_POLL_INTERVAL_SECONDS,
                    shutdown_timeout_seconds=(
                        frame_review_worker_configuration.runtime.shutdown_timeout_seconds
                    ),
                )
                application.state.frame_review_worker_service = frame_review_service
                frame_review_service.start()
            if (
                frame_optimize_worker_runtime is not None
                and frame_optimize_worker_configuration is not None
                and frame_optimize_worker_configuration.enabled
            ):
                frame_optimize_service = GenerationWorkerService(
                    frame_optimize_worker_runtime,
                    task_type="batch-optimize",
                    poll_interval_seconds=_GENERATION_WORKER_POLL_INTERVAL_SECONDS,
                    shutdown_timeout_seconds=(
                        frame_optimize_worker_configuration.runtime.shutdown_timeout_seconds
                    ),
                )
                application.state.frame_optimize_worker_service = frame_optimize_service
                frame_optimize_service.start()
            yield
        finally:
            if worker_capacity is not None:
                worker_capacity.close_claims()
            if active_fused_recovery_worker is not None:
                await active_fused_recovery_worker.shutdown()
                application.state.fused_generation_recovery_worker = None
            if frame_optimize_service is not None:
                receipt = await run_in_threadpool(
                    frame_optimize_service.shutdown,
                    "application lifespan shutdown",
                )
                application.state.frame_optimize_worker_shutdown = receipt
                application.state.frame_optimize_worker_service = None
            if image_batch_service is not None:
                receipt = await run_in_threadpool(
                    image_batch_service.shutdown,
                    "application lifespan shutdown",
                )
                application.state.image_batch_worker_shutdown = receipt
                application.state.image_batch_worker_service = None
            if video_single_service is not None:
                receipt = await run_in_threadpool(
                    video_single_service.shutdown,
                    "application lifespan shutdown",
                )
                application.state.video_single_worker_shutdown = receipt
                application.state.video_single_worker_service = None
            if frame_review_service is not None:
                receipt = await run_in_threadpool(
                    frame_review_service.shutdown,
                    "application lifespan shutdown",
                )
                application.state.frame_review_worker_shutdown = receipt
                application.state.frame_review_worker_service = None
            if worker_service is not None:
                receipt = await run_in_threadpool(
                    worker_service.shutdown,
                    "application lifespan shutdown",
                )
                application.state.generation_worker_shutdown = receipt
                application.state.generation_worker_service = None

    app = FastAPI(
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.generation_worker_runtime = generation_worker_runtime
    app.state.generation_worker_service = None
    app.state.fused_generation_recovery_worker = None
    app.state.image_batch_worker_runtime = image_batch_worker_runtime
    app.state.image_batch_worker_service = None
    app.state.image_batch_worker_shutdown = None
    app.state.video_single_worker_runtime = video_single_worker_runtime
    app.state.video_single_worker_service = None
    app.state.video_single_worker_shutdown = None
    app.state.frame_review_worker_runtime = frame_review_worker_runtime
    app.state.frame_review_worker_service = None
    app.state.frame_optimize_worker_runtime = frame_optimize_worker_runtime
    app.state.frame_optimize_worker_service = None
    app.state.worker_execution_capacity = worker_capacity
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

    public_site_config_uow_factory = (
        public_site_config_unit_of_work_factory(effective_session_factory)
        if effective_session_factory is not None
        else None
    )
    app.include_router(
        build_public_site_config_router(uow_factory=public_site_config_uow_factory)
    )

    package_uow_factory = (
        purchase_packages_uow_factory(effective_session_factory)
        if effective_session_factory is not None
        else None
    )
    app.include_router(
        build_admin_purchase_packages_router(
            authentication_service=authentication_service,
            uow_factory=package_uow_factory,
        )
    )
    app.include_router(
        build_admin_system_prompts_router(
            authentication_service=authentication_service,
            session_factory=effective_session_factory,
        )
    )
    app.include_router(
        build_admin_system_config_router(
            authentication_service=authentication_service,
            session_factory=effective_session_factory,
        )
    )
    app.include_router(
        build_admin_credit_queries_router(
            authentication_service=authentication_service,
            session_factory=effective_session_factory,
        )
    )
    app.include_router(
        build_admin_redemption_codes_router(
            authentication_service=authentication_service,
            session_factory=effective_session_factory,
        )
    )
    app.include_router(
        build_admin_system_models_router(
            authentication_service=authentication_service,
            session_factory=effective_session_factory,
            cipher=LegacyFernet(
                authentication_runtime.secret_key
                if authentication_runtime is not None
                else None
            ),
        )
    )
    app.include_router(
        build_admin_model_configs_router(
            authentication_service=authentication_service,
            session_factory=effective_session_factory,
        )
    )
    app.include_router(
        build_admin_prompt_configs_router(
            authentication_service=authentication_service,
            session_factory=effective_session_factory,
        )
    )
    app.include_router(
        build_admin_chapter_character_queries_router(
            authentication_service=authentication_service,
            session_factory=effective_session_factory,
        )
    )
    app.include_router(
        build_admin_scene_prop_queries_router(
            authentication_service=authentication_service,
            session_factory=effective_session_factory,
        )
    )
    app.include_router(
        build_admin_storyboard_chat_queries_router(
            authentication_service=authentication_service,
            session_factory=effective_session_factory,
        )
    )
    app.include_router(
        build_admin_users_router(
            authentication_service=authentication_service,
            session_factory=effective_session_factory,
            password_hasher=(
                authentication_runtime.get_password_hasher()
                if authentication_runtime is not None
                else None
            ),
        )
    )
    app.include_router(
        build_admin_series_router(
            authentication_service=authentication_service,
            session_factory=effective_session_factory,
        )
    )
    app.include_router(
        build_admin_smtp_accounts_router(
            authentication_service=authentication_service,
            session_factory=effective_session_factory,
            cipher=SmtpLegacyFernet(
                authentication_runtime.secret_key
                if authentication_runtime is not None
                else None
            ),
            daily_reset=smtp_daily_reset,
        )
    )
    app.include_router(
        build_admin_statistics_router(
            authentication_service=authentication_service,
            session_factory=effective_session_factory,
        )
    )

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

    chapter_lock_uow_factory = None
    if effective_session_factory is not None:
        def make_chapter_lock_uow() -> SqlAlchemyChapterLocksUnitOfWork:
            return SqlAlchemyChapterLocksUnitOfWork(effective_session_factory())

        chapter_lock_uow_factory = make_chapter_lock_uow

    chapter_lock_actor_resolver = resolve_chapter_lock_actor
    if chapter_lock_actor_resolver is None:
        async def resolve_configured_chapter_lock_actor(request: Request):
            try:
                return await run_in_threadpool(
                    resolve_authenticated_chapter_lock_actor,
                    authentication_service,
                    request.headers.get("authorization", ""),
                )
            except AuthenticationError as exc:
                raise HTTPException(
                    status_code=exc.status_code,
                    detail=exc.detail,
                    headers=exc.headers,
                ) from exc

        chapter_lock_actor_resolver = resolve_configured_chapter_lock_actor

    app.include_router(
        build_chapter_locks_router(
            uow_factory=chapter_lock_uow_factory,
            resolve_actor=chapter_lock_actor_resolver,
        )
    )

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
        generation_session_factory=effective_session_factory,
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
        generation_session_factory=effective_session_factory,
    )
    canvas_source_uow_factory: CanvasSourceUnitOfWorkFactory | None = None
    if effective_session_factory is not None:
        def make_canvas_source_uow() -> SqlAlchemyCanvasSourceUnitOfWork:
            return SqlAlchemyCanvasSourceUnitOfWork(effective_session_factory())

        canvas_source_uow_factory = make_canvas_source_uow

    app.include_router(
        build_canvas_sources_router(
            uow_factory=canvas_source_uow_factory,
            resolve_actor=effective_resolver,
            receipt_secret=(
                authentication_runtime.secret_key
                if authentication_runtime is not None
                else None
            ),
        )
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

    status_image_fetcher = runtime_status_image_fetcher
    if status_image_fetcher is None:
        status_image_fetcher = _LazyAiohttpPublicImageFetcher()
    canvas_preview_fetcher = (
        status_image_fetcher
        if canvas_result_image_fetcher is None
        else canvas_result_image_fetcher
    )

    task_cancellation_writer = (
        SqlTaskCancellationWriter(task_cancellation_connection_factory)
        if task_cancellation_connection_factory is not None
        else None
    )
    frame_review_cancellation = None
    if frame_review_worker_configuration is not None:
        runtime_configuration = frame_review_worker_configuration.runtime
        registry_capabilities = frame_review_worker_configuration.registry_capabilities
        if (
            effective_session_factory is not None
            and runtime_configuration.session_factory is effective_session_factory
        ):
            frame_review_cancellation = build_frame_review_cancellation_facade(
                session_factory=runtime_configuration.session_factory,
                registry_capabilities=registry_capabilities,
                db_clock=runtime_configuration.db_clock,
                id_factory=runtime_configuration.id_factory,
            )
    elif (
        chat_text_generation_configuration is not None
        and chat_text_generation_configuration.frame_review_admission is not None
    ):
        registry_capabilities = (
            chat_text_generation_configuration.frame_review_admission.registry_capabilities
        )
        if effective_session_factory is not None:
            frame_review_cancellation = build_frame_review_cancellation_facade(
                session_factory=effective_session_factory,
                registry_capabilities=registry_capabilities,
            )
    frame_optimize_cancellation = None
    if (
        frame_optimize_worker_configuration is not None
        and frame_optimize_session_bound
        and effective_session_factory is not None
    ):
        capabilities = frame_optimize_worker_configuration.registry_capabilities
        if capabilities.crypto is not None and capabilities.proof_authority is not None:
            frame_optimize_cancellation = SqlAlchemyFrameOptimizeCancellation(
                session_factory=effective_session_factory,
                db_clock=frame_optimize_worker_configuration.runtime.db_clock,
                id_factory=frame_optimize_worker_configuration.runtime.id_factory,
                crypto=capabilities.crypto,
                proof_authority=capabilities.proof_authority,
            )
    haoai_group_cancellation = None
    if (
        video_single_worker_configuration is not None
        and video_single_session_bound
        and effective_session_factory is not None
    ):
        capabilities = video_single_worker_configuration.registry_capabilities
        if isinstance(capabilities.proof_authority, HmacProofAuthority):
            haoai_group_cancellation = SqlAlchemyHaoaiGroupCancellation(
                session_factory=effective_session_factory,
                db_clock=video_single_worker_configuration.runtime.db_clock,
                proof_authority=capabilities.proof_authority,
                id_factory=video_single_worker_configuration.runtime.id_factory,
            )
    app.include_router(
        build_task_observation_router(
            uow_factory=task_uow_factory,
            resolve_account_actor=account_actor_resolver,
            resolve_active_actor=active_actor_resolver,
            cancellation_signals=task_cancellation_signals,
            cancellation_writer=task_cancellation_writer,
            frame_review_cancellation=frame_review_cancellation,
            frame_optimize_cancellation=frame_optimize_cancellation,
            haoai_group_cancellation=haoai_group_cancellation,
        )
    )
    app.include_router(
        build_canvas_result_http_router(
            session_factory=effective_session_factory,
            resolve_account_actor=account_actor_resolver,
            image_fetcher=canvas_preview_fetcher,
        )
    )
    app.include_router(
        build_canvas_result_discovery_http_router(
            session_factory=effective_session_factory,
            resolve_account_actor=account_actor_resolver,
        )
    )

    task_identity_resolvers = create_task_identity_resolvers(
        runtime=authentication_runtime,
        unit_of_work_factory=authentication_uow_factory,
    )
    generic_active_actor_resolver = resolve_generic_task_active
    if generic_active_actor_resolver is None:
        if resolve_actor is not None:
            generic_active_actor_resolver = resolve_actor
        else:
            generic_active_actor_resolver = _public_task_actor_resolver(
                task_identity_resolvers.active
            )

    admin_actor_resolver = resolve_admin_task_actor
    if admin_actor_resolver is None:
        admin_actor_resolver = _public_task_actor_resolver(
            task_identity_resolvers.admin
        )

    generic_task_uow_factory = create_generic_task_uow_factory(
        effective_session_factory
    )
    admin_task_uow_factory = None
    if effective_session_factory is not None:
        admin_task_uow_factory = admin_task_unit_of_work_factory(
            effective_session_factory
        )

    app.include_router(
        build_generic_tasks_router(
            uow_factory=generic_task_uow_factory,
            resolve_active_actor=generic_active_actor_resolver,
            provider_status_runtime=provider_status_runtime,
        )
    )
    app.include_router(
        build_admin_task_router(
            uow_factory=admin_task_uow_factory,
            resolve_admin_actor=admin_actor_resolver,
        )
    )
    user_preferences_uow_factory = None
    if effective_session_factory is not None:
        def make_user_preferences_uow() -> SqlAlchemyUserPreferencesUnitOfWork:
            return SqlAlchemyUserPreferencesUnitOfWork(effective_session_factory())

        user_preferences_uow_factory = make_user_preferences_uow

    user_preferences_account_resolver = resolve_user_preferences_account
    if user_preferences_account_resolver is None:
        user_preferences_account_resolver = resolve_task_account
    if user_preferences_account_resolver is None:
        async def resolve_configured_user_preferences_account(request):
            return await run_in_threadpool(
                resolve_task_account_actor,
                authentication_service,
                request.headers.get("authorization", ""),
            )

        user_preferences_account_resolver = resolve_configured_user_preferences_account

    mount_user_preferences_routes(
        app,
        uow_factory=user_preferences_uow_factory,
        resolve_account_actor=user_preferences_account_resolver,
    )

    prompt_uow_factory = (
        prompts_unit_of_work_factory(effective_session_factory)
        if effective_session_factory is not None
        else None
    )
    prompt_identity_resolvers = create_prompt_identity_resolvers(authentication_service)

    def public_prompt_resolver(
        resolve_identity: Callable[[str], PromptActor],
    ) -> PromptActorResolver:
        async def resolve_prompt_actor(request: Request) -> PromptActor:
            try:
                return await run_in_threadpool(
                    resolve_identity,
                    request.headers.get("authorization", ""),
                )
            except AuthenticationError as exc:
                raise HTTPException(
                    status_code=exc.status_code,
                    detail=exc.detail,
                    headers=exc.headers,
                ) from exc

        return resolve_prompt_actor

    prompt_active_resolver = resolve_prompt_active_actor
    if prompt_active_resolver is None:
        prompt_active_resolver = public_prompt_resolver(prompt_identity_resolvers.active)
    prompt_administrator_resolver = resolve_prompt_administrator
    if prompt_administrator_resolver is None:
        prompt_administrator_resolver = public_prompt_resolver(
            prompt_identity_resolvers.administrator
        )

    app.include_router(
        build_prompts_router(
            uow_factory=prompt_uow_factory,
            resolve_active_actor=prompt_active_resolver,
            resolve_administrator=prompt_administrator_resolver,
        )
    )
    app.include_router(
        build_user_profiles_router(
            authentication_service=authentication_service,
            session_factory=effective_session_factory,
        )
    )
    app.include_router(
        build_model_configs_router(
            authentication_service=authentication_service,
            session_factory=effective_session_factory,
            connection_tester=model_connection_tester,
        )
    )
    app.include_router(
        build_redemption_codes_router(
            authentication_service=authentication_service,
            session_factory=effective_session_factory,
        )
    )

    upload_actor_resolver: Callable[[Request], UploadFileActor] | None = None
    if authentication_service is not None:
        resolve_upload_identity = create_upload_file_identity_resolver(
            authentication_service
        )

        def resolve_upload_active_actor(request: Request) -> UploadFileActor:
            return resolve_upload_identity(
                request.headers.get("authorization", "")
            )

        upload_actor_resolver = resolve_upload_active_actor

    app.include_router(
        build_upload_files_router(
            directory_provider=upload_directory_provider(
                effective_session_factory,
                upload_directory,
            ),
            file_reader=LocalApprovedFileReader(),
            resolve_active_actor=upload_actor_resolver,
        )
    )

    secret_key = (
        authentication_runtime.secret_key
        if authentication_runtime is not None
        else None
    )
    app.include_router(
        build_provider_quota_router(
            authentication_service=authentication_service,
            session_factory=effective_session_factory,
            cipher=ProviderQuotaLegacyFernetCipher(secret_key),
            transport=(
                provider_quota_transport
                if provider_quota_transport is not None
                else AiohttpProviderQuotaTransport()
            ),
        )
    )
    app.include_router(
        build_zhangyuge_router(
            authentication_service=authentication_service,
            session_factory=effective_session_factory,
            balance_transport=(
                zhangyuge_balance_transport
                if zhangyuge_balance_transport is not None
                else AiohttpBalanceTransport()
            ),
            cipher=ZhangyugeLegacyFernet(secret_key),
        )
    )

    aistarslab_model_configs_uow_factory = (
        model_configs_unit_of_work_factory(effective_session_factory)
        if effective_session_factory is not None
        else None
    )
    aistarslab_reader_session_factory = effective_session_factory
    if aistarslab_reader_session_factory is None:
        def unavailable_aistarslab_session_factory() -> Session:
            raise RuntimeError("AIStarsLab persistence is not configured")

        aistarslab_reader_session_factory = unavailable_aistarslab_session_factory
    aistarslab_legacy_reader_factory = (
        build_sqlalchemy_aistarslab_legacy_reader_factory(
            aistarslab_reader_session_factory
        )
    )
    aistarslab_system_config_uow_factory = None
    aistarslab_system_model_uow_factory = None
    if effective_session_factory is not None:
        def make_aistarslab_system_config_uow() -> SqlAlchemyAdminSystemConfigUnitOfWork:
            return SqlAlchemyAdminSystemConfigUnitOfWork(effective_session_factory())

        def make_aistarslab_system_model_uow() -> SqlAlchemyAdminSystemModelUnitOfWork:
            return SqlAlchemyAdminSystemModelUnitOfWork(effective_session_factory())

        aistarslab_system_config_uow_factory = make_aistarslab_system_config_uow
        aistarslab_system_model_uow_factory = make_aistarslab_system_model_uow

    app.include_router(
        build_aistarslab_router(
            authentication_service=authentication_service,
            model_configs_uow_factory=aistarslab_model_configs_uow_factory,
            legacy_reader_factory=aistarslab_legacy_reader_factory,
            system_config_uow_factory=aistarslab_system_config_uow_factory,
            system_model_uow_factory=aistarslab_system_model_uow_factory,
            cipher=LegacyFernet(secret_key),
            transport=(
                aistarslab_transport
                if aistarslab_transport is not None
                else AioHttpTransport()
            ),
        )
    )

    status_configuration = runtime_status_configuration
    if status_configuration is None:
        status_configuration = RuntimeStatusConfiguration(
            service_name="Hao AI Backend",
            version="0.1.0",
            readiness_initialized=True,
            object_storage_configured=False,
            probe_timeout_seconds=1.0,
            ready_cache_seconds=5.0,
            initial_failure_backoff_seconds=1.0,
            max_failure_backoff_seconds=30.0,
        )

    status_account_resolver = resolve_runtime_status_account
    if status_account_resolver is None and authentication_service is not None:
        status_account_resolver = create_account_only_current_user_resolver(
            authentication_service
        )

    app.include_router(
        build_runtime_status_router(
            configuration=status_configuration,
            image_fetcher=status_image_fetcher,
            resolve_current_user=status_account_resolver,
            database_probe=runtime_status_database_probe,
            object_storage_probe=runtime_status_storage_probe,
        )
    )

    maintenance_uow_factory = (
        series_maintenance_uow_factory(effective_session_factory)
        if effective_session_factory is not None
        else None
    )
    mount_series_maintenance_routes(
        app,
        uow_factory=maintenance_uow_factory,
        resolve_actor=effective_resolver,
    )

    exchange_uow_factory = (
        series_exchange_uow_factory(effective_session_factory)
        if effective_session_factory is not None
        else None
    )
    mount_series_exchange_routes(
        app,
        uow_factory=exchange_uow_factory,
        resolve_actor=effective_resolver,
    )
    mount_storyboard_save_routes(
        app,
        session_factory=effective_session_factory,
        resolve_actor=effective_resolver,
    )
    media_upload_identity_resolver = create_media_upload_identity_resolver(
        authentication_service
    )
    app.include_router(
        build_media_uploads_router(
            application=media_upload_application,
            resolve_active_member=media_upload_identity_resolver,
        )
    )
    fused_uow_factory = (
        fused_generation_uow_factory(
            effective_session_factory,
            decrypt_api_key=LegacyFernet(secret_key).decrypt,
        )
        if effective_session_factory is not None
        else None
    )
    fused_generation_execution = FusedGenerationExecution(
        runtime=fused_generation_runtime,
        uow_factory=fused_uow_factory,
        storage=fused_generation_storage,
        inline_image_storage=fused_generation_inline_image_storage,
        planned_media_storage=fused_generation_planned_media_storage,
        credential_registry=fused_generation_credential_registry,
        credential_crypto=fused_generation_credential_crypto,
        proof_authority=fused_generation_proof_authority,
    )
    fused_recovery_worker = (
        FusedGenerationRecoveryWorker(
            fused_generation_execution,
            fused_uow_factory,
        )
        if fused_uow_factory is not None
        and fused_generation_runtime is not None
        and fused_generation_credential_registry is not None
        and fused_generation_credential_crypto is not None
        and fused_generation_proof_authority is not None
        else None
    )
    app.include_router(
        build_fused_generation_router(
            uow_factory=fused_uow_factory,
            resolve_actor=effective_resolver,
            runtime=fused_generation_runtime,
            resolve_receipt_actor=(
                account_actor_resolver
                if resolve_task_account is not None or authentication_runtime is not None
                else None
            ),
            storage=fused_generation_storage,
            inline_image_storage=fused_generation_inline_image_storage,
            planned_media_storage=fused_generation_planned_media_storage,
            credential_registry=fused_generation_credential_registry,
            credential_crypto=fused_generation_credential_crypto,
            proof_authority=fused_generation_proof_authority,
            execution=fused_generation_execution,
        )
    )
    storyboard_uow_factory = (
        storyboard_generation_uow_factory(
            effective_session_factory,
            decrypt_api_key=LegacyFernet(secret_key).decrypt,
        )
        if effective_session_factory is not None
        else None
    )
    app.include_router(
        build_storyboard_generation_router(
            uow_factory=storyboard_uow_factory,
            resolve_actor=effective_resolver,
            runtime=storyboard_generation_runtime,
            session_factory=effective_session_factory,
            decrypt_api_key=LegacyFernet(secret_key).decrypt,
        )
    )
    storyboard_asset_uow_factory_value = (
        storyboard_asset_uow_factory(
            effective_session_factory,
            decrypt_api_key=LegacyFernet(secret_key).decrypt,
        )
        if effective_session_factory is not None
        else None
    )
    app.include_router(
        build_storyboard_asset_generation_router(
            uow_factory=storyboard_asset_uow_factory_value,
            resolve_actor=effective_resolver,
            runtime=storyboard_asset_generation_runtime,
        )
    )

    chat_media_producer_binding = None
    if chat_media_admission_configuration is not None:
        try:
            chat_media_producer_binding = build_media_snapshot_producer(
                authentication_service,
                chat_media_admission_configuration.registry_capabilities,
                server_id_factory=(
                    chat_media_admission_configuration.server_id_factory
                ),
                policies=chat_media_admission_configuration.policies,
                versions=chat_media_admission_configuration.versions,
            )
        except GenerationError:
            chat_media_producer_binding = None
    app.include_router(
        build_chat_media_admission_router(
            authentication_service=authentication_service,
            session_factory=effective_session_factory,
            producer_binding=chat_media_producer_binding,
            enabled_operations=(
                chat_media_admission_configuration.enabled_operations
                if chat_media_admission_configuration is not None
                else None
            ),
            image_references_enabled=(
                chat_media_admission_configuration.image_references_enabled
                if chat_media_admission_configuration is not None
                else True
            ),
        )
    )
    chat_text_generation_router = build_chat_text_generation_router(
        authentication_service=authentication_service,
        session_factory=effective_session_factory,
        configuration=chat_text_generation_configuration,
        decrypt_api_key=(
            LegacyFernet(authentication_runtime.secret_key).decrypt
            if authentication_runtime is not None
            and authentication_runtime.secret_key
            else None
        ),
    )
    app.include_router(chat_text_generation_router)
    app.state.chat_text_generation_execution = getattr(
        chat_text_generation_router, "chat_text_generation_execution", None
    )
    app.include_router(
        build_frame_optimize_router(
            authentication_service=authentication_service,
            session_factory=effective_session_factory,
            configuration=(
                frame_optimize_worker_configuration
                if frame_optimize_session_bound
                else None
            ),
        )
    )

    return app


def _frame_optimize_session_bound(
    configuration: FrameOptimizeWorkerConfiguration | None,
    session_factory: SessionFactory | None,
    authentication_runtime: AuthenticationRuntime | None,
) -> bool:
    if configuration is None:
        return True
    return bool(
        session_factory is not None
        and configuration.runtime.session_factory is session_factory
        and configuration.registry_capabilities.read_session_factory is session_factory
        and (
            authentication_runtime is None
            or authentication_runtime.session_factory is session_factory
        )
    )


def _image_batch_session_bound(
    configuration: ImageBatchWorkerConfiguration | None,
    session_factory: SessionFactory | None,
    authentication_runtime: AuthenticationRuntime | None,
) -> bool:
    if configuration is None:
        return True
    return bool(
        session_factory is not None
        and configuration.runtime.session_factory is session_factory
        and configuration.registry_capabilities.read_session_factory is session_factory
        and (
            authentication_runtime is None
            or authentication_runtime.session_factory is session_factory
        )
    )


def _video_single_session_bound(
    configuration: VideoSingleWorkerConfiguration | None,
    session_factory: SessionFactory | None,
    authentication_runtime: AuthenticationRuntime | None,
) -> bool:
    if configuration is None:
        return True
    return bool(
        session_factory is not None
        and configuration.runtime.session_factory is session_factory
        and configuration.registry_capabilities.read_session_factory is session_factory
        and (
            authentication_runtime is None
            or authentication_runtime.session_factory is session_factory
        )
    )
