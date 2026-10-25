CREATE TABLE generation_result_targets (
    result_ref VARCHAR(36) NOT NULL PRIMARY KEY,
    owner_id VARCHAR(36) NOT NULL,
    task_id VARCHAR(36) NOT NULL,
    series_id VARCHAR(36) NOT NULL,
    chapter_id VARCHAR(36) NOT NULL,
    target_kind VARCHAR(64) NOT NULL,
    target_identity VARCHAR(255) NOT NULL,
    target_snapshot_digest VARCHAR(64) NOT NULL,
    generation_fulfilled_at TIMESTAMP NOT NULL,
    CONSTRAINT fk_generation_result_targets_result_ref_generation_result_receipts
        FOREIGN KEY (result_ref) REFERENCES generation_result_receipts(result_ref),
    CONSTRAINT fk_generation_result_targets_owner_id_users
        FOREIGN KEY (owner_id) REFERENCES users(id),
    CONSTRAINT fk_generation_result_targets_task_id_ai_tasks
        FOREIGN KEY (task_id) REFERENCES ai_tasks(id),
    CONSTRAINT ck_generation_result_targets_kind
        CHECK (target_kind IN ('canvas_frame_preview', 'canvas_asset_image'))
);

CREATE INDEX ix_generation_result_targets_page
    ON generation_result_targets (
        owner_id,
        chapter_id,
        target_kind,
        target_identity,
        generation_fulfilled_at,
        result_ref
    );

CREATE INDEX ix_generation_result_receipts_owner_result_ref
    ON generation_result_receipts (owner_id, result_ref);
