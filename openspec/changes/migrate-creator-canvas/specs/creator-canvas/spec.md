# Creator canvas

## Purpose

Provide a chapter-level creative canvas backed by the chapter's current source data and a guarded canvas document API.

## Requirements

### Requirement: guarded canvas negotiation

The API MUST preserve the existing default GET and PUT behavior. A client that requests GET /api/chapters/{chapter_id}/canvas?save_mode=guarded-v1 MUST receive save_capability: guarded-v1 only when the server supports guarded saves. The GET MUST NOT create a canvas document.

If a client does not receive the capability marker, it MUST remain read-only and MUST NOT send a canvas PUT.

### Requirement: guarded conditional save

A guarded PUT MUST provide save_guard with mode guarded-v1, the expected document ID or null for first creation, and a strict positive expected version. If an outer version is present, it MUST match the guard version.

An update MUST match the expected document ID, chapter, series, and version in the database write. First creation MUST be atomic and MUST NOT fall through to an update when another writer has created the document. A missing match MUST return a conflict and MUST leave the document unchanged.

A successful response MUST return document data and metadata from the same conditional write, with a guarded receipt whose ID and version match the top-level response. The default legacy write mode MUST keep its established snapshot comparison, version behavior, and post-commit request-document echo.

### Requirement: preserve the complete canvas document

The client MUST retain the full JSON document as its save base, including fields it does not render. Editing a layout, note, card, hidden relation, viewport, or manual edge MUST patch only the targeted data and preserve unknown fields, stable IDs, and dangling references.

The client MUST remain read-only if the stored JSON cannot be represented safely. Loading an empty or failed response MUST NOT create an automatic save. Layout references such as frame:i and media:i:mi MUST NOT be treated as authority to edit chapter or asset data.

### Requirement: chapter canvas workflow

From the selected chapter, a creator MUST be able to open the canvas, see current chapter frames and referenced assets, and view available storyboard compositions, previews, videos, notes, cards, and manual relations.

The canvas MUST support node selection and filtering, node movement, zoom, pan, fit-to-content, note and text-card editing, manual relations, and local undo and redo. Initial auto-layout MUST remain display-only until the creator changes a position.

Saving MUST use a guarded capability and receipt. One save may be in flight at a time. Edits made during a save MUST remain dirty until their own save is confirmed. Conflicts, malformed receipts, and unknown results MUST retain the draft and stop automatic saving.

Closing a dirty or in-flight canvas MUST offer an explicit wait, continue-editing, or local-draft-discard choice. A discarded local draft MUST NOT be presented as proof that an unknown server write did not commit.

### Requirement: scope and media lifecycle

Canvas reads, writes, timers, gestures, keyboard listeners, and image object URLs MUST be scoped to the current actor, session generation, service port, series, chapter, and open instance. Late results from an old scope MUST NOT update the current canvas or end a replacement session.

Canvas images MUST be loaded through an authenticated same-origin upload or image-proxy request with image type and size checks. The canvas MUST release object URLs when the source or scope changes.

## Out of scope

This change does not implement source chapter or asset CRUD, owned generation-result application, generation submission, first-success result application, legacy blind-writer convergence, or final browser/PostgreSQL/multi-user acceptance.
