CREATE TABLE fused_generation_submissions (
    submission_id VARCHAR(36) NOT NULL PRIMARY KEY,
    owner_id VARCHAR(36) NOT NULL,
    operation VARCHAR(32) NOT NULL,
    idempotency_key VARCHAR(255) NOT NULL,
    request_digest VARCHAR(64) NOT NULL,
    stage VARCHAR(32) NOT NULL,
    fee_disposition VARCHAR(16) NOT NULL,
    fee_amount INTEGER NOT NULL,
    debit_log_id VARCHAR(36),
    task_id VARCHAR(36),
    message_id VARCHAR(36),
    encrypted_request TEXT,
    request_nonce VARCHAR(128),
    encrypted_snapshot TEXT,
    snapshot_nonce VARCHAR(128),
    encrypted_result TEXT,
    result_nonce VARCHAR(128),
    result_kind VARCHAR(16),
    result_digest VARCHAR(64),
    seal_key_id VARCHAR(128),
    crypto_algorithm VARCHAR(64),
    model_source VARCHAR(16),
    model_row_id VARCHAR(36),
    provider VARCHAR(64),
    principal_id VARCHAR(36),
    binding_id VARCHAR(36),
    binding_version VARCHAR(16),
    endpoint_origin VARCHAR(512),
    allowed_content_paths TEXT,
    rotation_provenance VARCHAR(255),
    snapshot_proof_key_id VARCHAR(128),
    snapshot_proof VARCHAR(128),
    response_status INTEGER,
    response_json TEXT,
    dispatch_nonce VARCHAR(36),
    dispatch_owner VARCHAR(64),
    dispatch_generation INTEGER,
    wire_digest VARCHAR(64),
    asset_id VARCHAR(36),
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    CONSTRAINT fk_fused_submissions_owner_id_users
        FOREIGN KEY (owner_id) REFERENCES users(id),
    CONSTRAINT fk_fused_submissions_debit_log_id_credit_logs
        FOREIGN KEY (debit_log_id) REFERENCES credit_logs(id)
        DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT uq_fused_submissions_owner_key
        UNIQUE (owner_id, idempotency_key),
    CONSTRAINT uq_fused_submissions_task_id
        UNIQUE (task_id),
    CONSTRAINT ck_fused_submissions_operation
        CHECK (operation = 'fused.image'),
    CONSTRAINT ck_fused_submissions_stage
        CHECK (stage IN (
            'reserved', 'fee_committed', 'snapshot_ready', 'accepted',
            'rejected_after_charge', 'charged_incomplete', 'dispatching',
            'provider_result_unknown', 'result_known', 'media_ack_unknown',
            'media_started', 'completed', 'failed'
        )),
    CONSTRAINT ck_fused_submissions_fee_disposition
        CHECK (fee_disposition IN ('debit', 'byok', 'zero')),
    CONSTRAINT ck_fused_submissions_fee_facts
        CHECK (fee_amount >= 0 AND (
            (fee_disposition = 'debit' AND fee_amount > 0 AND debit_log_id IS NOT NULL)
            OR (fee_disposition IN ('byok', 'zero') AND fee_amount = 0 AND debit_log_id IS NULL)
        )),
    CONSTRAINT ck_fused_submissions_request_digest
        CHECK (length(request_digest) = 64)
);

CREATE INDEX ix_fused_submissions_recovery
    ON fused_generation_submissions (stage, updated_at, submission_id);
