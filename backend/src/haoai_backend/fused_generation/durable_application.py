"""Durable submission fence for fused image generation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any
from uuid import uuid4

from haoai_backend.generation_contracts.types import CredentialBinding
from haoai_backend.generation_credentials.application import CredentialRegistry
from haoai_backend.generation_credentials.domain import (
    CredentialCrypto,
    CredentialScope,
    HmacProofAuthority,
)
from haoai_backend.shared.errors import BusinessError
from haoai_backend.shared.identity import TrustedActor

from .application import (
    _reference_mapping,
    _resolve_members,
    _resolve_prompt,
    _resolve_target_frame,
)
from .durable_values import (
    json_digest,
    open_json_value,
    prepared_from_document,
    prepared_to_document,
    seal_json_value,
    snapshot_proof_payload,
)
from .domain import FusedFrameTarget, FusedServerImageModel, PreparedFusedGeneration
from .errors import (
    FusedGenerationBadRequest,
    FusedGenerationConflict,
    FusedGenerationUnavailable,
)
from .ports import FusedGenerationUnitOfWorkFactory
from .schemas import FusedAssetGenerateRequest

_OPERATION = "fused.image"
_LEGACY_REQUEST_FIELDS = (
    "series_id",
    "member_type",
    "members",
    "model_id",
    "prompt_id",
    "prompt_system",
    "prompt_user",
    "size",
    "chapter_id",
    "frame_index",
    "name",
)
_LEGACY_MEMBER_FIELDS = ("kind", "id", "url", "name")
_TARGET_PROOF_FIELDS = ("storyboard_asset_id", "expected_frame", "expected_content")


@dataclass(frozen=True, slots=True)
class DurableSubmissionResponse:
    status_code: int
    body: Mapping[str, object]
    execute_submission: bool = False


def submit_durable_fused_generation(
    uow_factory: FusedGenerationUnitOfWorkFactory | None,
    *,
    registry: CredentialRegistry | None,
    crypto: CredentialCrypto | None,
    proof_authority: HmacProofAuthority | None,
    actor: TrustedActor,
    request: FusedAssetGenerateRequest,
    idempotency_key: str,
) -> DurableSubmissionResponse:
    if (
        uow_factory is None
        or registry is None
        or crypto is None
        or proof_authority is None
        or crypto.ready() is not True
    ):
        raise FusedGenerationUnavailable("融合生成持久凭据能力尚未接线")
    if not _valid_idempotency_key(idempotency_key):
        raise FusedGenerationBadRequest("Idempotency-Key 格式无效")

    request_document = canonical_fused_request_document(request)
    request_digest = json_digest(request_document)
    existing = _lookup_submission(uow_factory, actor.user_id, idempotency_key)
    if existing is not None:
        return _existing_submission_response(
            uow_factory,
            existing,
            request_digest=request_digest,
            crypto=crypto,
            proof_authority=proof_authority,
        )

    uow = uow_factory()
    try:
        # Recheck under the same transaction as the unique reservation. This
        # prevents concurrent callers from charging before the key fence wins.
        existing = uow.find_durable_submission(actor.user_id, idempotency_key)
        if existing is not None:
            uow.rollback()
            return _existing_submission_response(
                uow_factory,
                existing,
                request_digest=request_digest,
                crypto=crypto,
                proof_authority=proof_authority,
            )

        uow.require_series_access(actor, request.series_id)
        members, reference_images, reference_names = _resolve_members(uow, request)
        if not members:
            raise FusedGenerationBadRequest("没有有效的融合成员")
        if not reference_images:
            raise FusedGenerationBadRequest("融合成员都没有图片，无法生成")
        target_frame = _resolve_target_frame(uow, request)
        model, scope = uow.resolve_durable_model_identity(actor.user_id, request.model_id)
        captured = uow.capture_durable_binding(registry, scope)
        if captured is None:
            raise FusedGenerationUnavailable("模型凭据尚未完成受控登记")
        locked_model, locked_scope = uow.resolve_durable_model_identity(
            actor.user_id,
            request.model_id,
        )
        if locked_scope != scope or locked_model != model:
            raise FusedGenerationUnavailable("模型执行配置在凭据核对期间发生变化，请重试")

        binding = captured.binding
        endpoint_policy = captured.endpoint_policy
        if (
            binding.source != scope.source
            or binding.row_id != scope.row_id
            or binding.owner_or_system != scope.owner_or_system
            or binding.provider != scope.provider
            or endpoint_policy.provider_origin != binding.endpoint_origin
        ):
            raise FusedGenerationUnavailable("模型 endpoint 与原始凭据登记不一致")
        model = replace(
            locked_model,
            base_url=(
                endpoint_policy.provider_origin + endpoint_policy.api_base_path
            ),
        )

        submission_id = uow.new_id()
        task_id = uow.new_id()
        message_id = uow.new_id() if request.chapter_id else None
        facts_document = {
            "version": 1,
            "request": request_document,
            "members": members,
            "reference_images": reference_images,
            "reference_names": reference_names,
            "target_frame": (
                {
                    "chapter_id": target_frame.chapter_id,
                    "frame_index": target_frame.frame_index,
                    "identity": target_frame.identity,
                }
                if target_frame is not None
                else None
            ),
            "model": {
                "id": model.id,
                "name": model.name,
                "provider": model.provider,
                "wire_model": model.wire_model,
                "base_url": model.base_url,
                "params": model.params,
                "cost_per_call": model.cost_per_call,
                "is_system": model.is_system,
            },
        }
        encrypted_request, request_nonce, seal_key_id, crypto_algorithm = seal_json_value(
            facts_document,
            crypto=crypto,
            submission_id=submission_id,
            owner_id=actor.user_id,
            request_digest=request_digest,
            task_id=task_id,
            binding=binding,
            purpose="fused-preprompt-request-v1",
        )
        uow.reserve_durable_submission(
            submission_id=submission_id,
            owner_id=actor.user_id,
            idempotency_key=idempotency_key,
            request_digest=request_digest,
            model=model,
            scope=scope,
            captured_binding=captured,
            task_id=task_id,
            message_id=message_id,
            encrypted_request=encrypted_request,
            request_nonce=request_nonce,
            seal_key_id=seal_key_id,
            crypto_algorithm=crypto_algorithm,
        )
        uow.commit()
    except BusinessError:
        uow.rollback()
        raise
    except Exception as error:
        uow.rollback()
        # A unique-key race or an uncertain commit is resolved only by reading
        # the original key; no second fee transaction is attempted.
        existing = _lookup_submission(uow_factory, actor.user_id, idempotency_key)
        if existing is None:
            raise FusedGenerationUnavailable("融合任务提交结果暂时无法确认") from error
        return _existing_submission_response(
            uow_factory,
            existing,
            request_digest=request_digest,
            crypto=crypto,
            proof_authority=proof_authority,
        )
    finally:
        uow.close()

    # Preserve the historical independent fee commit before prompt lookup and
    # task/message staging. A prompt rejection is durably replayable as-is.
    return _complete_snapshot_and_accept(
        uow_factory,
        registry=registry,
        crypto=crypto,
        proof_authority=proof_authority,
        actor=actor,
        request=request,
        request_digest=request_digest,
        submission_id=submission_id,
        task_id=task_id,
        message_id=message_id,
        model=model,
        binding=binding,
        members=tuple(members),
        reference_images=tuple(reference_images),
        reference_names=reference_names,
        target_frame=target_frame,
    )


def canonical_fused_request_document(request: FusedAssetGenerateRequest) -> dict[str, object]:
    """Return the published legacy request shape plus only active target proofs."""
    dumped = request.model_dump(mode="json")
    document: dict[str, object] = {
        field: dumped[field]
        for field in _LEGACY_REQUEST_FIELDS
    }
    document["members"] = [
        {field: member[field] for field in _LEGACY_MEMBER_FIELDS}
        for member in dumped["members"]
    ]
    for field in _TARGET_PROOF_FIELDS:
        value = dumped[field]
        if value is not None:
            document[field] = value
    return document


def _complete_snapshot_and_accept(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    *,
    registry: CredentialRegistry,
    crypto: CredentialCrypto,
    proof_authority: HmacProofAuthority,
    actor: TrustedActor,
    request: FusedAssetGenerateRequest,
    request_digest: str,
    submission_id: str,
    task_id: str,
    message_id: str | None,
    model: FusedServerImageModel,
    binding: CredentialBinding,
    members: tuple[Mapping[str, object], ...],
    reference_images: tuple[str, ...],
    reference_names: list[str],
    target_frame: FusedFrameTarget | None,
) -> DurableSubmissionResponse:
    uow = uow_factory()
    try:
        mapping = _reference_mapping(request.member_type, reference_names)
        try:
            prompt = _resolve_prompt(uow, actor.user_id, request, mapping)
        except BusinessError as error:
            response_json = json.dumps(
                {"detail": error.detail}, ensure_ascii=False, separators=(",", ":")
            )
            uow.reject_durable_submission(
                submission_id=submission_id,
                status_code=error.status_code,
                response_json=response_json,
            )
            try:
                uow.commit()
            except Exception:
                # A lost acknowledgement is resolved only by reading this
                # submission's existing key; never repeat the fee or rejection
                # write.
                uow.rollback()
                confirmed = _lookup_submission_by_id(uow_factory, submission_id)
                if confirmed is None or confirmed.get("stage") != "rejected_after_charge":
                    return _charged_incomplete(confirmed)
                return _response_for_row(confirmed)
            return DurableSubmissionResponse(error.status_code, {"detail": error.detail})

        label = "角色" if request.member_type == "character" else "道具"
        prepared = PreparedFusedGeneration(
            task_id=task_id,
            message_id=message_id,
            user_id=actor.user_id,
            series_id=request.series_id,
            chapter_id=request.chapter_id,
            member_type=request.member_type,
            members=members,
            name=request.name or f"{label}融合 {len(members)}合1",
            size=request.size,
            prompt=prompt,
            reference_images=reference_images,
            model_id=model.id,
            model_name=model.name,
            credit_cost=model.cost_per_call if model.is_system else 0,
            server_model=model,
            target_frame=target_frame,
            submission_id=submission_id,
            idempotency_key=None,
            request_digest=request_digest,
            credential_binding=binding,
        )
        snapshot_document = prepared_to_document(prepared)
        snapshot_digest = json_digest(snapshot_document)
        signature = proof_authority.sign(
            "fused-generation-snapshot-v1",
            snapshot_proof_payload(
                snapshot_digest,
                submission_id=submission_id,
                owner_id=actor.user_id,
                request_digest=request_digest,
                task_id=task_id,
                binding=binding,
            ),
        )
        encrypted_snapshot, snapshot_nonce, _key_id, _algorithm = seal_json_value(
            snapshot_document,
            crypto=crypto,
            submission_id=submission_id,
            owner_id=actor.user_id,
            request_digest=request_digest,
            task_id=task_id,
            binding=binding,
            purpose="fused-final-snapshot-v1",
        )
        written = uow.save_durable_snapshot(
            submission_id=submission_id,
            encrypted_snapshot=encrypted_snapshot,
            snapshot_nonce=snapshot_nonce,
            snapshot_proof_key_id=proof_authority.key_id,
            snapshot_proof=signature,
        )
        if not written:
            uow.rollback()
            row = _lookup_submission_by_id(uow_factory, submission_id)
            return _response_for_row(row) if row is not None else _charged_incomplete()
        uow.commit()
    except Exception:
        uow.rollback()
        row = _lookup_submission_by_id(uow_factory, submission_id)
        if row is None or row.get("stage") not in {"snapshot_ready", "accepted"}:
            return _charged_incomplete(row)
    finally:
        uow.close()

    return _accept_snapshot_if_ready(
        uow_factory,
        submission_id,
        actor.user_id,
        crypto=crypto,
        proof_authority=proof_authority,
    )


def lookup_durable_submission(
    uow_factory: FusedGenerationUnitOfWorkFactory | None,
    *,
    owner_id: str,
    idempotency_key: str,
) -> Mapping[str, object] | None:
    if uow_factory is None or not _valid_idempotency_key(idempotency_key):
        return None
    return _lookup_submission(uow_factory, owner_id, idempotency_key)


def lookup_durable_submission_receipt(
    uow_factory: FusedGenerationUnitOfWorkFactory | None,
    *,
    owner_id: str,
    idempotency_key: str,
) -> Mapping[str, object] | None:
    if uow_factory is None or not _valid_idempotency_key(idempotency_key):
        return None
    uow = uow_factory()
    try:
        return uow.find_durable_submission_receipt(owner_id, idempotency_key)
    finally:
        try:
            uow.rollback()
        finally:
            uow.close()


def accept_durable_snapshot_for_recovery(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    submission_id: str,
    owner_id: str,
    *,
    crypto: CredentialCrypto,
    proof_authority: HmacProofAuthority,
) -> DurableSubmissionResponse:
    """Accept only the persisted snapshot and reserved ids after a restart."""
    return _accept_snapshot_if_ready(
        uow_factory,
        submission_id,
        owner_id,
        crypto=crypto,
        proof_authority=proof_authority,
    )


def public_submission_receipt(row: Mapping[str, object]) -> dict[str, object]:
    stage = row.get("stage")
    status = {
        "reserved": "charged_incomplete",
        "fee_committed": "charged_incomplete",
        "snapshot_ready": "charged_incomplete",
        "accepted": "accepted",
        "rejected_after_charge": "rejected_after_charge",
        "charged_incomplete": "charged_incomplete",
        "dispatching": "provider_result_unknown",
        "provider_result_unknown": "provider_result_unknown",
        "result_known": "result_known",
        "media_started": "media_result_unknown",
        "media_ack_unknown": "media_result_unknown",
        "completed": "completed",
        "failed": "failed",
    }.get(stage, "charged_incomplete")
    response = _json_mapping(row.get("response_json"))
    receipt: dict[str, object] = {
        "status": status,
        "fee_disposition": row.get("fee_disposition"),
        "fee_amount": row.get("fee_amount"),
        "task_id": row.get("task_id"),
        "message_id": row.get("message_id"),
    }
    if status == "rejected_after_charge":
        receipt["response_status"] = row.get("response_status")
        receipt["response"] = response
    return receipt


def load_durable_prepared_snapshot(
    row: Mapping[str, object],
    *,
    crypto: CredentialCrypto,
    proof_authority: HmacProofAuthority,
) -> PreparedFusedGeneration | None:
    binding = _binding_for_row(row)
    if binding is None:
        return None
    submission_id = row.get("submission_id")
    owner_id = row.get("owner_id")
    task_id = row.get("task_id")
    digest = row.get("request_digest")
    if any(type(value) is not str or not value for value in (submission_id, owner_id, task_id, digest)):
        return None
    document = open_json_value(
        row.get("encrypted_snapshot"),
        row.get("snapshot_nonce"),
        row.get("seal_key_id"),
        row.get("crypto_algorithm"),
        crypto=crypto,
        submission_id=submission_id,
        owner_id=owner_id,
        request_digest=digest,
        task_id=task_id,
        binding=binding,
        purpose="fused-final-snapshot-v1",
    )
    if document is None or not _verify_snapshot(
        row, json_digest(document), binding, proof_authority
    ):
        return None
    prepared = prepared_from_document(document)
    if (
        prepared is None
        or prepared.submission_id != submission_id
        or prepared.user_id != owner_id
        or prepared.task_id != task_id
        or prepared.request_digest != digest
        or prepared.credential_binding != binding
    ):
        return None
    return prepared


def load_durable_provider_result(
    row: Mapping[str, object],
    *,
    crypto: CredentialCrypto,
) -> object | None:
    binding = _binding_for_row(row)
    if binding is None:
        return None
    try:
        value = open_json_value(
            row.get("encrypted_result"),
            row.get("result_nonce"),
            row.get("seal_key_id"),
            row.get("crypto_algorithm"),
            crypto=crypto,
            submission_id=str(row["submission_id"]),
            owner_id=str(row["owner_id"]),
            request_digest=str(row["request_digest"]),
            task_id=str(row["task_id"]),
            binding=binding,
            purpose="fused-provider-result-v1",
        )
        return value
    except (KeyError, TypeError, ValueError):
        return None


def _existing_submission_response(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    row: Mapping[str, object],
    *,
    request_digest: str,
    crypto: CredentialCrypto,
    proof_authority: HmacProofAuthority,
) -> DurableSubmissionResponse:
    if row.get("request_digest") != request_digest:
        raise FusedGenerationConflict("相同 Idempotency-Key 已用于不同的融合请求")
    if row.get("stage") == "snapshot_ready":
        return _accept_snapshot_if_ready(
            uow_factory,
            str(row["submission_id"]),
            str(row["owner_id"]),
            crypto=crypto,
            proof_authority=proof_authority,
        )
    if row.get("stage") == "rejected_after_charge":
        return DurableSubmissionResponse(
            int(row.get("response_status") or 400),
            _json_mapping(row.get("response_json")),
        )
    if row.get("stage") in {"fee_committed", "reserved", "charged_incomplete"}:
        return _charged_incomplete(row)
    body = _json_mapping(row.get("response_json")) or {
        "task_id": row.get("task_id"),
        "message_id": row.get("message_id"),
    }
    return DurableSubmissionResponse(
        int(row.get("response_status") or 200),
        body,
        execute_submission=row.get("stage") in {"accepted", "result_known"},
    )


def _accept_snapshot_if_ready(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    submission_id: str,
    owner_id: str,
    *,
    crypto: CredentialCrypto,
    proof_authority: HmacProofAuthority,
) -> DurableSubmissionResponse:
    row = _lookup_submission_by_id(uow_factory, submission_id)
    if row is None or row.get("owner_id") != owner_id:
        return _charged_incomplete(row)
    if row.get("stage") != "snapshot_ready":
        return _response_for_row(row)
    binding = _binding_for_row(row)
    if binding is None:
        return _charged_incomplete(row)
    document = open_json_value(
        row.get("encrypted_snapshot"),
        row.get("snapshot_nonce"),
        row.get("seal_key_id"),
        row.get("crypto_algorithm"),
        crypto=crypto,
        submission_id=submission_id,
        owner_id=owner_id,
        request_digest=str(row.get("request_digest")),
        task_id=str(row.get("task_id")),
        binding=binding,
        purpose="fused-final-snapshot-v1",
    )
    if document is None:
        return _charged_incomplete(row)
    digest = json_digest(document)
    if not _verify_snapshot(row, digest, binding, proof_authority):
        return _charged_incomplete(row)
    prepared = prepared_from_document(document)
    if prepared is None or prepared.user_id != owner_id or prepared.submission_id != submission_id:
        return _charged_incomplete(row)
    body = {"task_id": prepared.task_id, "message_id": prepared.message_id}
    response_json = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    uow = uow_factory()
    try:
        accepted = uow.accept_durable_submission(
            prepared,
            response_status=200,
            response_json=response_json,
        )
        if not accepted:
            uow.rollback()
            return _charged_incomplete(row)
        uow.commit()
    except Exception:
        uow.rollback()
        confirmed = _lookup_submission_by_id(uow_factory, submission_id)
        if confirmed is not None and confirmed.get("stage") == "accepted":
            return _response_for_row(confirmed, execute=True)
        return _charged_incomplete(confirmed or row)
    finally:
        uow.close()
    return DurableSubmissionResponse(200, body, execute_submission=True)


def _verify_snapshot(
    row: Mapping[str, object],
    digest: str,
    binding: CredentialBinding,
    authority: HmacProofAuthority,
) -> bool:
    return (
        row.get("snapshot_proof_key_id") == authority.key_id
        and authority.verify(
            "fused-generation-snapshot-v1",
            snapshot_proof_payload(
                digest,
                submission_id=str(row.get("submission_id")),
                owner_id=str(row.get("owner_id")),
                request_digest=str(row.get("request_digest")),
                task_id=str(row.get("task_id")),
                binding=binding,
            ),
            str(row.get("snapshot_proof")),
        )
    )


def _binding_for_row(row: Mapping[str, object]) -> CredentialBinding | None:
    paths = row.get("allowed_content_paths")
    try:
        parsed_paths = json.loads(paths) if isinstance(paths, str) else None
        if not isinstance(parsed_paths, list) or any(type(path) is not str for path in parsed_paths):
            return None
        return CredentialBinding(
            source=str(row["model_source"]),
            row_id=str(row["model_row_id"]),
            owner_or_system=("system" if row["model_source"] == "system" else str(row["owner_id"])),
            provider=str(row["provider"]),
            original_principal_id=str(row["principal_id"]),
            endpoint_origin=str(row["endpoint_origin"]),
            allowed_content_paths=tuple(parsed_paths),
            binding_version=str(row["binding_version"]),
            rotation_provenance=(
                str(row["rotation_provenance"])
                if row.get("rotation_provenance") is not None
                else None
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _lookup_submission(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    owner_id: str,
    idempotency_key: str,
) -> Mapping[str, object] | None:
    uow = uow_factory()
    try:
        return uow.find_durable_submission(owner_id, idempotency_key)
    finally:
        try:
            uow.rollback()
        finally:
            uow.close()


def _lookup_submission_by_id(
    uow_factory: FusedGenerationUnitOfWorkFactory,
    submission_id: str,
) -> Mapping[str, object] | None:
    uow = uow_factory()
    try:
        return uow.durable_submission_by_id(submission_id)
    finally:
        try:
            uow.rollback()
        finally:
            uow.close()


def _response_for_row(
    row: Mapping[str, object],
    *,
    execute: bool = False,
) -> DurableSubmissionResponse:
    if row.get("stage") == "rejected_after_charge":
        return DurableSubmissionResponse(
            int(row.get("response_status") or 400),
            _json_mapping(row.get("response_json")),
        )
    if row.get("stage") == "accepted":
        return DurableSubmissionResponse(
            int(row.get("response_status") or 200),
            _json_mapping(row.get("response_json")) or {
                "task_id": row.get("task_id"),
                "message_id": row.get("message_id"),
            },
            execute_submission=execute,
        )
    return _charged_incomplete(row)


def _charged_incomplete(
    row: Mapping[str, object] | None = None,
) -> DurableSubmissionResponse:
    body: dict[str, object] = {"status": "charged_incomplete"}
    if row is not None:
        body["fee_disposition"] = row.get("fee_disposition")
        body["fee_amount"] = row.get("fee_amount")
    return DurableSubmissionResponse(202, body)


def _json_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed, Mapping):
        return {}
    return dict(parsed)


def _valid_idempotency_key(value: object) -> bool:
    return (
        type(value) is str
        and bool(value)
        and len(value) <= 255
        and value == value.strip()
        and not any(ord(character) < 0x20 or ord(character) == 0x7f for character in value)
    )
