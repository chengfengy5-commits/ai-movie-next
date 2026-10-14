"""Owned short-session lifecycle around shared media staging and receipt lookup."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TypeAlias

from sqlalchemy.orm import Session

from haoai_backend.generation_core.admission_receipts import (
    AdmissionCommitUnknown,
    AdmissionConfirmed,
    AdmissionReadResult,
    AdmissionStaged,
    lookup_staged_admission,
    lookup_unknown_commit,
    retain_unknown_commit,
)
from haoai_backend.generation_core.admission_staging import (
    AdmissionStageRefusal,
    MediaSnapshotProducerBinding,
    prepare_and_stage,
)
from haoai_backend.generation_catalog.admission_types import AdmissionRefusal

SessionFactory = Callable[[], Session]
ChatMediaStageResult: TypeAlias = (
    AdmissionConfirmed
    | AdmissionStaged
    | AdmissionCommitUnknown
    | AdmissionRefusal
    | AdmissionStageRefusal
)


def stage_admission(
    session_factory: SessionFactory,
    binding: MediaSnapshotProducerBinding,
    *,
    authorization: str,
    operation: str,
    raw_key: str,
    request: Mapping[str, object],
) -> ChatMediaStageResult:
    """Stage with the actual producer and commit once in one owned Session."""

    session = session_factory()
    try:
        session.begin()
        result = prepare_and_stage(
            binding,
            session,
            authorization=authorization,
            operation=operation,
            raw_key=raw_key,
            request=request,
        )
        if isinstance(result, AdmissionStaged):
            try:
                session.commit()
            except Exception:
                try:
                    session.rollback()
                except Exception:
                    pass
                return retain_unknown_commit(result)
            return result

        try:
            session.rollback()
        except Exception:
            if isinstance(result, (AdmissionRefusal, AdmissionStageRefusal)):
                return AdmissionStageRefusal(
                    status_code=503,
                    code="admission_stage_failed",
                    message="准入结果暂时无法安全完成",
                    caller_must_abort=False,
                )
            raise
        return result
    except Exception:
        try:
            session.rollback()
        except Exception:
            pass
        raise
    finally:
        session.close()


def lookup_committed_receipt(
    session_factory: SessionFactory,
    staged: AdmissionStaged | AdmissionCommitUnknown,
) -> AdmissionReadResult:
    """Read through a distinct fresh Session after the writer has closed."""

    session = session_factory()
    try:
        if isinstance(staged, AdmissionCommitUnknown):
            return lookup_unknown_commit(session, staged)
        return lookup_staged_admission(session, staged)
    finally:
        session.close()
