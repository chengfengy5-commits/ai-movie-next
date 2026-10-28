# Continuous chapter operation receipts

`0005_continuous_chapter_operations.sql` belongs to the continuous chapter
operation-receipt feature. Its checksum and table list are recorded in
`continuous_chapter_operations_manifest.json` and checked by the feature's
explicit migration helper.

This migration is separate from `manifest.json` and the generation migration
runner. It does not change migrations 0001–0004 or the readiness of generation
and legacy chapter routes. The application never creates or migrates this schema
at startup, during capability checks, or while serving requests. The new
continuous-operation capability is advertised only when this exact migration
and both required tables are present.

Local tests may apply this migration to an owned temporary SQLite database.
Production application and deployment are outside this feature's implementation
and test scope.

## Original operation protocol

The authenticated POST and GET routes are
`/api/chapters/{chapter_id}/continuous-edit/operations/{operation_id}`.
They cover guarded chapter saves and character, scene, or prop creation. The
business effect and its original receipt commit in the same caller transaction.
An existing owner/UUID with the same frozen request returns the original receipt;
a different request conflicts. Lookup also requires current access to the series.

`request_body_sha256` hashes the exact UTF-8 HTTP body. The wire field
`source_snapshot_digest` binds the complete frozen request using SHA-256 over
`b"haoai-continuous-source-v1\x00"` followed by the ASCII lowercase body hash.
It is a domain-separated source-context binding, not a reusable source-only
fingerprint. Full source, permission, and lock checks still run independently.
`canonical_response_json` contains the exact original response text stored in
the receipt; its UTF-8 hash is `result_digest`. Clients validate these bytes
instead of assuming Python and JavaScript serialize numbers identically.

A browser journal preserves the complete draft and exact request before sending.
Cooperating tabs serialize updates with Web Locks; localStorage is not a compare
and swap primitive. An unknown response remains tied to its original UUID and
is queried rather than automatically posted again. The frontend recovery
interface is a separate implementation step from these protocol foundations.
