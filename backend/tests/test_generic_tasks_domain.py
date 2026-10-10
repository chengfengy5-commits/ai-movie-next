from __future__ import annotations

from dataclasses import FrozenInstanceError, fields

import pytest

from haoai_backend.generic_tasks.domain import (
    CreditLedgerEntry,
    CreditWallet,
    GenericTaskDraft,
    GenericTaskRecord,
    TaskSubmissionRecord,
)
from haoai_backend.generic_tasks.guard import CONTROLLED_TASK_TYPES
from generic_tasks_support import NOW


def test_domain_values_preserve_legacy_nullable_and_empty_values() -> None:
    record = GenericTaskRecord(
        id="g-task",
        user_id="user-a",
        type="image",
        status="processing",
        credit_cost=0,
        message_id="",
        request_data="{}",
        result=None,
        progress=0,
        progress_message="",
        external_task_id=None,
        external_provider="",
        model_name=None,
        created_at=NOW,
        updated_at=NOW,
    )

    assert record.result is None
    assert record.progress_message == ""
    assert record.external_task_id is None
    assert record.external_provider == ""
    with pytest.raises(FrozenInstanceError):
        record.status = "failed"  # type: ignore[misc]


def test_domain_ports_keep_task_and_ledger_values_explicit() -> None:
    draft = GenericTaskDraft(
        id="g-draft",
        user_id="user-a",
        type="image",
        status="processing",
        credit_cost=-1,
        message_id="",
        request_data="{}",
        model_name=None,
        created_at=NOW,
        updated_at=NOW,
    )
    wallet = CreditWallet(id="wallet-a", user_id="user-a", credits=12)
    entry = CreditLedgerEntry(
        id="ledger-a",
        user_id="user-a",
        task_id="g-draft",
        type="usage",
        amount=-2,
        business_key="charge:g-draft",
    )
    submission = TaskSubmissionRecord(id="s-a", user_id="user-a", task_ids=[])

    assert draft.credit_cost == -1
    assert (wallet.credits, entry.amount, entry.related_debit_id) == (12, -2, None)
    assert submission.task_ids == []
    assert [field.name for field in fields(GenericTaskRecord)] == [
        "id", "user_id", "type", "status", "credit_cost", "message_id",
        "request_data", "result", "progress", "progress_message",
        "external_task_id", "external_provider", "model_name", "created_at",
        "updated_at",
    ]


def test_exact_controlled_task_type_set_is_kept() -> None:
    assert CONTROLLED_TASK_TYPES == {
        "batch-optimize",
        "batch-image",
        "image-single",
        "video-single",
        "ai-review",
    }
