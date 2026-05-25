-- 0031_data_uploads.sql — direct file uploads as agent data sources.
--
-- For the supply chain VP whose data lives in spreadsheets and documents, not
-- connected systems: a reviewer uploads a CSV / Excel / Parquet / PDF / DOCX
-- file, ownEvo parses it once, and the workflow's agent reads the parsed
-- result by id on every iteration (no re-upload).
--
-- The parsed representation — not the raw bytes — is what the agent consumes,
-- so it is stored here directly:
--   * spreadsheets: `schema` holds the detected columns + dtypes, `content`
--     holds {"rows": [...]} (JSON-coerced cells), `row_count` the count.
--   * documents: `schema` holds the structured metadata (title, section
--     headings, table count), `content` holds {"text": "...", "sections":
--     [...], "tables": [...]}.
-- `sha256` + `size_bytes` + original `name` are kept for provenance and
-- dedupe. Raw bytes are intentionally not retained — re-parsing a stored blob
-- buys nothing once the normalized form exists, and it keeps the row small.
--
-- `retention_expires_at` records when an upload may be purged; a NULL means
-- keep indefinitely. Single-tenant MVP: no workspace_id (consistent with the
-- rest of the schema); the multi-tenant retrofit adds it alongside the others.

CREATE TABLE IF NOT EXISTS data_uploads (
    id                    uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    name                  text        NOT NULL,
    kind                  text        NOT NULL
        CHECK (kind IN ('csv', 'excel', 'parquet', 'pdf', 'docx')),
    content_type          text,
    size_bytes            bigint      NOT NULL,
    sha256                text        NOT NULL,
    schema                jsonb       NOT NULL DEFAULT '{}'::jsonb,
    row_count             integer,
    content               jsonb       NOT NULL DEFAULT '{}'::jsonb,
    uploaded_at           timestamptz NOT NULL DEFAULT now(),
    retention_expires_at  timestamptz
);
