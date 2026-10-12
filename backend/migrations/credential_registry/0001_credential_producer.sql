CREATE TABLE generation_credential_principals (
    principal_id VARCHAR(36) PRIMARY KEY,
    provider VARCHAR(64) NOT NULL,
    issuer_namespace VARCHAR(255) NOT NULL,
    stable_account_subject VARCHAR(255) NOT NULL,
    owner_scope VARCHAR(128) NOT NULL,
    record_id VARCHAR(255) NOT NULL,
    evidence_ref VARCHAR(255) NOT NULL,
    evidence_version VARCHAR(128) NOT NULL,
    source_state VARCHAR(16) NOT NULL,
    created_by VARCHAR(36) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_generation_credential_principal_identity UNIQUE (provider, issuer_namespace, stable_account_subject, owner_scope),
    CONSTRAINT uq_generation_credential_principal_scope UNIQUE (principal_id, provider, owner_scope),
    CONSTRAINT ck_generation_credential_principal_state CHECK (source_state IN ('valid', 'revoked'))
);

CREATE TABLE generation_credential_bindings (
    binding_id VARCHAR(36) PRIMARY KEY,
    source VARCHAR(16) NOT NULL,
    row_id VARCHAR(36) NOT NULL,
    owner_or_system VARCHAR(128) NOT NULL,
    provider VARCHAR(64) NOT NULL,
    revision INTEGER NOT NULL,
    current_version INTEGER NOT NULL,
    enabled BOOLEAN NOT NULL,
    created_by VARCHAR(36) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_generation_credential_binding_scope UNIQUE (source, row_id, owner_or_system, provider),
    CONSTRAINT uq_generation_credential_binding_full_scope UNIQUE (binding_id, source, row_id, owner_or_system, provider),
    CONSTRAINT ck_generation_credential_binding_source CHECK (source IN ('system', 'user')),
    CONSTRAINT ck_generation_credential_binding_revision CHECK (revision > 0),
    CONSTRAINT ck_generation_credential_binding_head_positive CHECK (current_version > 0)
);

CREATE TABLE generation_credential_versions (
    binding_id VARCHAR(36) NOT NULL,
    binding_version INTEGER NOT NULL,
    source VARCHAR(16) NOT NULL,
    row_id VARCHAR(36) NOT NULL,
    owner_or_system VARCHAR(128) NOT NULL,
    provider VARCHAR(64) NOT NULL,
    principal_id VARCHAR(36) NOT NULL,
    issuer_namespace VARCHAR(255) NOT NULL,
    stable_account_subject VARCHAR(255) NOT NULL,
    key_generation VARCHAR(128) NOT NULL,
    attempt_key_generation VARCHAR(128) NULL,
    record_id VARCHAR(255) NOT NULL,
    protected_record_id VARCHAR(255) NULL,
    evidence_ref VARCHAR(255) NOT NULL,
    evidence_version VARCHAR(128) NOT NULL,
    endpoint_origin VARCHAR(512) NOT NULL,
    api_base_path VARCHAR(1024) NOT NULL,
    allowed_content_paths TEXT NOT NULL,
    endpoint_policy_version VARCHAR(128) NOT NULL,
    version_kind VARCHAR(16) NOT NULL,
    predecessor_version INTEGER NULL,
    expected_revision INTEGER NULL,
    rotation_proof_ref VARCHAR(255) NULL,
    ciphertext BYTEA NULL,
    nonce BYTEA NULL,
    seal_key_id VARCHAR(128) NULL,
    seal_algorithm_version VARCHAR(128) NULL,
    guard_key_id VARCHAR(128) NULL,
    secret_consistency_guard VARCHAR(64) NULL,
    server_attempt_id VARCHAR(128) NOT NULL,
    actor_id VARCHAR(36) NOT NULL,
    operation VARCHAR(16) NOT NULL,
    proof_key_id VARCHAR(128) NOT NULL,
    command_proof VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (binding_id, binding_version),
    CONSTRAINT uq_generation_credential_version_full_scope UNIQUE (binding_id, binding_version, principal_id, source, row_id, owner_or_system, provider),
    CONSTRAINT uq_generation_credential_version_attempt UNIQUE (server_attempt_id),
    CONSTRAINT fk_generation_credential_version_binding_scope FOREIGN KEY (binding_id, source, row_id, owner_or_system, provider)
        REFERENCES generation_credential_bindings (binding_id, source, row_id, owner_or_system, provider) ON DELETE RESTRICT,
    CONSTRAINT fk_generation_credential_version_principal_scope FOREIGN KEY (principal_id, provider, owner_or_system)
        REFERENCES generation_credential_principals (principal_id, provider, owner_scope) ON DELETE RESTRICT,
    CONSTRAINT fk_generation_credential_version_predecessor FOREIGN KEY (binding_id, predecessor_version)
        REFERENCES generation_credential_versions (binding_id, binding_version) ON DELETE RESTRICT,
    CONSTRAINT ck_generation_credential_version_positive CHECK (binding_version > 0),
    CONSTRAINT ck_generation_credential_version_predecessor_earlier CHECK (predecessor_version IS NULL OR predecessor_version < binding_version),
    CONSTRAINT ck_generation_credential_version_kind CHECK (version_kind IN ('registered', 'rotation', 'replacement', 'disabled')),
    CONSTRAINT ck_generation_credential_version_operation CHECK (operation IN ('register', 'rotate', 'replace', 'disable')),
    CONSTRAINT ck_generation_credential_version_material CHECK (
        (version_kind = 'disabled' AND ciphertext IS NULL AND nonce IS NULL
            AND seal_key_id IS NULL AND seal_algorithm_version IS NULL
            AND guard_key_id IS NULL AND secret_consistency_guard IS NULL)
        OR
        (version_kind <> 'disabled' AND ciphertext IS NOT NULL AND nonce IS NOT NULL
            AND seal_key_id IS NOT NULL AND seal_algorithm_version IS NOT NULL
            AND guard_key_id IS NOT NULL AND secret_consistency_guard IS NOT NULL)
    ),
    CONSTRAINT ck_generation_credential_version_rotation_proof CHECK (
        (version_kind = 'rotation' AND rotation_proof_ref IS NOT NULL)
        OR (version_kind <> 'rotation' AND rotation_proof_ref IS NULL)
    )
);

ALTER TABLE generation_credential_bindings
    ADD CONSTRAINT fk_generation_credential_binding_head
    FOREIGN KEY (binding_id, current_version)
    REFERENCES generation_credential_versions (binding_id, binding_version)
    ON DELETE RESTRICT
    DEFERRABLE INITIALLY DEFERRED;

CREATE INDEX ix_generation_credential_versions_binding_principal
    ON generation_credential_versions (binding_id, principal_id, binding_version);

CREATE TABLE generation_prepared_payloads (
    prepared_payload_id VARCHAR(36) PRIMARY KEY,
    actor_id VARCHAR(36) NOT NULL,
    operation VARCHAR(64) NOT NULL,
    server_attempt_id VARCHAR(128) NOT NULL,
    command_proof VARCHAR(64) NOT NULL,
    context_fingerprint VARCHAR(128) NOT NULL,
    schema_version VARCHAR(128) NOT NULL,
    projector_version VARCHAR(128) NOT NULL,
    reference_policy_version VARCHAR(128) NOT NULL,
    target_ref VARCHAR(255) NOT NULL,
    model_ref VARCHAR(255) NOT NULL,
    grant_ref VARCHAR(255) NOT NULL,
    source VARCHAR(16) NOT NULL,
    row_id VARCHAR(36) NOT NULL,
    owner_or_system VARCHAR(128) NOT NULL,
    provider VARCHAR(64) NOT NULL,
    binding_id VARCHAR(36) NOT NULL,
    binding_version INTEGER NOT NULL,
    principal_id VARCHAR(36) NOT NULL,
    ciphertext BYTEA NOT NULL,
    nonce BYTEA NOT NULL,
    encryption_key_id VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_generation_prepared_payload_attempt UNIQUE (actor_id, operation, server_attempt_id),
    CONSTRAINT fk_generation_prepared_payload_binding_version FOREIGN KEY
        (binding_id, binding_version, principal_id, source, row_id, owner_or_system, provider)
        REFERENCES generation_credential_versions
        (binding_id, binding_version, principal_id, source, row_id, owner_or_system, provider)
        ON DELETE RESTRICT
);

CREATE TABLE generation_credential_schema_migrations (
    version VARCHAR(128) PRIMARY KEY,
    checksum VARCHAR(64) NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_generation_credential_migration_checksum CHECK (length(checksum) = 64)
);

CREATE FUNCTION reject_generation_credential_immutable_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'immutable generation credential record';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_generation_credential_versions_immutable
    BEFORE UPDATE OR DELETE ON generation_credential_versions
    FOR EACH ROW EXECUTE FUNCTION reject_generation_credential_immutable_change();

CREATE TRIGGER trg_generation_prepared_payloads_immutable
    BEFORE UPDATE OR DELETE ON generation_prepared_payloads
    FOR EACH ROW EXECUTE FUNCTION reject_generation_credential_immutable_change();

CREATE FUNCTION guard_generation_credential_principal_update() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'immutable generation credential principal';
    END IF;
    IF NEW.principal_id <> OLD.principal_id
        OR NEW.provider <> OLD.provider
        OR NEW.issuer_namespace <> OLD.issuer_namespace
        OR NEW.stable_account_subject <> OLD.stable_account_subject
        OR NEW.owner_scope <> OLD.owner_scope
        OR NEW.record_id <> OLD.record_id
        OR NEW.evidence_ref <> OLD.evidence_ref
        OR NEW.evidence_version <> OLD.evidence_version
        OR NEW.created_by <> OLD.created_by
        OR NEW.created_at <> OLD.created_at
        OR NOT (OLD.source_state = 'valid' AND NEW.source_state = 'revoked') THEN
        RAISE EXCEPTION 'invalid generation credential principal change';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_generation_credential_principals_immutable_identity
    BEFORE UPDATE OR DELETE ON generation_credential_principals
    FOR EACH ROW EXECUTE FUNCTION guard_generation_credential_principal_update();

CREATE FUNCTION guard_generation_credential_binding_update() RETURNS trigger AS $$
BEGIN
    IF NEW.binding_id <> OLD.binding_id
        OR NEW.source <> OLD.source
        OR NEW.row_id <> OLD.row_id
        OR NEW.owner_or_system <> OLD.owner_or_system
        OR NEW.provider <> OLD.provider
        OR NEW.revision <> OLD.revision + 1
        OR NEW.current_version <> OLD.current_version + 1
        OR (OLD.enabled = FALSE AND NEW.enabled = TRUE) THEN
        RAISE EXCEPTION 'invalid generation credential binding change';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_generation_credential_bindings_guard
    BEFORE UPDATE ON generation_credential_bindings
    FOR EACH ROW EXECUTE FUNCTION guard_generation_credential_binding_update();

CREATE FUNCTION reject_generation_credential_migration_history_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'immutable generation credential migration history';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_generation_credential_migrations_immutable
    BEFORE UPDATE OR DELETE ON generation_credential_schema_migrations
    FOR EACH ROW EXECUTE FUNCTION reject_generation_credential_migration_history_change();
