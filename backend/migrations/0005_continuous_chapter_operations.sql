CREATE TABLE continuous_chapter_operation_schema_migrations (
    version VARCHAR(64) PRIMARY KEY,
    checksum VARCHAR(64) NOT NULL,
    applied_at TIMESTAMP NOT NULL
);

CREATE TABLE continuous_chapter_operation_receipts (
    owner_id VARCHAR(36) NOT NULL,
    operation_id VARCHAR(36) NOT NULL,
    receipt_version SMALLINT NOT NULL,
    editor_session_id VARCHAR(36) NOT NULL,
    series_id VARCHAR(36) NOT NULL,
    chapter_id VARCHAR(36) NOT NULL,
    command_kind VARCHAR(32) NOT NULL,
    request_body_sha256 VARCHAR(64) NOT NULL,
    source_snapshot_digest VARCHAR(64) NOT NULL,
    target_draft_id TEXT,
    target_manifest_digest VARCHAR(64),
    outcome VARCHAR(16) NOT NULL,
    http_status SMALLINT NOT NULL,
    result_identity TEXT NOT NULL,
    canonical_response TEXT NOT NULL,
    result_digest VARCHAR(64) NOT NULL,
    committed_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_continuous_chapter_operation_receipts
        PRIMARY KEY (owner_id, operation_id),
    CONSTRAINT ck_continuous_chapter_operation_receipt_version
        CHECK (receipt_version = 1),
    CONSTRAINT ck_continuous_chapter_operation_outcome
        CHECK (outcome IN ('committed', 'rejected')),
    CONSTRAINT ck_continuous_chapter_operation_http_status
        CHECK (http_status >= 200 AND http_status < 500)
);

CREATE INDEX ix_continuous_chapter_operation_target
    ON continuous_chapter_operation_receipts (owner_id, series_id, chapter_id, created_at);
