-- Explicit, incremental migration for the eight canonical generation records.
-- Existing graph, quote, task, evidence, wallet and ledger tables are prerequisites.

CREATE TABLE stream_fee_records (
    server_attempt_id VARCHAR(36) NOT NULL PRIMARY KEY,
    owner_id VARCHAR(36) NOT NULL REFERENCES users(id),
    operation VARCHAR(32) NOT NULL,
    frozen_model_provenance TEXT NOT NULL,
    quoted_amount INTEGER NOT NULL,
    disposition VARCHAR(16) NOT NULL,
    actual_debited_amount INTEGER NOT NULL,
    original_debit_log_id VARCHAR(36) NULL REFERENCES credit_logs(id),
    original_business_key VARCHAR(255) NULL,
    settlement_state VARCHAR(32) NOT NULL,
    start_state VARCHAR(24) NOT NULL,
    start_token VARCHAR(128) NULL,
    task_id VARCHAR(36) NULL REFERENCES ai_tasks(id),
    message_id VARCHAR(36) NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    CONSTRAINT ck_stream_fee_records_operation CHECK (operation = 'text.stream'),
    CONSTRAINT ck_stream_fee_records_quote_nonnegative CHECK (quoted_amount >= 0),
    CONSTRAINT ck_stream_fee_records_debit_bounds CHECK (actual_debited_amount >= 0 AND actual_debited_amount <= quoted_amount),
    CONSTRAINT ck_stream_fee_records_disposition CHECK (disposition IN ('debit', 'skipped', 'byok', 'zero')),
    CONSTRAINT ck_stream_fee_records_settlement_state CHECK (settlement_state IN ('committed', 'success_retained', 'settlement_pending', 'refunded', 'no_debit_terminal')),
    CONSTRAINT ck_stream_fee_records_start_state CHECK (start_state IN ('ready_to_start', 'start_reserved', 'start_exposed', 'revoked')),
    CONSTRAINT ck_stream_fee_records_original_debit_scope CHECK (
        (actual_debited_amount = 0 AND original_debit_log_id IS NULL AND original_business_key IS NULL)
        OR (actual_debited_amount > 0 AND original_debit_log_id IS NOT NULL AND original_business_key IS NOT NULL)
    )
);

CREATE TABLE generation_result_receipts (
    result_ref VARCHAR(36) NOT NULL PRIMARY KEY,
    owner_id VARCHAR(36) NOT NULL REFERENCES users(id),
    task_id VARCHAR(36) NOT NULL REFERENCES ai_tasks(id),
    unit_id VARCHAR(36) NOT NULL REFERENCES billing_units(id),
    operation VARCHAR(64) NOT NULL,
    step_id VARCHAR(36) NOT NULL REFERENCES execution_steps(id),
    submission_id VARCHAR(36) NOT NULL REFERENCES external_submissions(id),
    evidence_id VARCHAR(36) NOT NULL REFERENCES result_evidence(id),
    selected_attempt_id VARCHAR(255) NOT NULL,
    recoverable_locator TEXT NOT NULL,
    content_digest VARCHAR(128) NOT NULL,
    validation_version VARCHAR(128) NOT NULL,
    validated_at TIMESTAMP NOT NULL,
    generation_fulfilled_at TIMESTAMP NOT NULL,
    late_disposition VARCHAR(64) NULL,
    policy_stop_disposition VARCHAR(64) NULL,
    automatic_application_allowed BOOLEAN NOT NULL
);

CREATE TABLE application_records (
    application_ref VARCHAR(36) NOT NULL PRIMARY KEY,
    result_ref VARCHAR(36) NOT NULL REFERENCES generation_result_receipts(result_ref),
    owner_id VARCHAR(36) NOT NULL REFERENCES users(id),
    task_id VARCHAR(36) NOT NULL REFERENCES ai_tasks(id),
    unit_id VARCHAR(36) NOT NULL REFERENCES billing_units(id),
    target_scope VARCHAR(255) NOT NULL,
    result_digest VARCHAR(128) NOT NULL,
    delivery_status VARCHAR(32) NOT NULL,
    before_target_digest VARCHAR(128) NULL,
    after_target_digest VARCHAR(128) NULL,
    media_revision_before INTEGER NULL,
    media_revision_after INTEGER NULL,
    notes_revision_before INTEGER NULL,
    notes_revision_after INTEGER NULL,
    committed_at TIMESTAMP NULL,
    CONSTRAINT uq_application_result_target UNIQUE (result_ref, target_scope),
    CONSTRAINT ck_application_records_delivery_status CHECK (delivery_status IN ('pending', 'applied', 'target_conflict', 'target_deleted', 'failed'))
);

CREATE TABLE unit_fee_decisions (
    unit_id VARCHAR(36) NOT NULL PRIMARY KEY REFERENCES billing_units(id),
    charge_phase VARCHAR(32) NOT NULL,
    resolution VARCHAR(32) NOT NULL,
    ledger_ref VARCHAR(36) NULL REFERENCES credit_logs(id),
    generation_result_ref VARCHAR(36) NULL REFERENCES generation_result_receipts(result_ref),
    application_ref VARCHAR(36) NULL REFERENCES application_records(application_ref),
    resolved_at TIMESTAMP NULL,
    CONSTRAINT ck_unit_fee_decisions_charge_phase CHECK (charge_phase IN ('stream_pre_task', 'single_admission', 'batch_admission', 'post_apply', 'pre_apply')),
    CONSTRAINT ck_unit_fee_decisions_resolution CHECK (resolution IN ('pending', 'charged', 'no_charge', 'waived_insufficient', 'waived_charge_failed', 'waived_cancelled', 'waived_delivery_failed', 'stopped_insufficient', 'stopped_late_generation'))
);

CREATE TABLE execution_attempt_groups (
    unit_id VARCHAR(36) NOT NULL REFERENCES billing_units(id),
    group_key VARCHAR(128) NOT NULL,
    registry_version VARCHAR(128) NOT NULL,
    attempt_step_ids JSON NOT NULL,
    original_payload_digest VARCHAR(128) NOT NULL,
    max_retries INTEGER NOT NULL,
    active_attempt INTEGER NOT NULL,
    selected_evidence_id VARCHAR(36) NULL REFERENCES result_evidence(id),
    status VARCHAR(32) NOT NULL,
    poll_used INTEGER NOT NULL DEFAULT 0,
    auth_unavailable_streak INTEGER NOT NULL DEFAULT 0,
    terminal_reason VARCHAR(128) NULL,
    poll_sequence INTEGER NOT NULL DEFAULT 0,
    next_poll_at TIMESTAMP NULL,
    outstanding_reservation_ref VARCHAR(36) NULL,
    sticky_policy_stop_event_ref VARCHAR(36) NULL,
    active_eligibility_version VARCHAR(128) NOT NULL,
    PRIMARY KEY (unit_id, group_key),
    CONSTRAINT ck_attempt_groups_max_retries CHECK (max_retries = 2),
    CONSTRAINT ck_attempt_groups_poll_used_nonnegative CHECK (poll_used >= 0),
    CONSTRAINT ck_attempt_groups_auth_streak_nonnegative CHECK (auth_unavailable_streak >= 0),
    CONSTRAINT ck_attempt_groups_poll_sequence_nonnegative CHECK (poll_sequence >= 0),
    CONSTRAINT ck_attempt_groups_status CHECK (status IN ('prepared', 'active', 'pending_verification', 'succeeded', 'failed', 'cancelled', 'policy_stopped', 'succeeded_late'))
);

CREATE TABLE poll_reservations (
    reservation_id VARCHAR(36) NOT NULL PRIMARY KEY,
    unit_id VARCHAR(36) NOT NULL,
    group_key VARCHAR(128) NOT NULL,
    sequence INTEGER NOT NULL,
    attempt_id VARCHAR(36) NOT NULL,
    step_id VARCHAR(36) NOT NULL REFERENCES execution_steps(id),
    submission_id VARCHAR(36) NOT NULL REFERENCES external_submissions(id),
    external_id VARCHAR(255) NOT NULL,
    payload_digest VARCHAR(128) NOT NULL,
    committed_poll_count INTEGER NOT NULL,
    reserved_at TIMESTAMP NOT NULL,
    timeout_at TIMESTAMP NOT NULL,
    next_poll_at TIMESTAMP NOT NULL,
    token VARCHAR(128) NOT NULL,
    state VARCHAR(24) NOT NULL,
    observation_ref VARCHAR(36) NULL,
    CONSTRAINT uq_poll_reservations_group_sequence UNIQUE (unit_id, group_key, sequence),
    CONSTRAINT fk_poll_reservations_attempt_group FOREIGN KEY (unit_id, group_key) REFERENCES execution_attempt_groups(unit_id, group_key),
    CONSTRAINT ck_poll_reservations_sequence_positive CHECK (sequence > 0),
    CONSTRAINT ck_poll_reservations_count_positive CHECK (committed_poll_count > 0),
    CONSTRAINT ck_poll_reservations_state CHECK (state IN ('reserved', 'accepted', 'closed_unknown'))
);

CREATE TABLE policy_stop_events (
    event_id VARCHAR(36) NOT NULL PRIMARY KEY,
    unit_id VARCHAR(36) NOT NULL,
    group_key VARCHAR(128) NOT NULL,
    policy_version VARCHAR(128) NOT NULL,
    reason VARCHAR(64) NOT NULL,
    reservation_refs JSON NOT NULL,
    observation_refs JSON NOT NULL,
    active_step_id VARCHAR(36) NOT NULL REFERENCES execution_steps(id),
    active_submission_id VARCHAR(36) NOT NULL REFERENCES external_submissions(id),
    external_id VARCHAR(255) NOT NULL,
    stopped_at TIMESTAMP NOT NULL,
    financial_disposition VARCHAR(32) NOT NULL,
    original_debit_ref VARCHAR(36) NULL REFERENCES credit_logs(id),
    refund_ref VARCHAR(36) NULL REFERENCES credit_logs(id),
    amount INTEGER NOT NULL,
    owner_id VARCHAR(36) NOT NULL REFERENCES users(id),
    task_id VARCHAR(36) NOT NULL REFERENCES ai_tasks(id),
    CONSTRAINT uq_policy_stop_events_group_policy_reason UNIQUE (unit_id, group_key, policy_version, reason),
    CONSTRAINT fk_policy_stop_events_attempt_group FOREIGN KEY (unit_id, group_key) REFERENCES execution_attempt_groups(unit_id, group_key),
    CONSTRAINT ck_policy_stop_events_reason CHECK (reason = 'auth_unavailable_12'),
    CONSTRAINT ck_policy_stop_events_financial_disposition CHECK (financial_disposition IN ('pending', 'refunded', 'retained_by_started_cancel', 'no_debit')),
    CONSTRAINT ck_policy_stop_events_amount_nonnegative CHECK (amount >= 0)
);

CREATE TABLE local_continuation_work (
    work_id VARCHAR(36) NOT NULL PRIMARY KEY,
    owner_id VARCHAR(36) NOT NULL REFERENCES users(id),
    task_id VARCHAR(36) NOT NULL REFERENCES ai_tasks(id),
    unit_id VARCHAR(36) NOT NULL REFERENCES billing_units(id),
    operation VARCHAR(64) NOT NULL,
    policy_version VARCHAR(128) NOT NULL,
    result_ref VARCHAR(36) NOT NULL REFERENCES generation_result_receipts(result_ref),
    content_digest VARCHAR(128) NOT NULL,
    generation_fulfilled_at TIMESTAMP NOT NULL,
    original_first_intent_at TIMESTAMP NOT NULL,
    original_deadline_at TIMESTAMP NOT NULL,
    phase_order JSON NOT NULL,
    phase VARCHAR(32) NOT NULL,
    phase_version INTEGER NOT NULL,
    fee_decision_ref VARCHAR(36) NULL REFERENCES unit_fee_decisions(unit_id),
    application_ref VARCHAR(36) NULL REFERENCES application_records(application_ref),
    terminal_reason VARCHAR(128) NULL,
    last_execution_generation INTEGER NULL,
    CONSTRAINT uq_continuation_work_task_unit_policy UNIQUE (task_id, unit_id, policy_version),
    CONSTRAINT ck_continuation_work_phase CHECK (phase IN ('delivery_pending', 'fee_pending', 'finalize_pending', 'done', 'stopped_cancelled', 'stopped_insufficient', 'stopped_delivery_failed', 'stopped_late_generation')),
    CONSTRAINT ck_continuation_work_phase_version_nonnegative CHECK (phase_version >= 0),
    CONSTRAINT ck_continuation_work_generation_nonnegative CHECK (last_execution_generation IS NULL OR last_execution_generation >= 0)
);
