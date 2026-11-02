BEGIN;

CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;

CREATE TABLE IF NOT EXISTS ops.pipeline_runs (
    run_id text PRIMARY KEY CHECK (length(trim(run_id)) > 0),
    operation text NOT NULL CHECK (length(trim(operation)) > 0),
    status text NOT NULL CHECK (status IN (
        'CREATED', 'EXTRACTING', 'RAW_READY', 'BRONZE_READY',
        'SILVER_VALIDATED', 'GOLD_VALIDATED', 'DS_VALIDATED',
        'PUBLISHED', 'FAILED', 'QUARANTINED'
    )),
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    config_sha256 text CHECK (config_sha256 ~ '^[0-9a-f]{64}$'),
    details jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(details) = 'object'),
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);

-- Only verified COMPLETE Raw snapshots are registered for loading.
CREATE TABLE IF NOT EXISTS ops.data_snapshots (
    snapshot_id text PRIMARY KEY CHECK (length(trim(snapshot_id)) > 0),
    run_id text REFERENCES ops.pipeline_runs (run_id),
    stream text NOT NULL CHECK (stream IN ('monthly_overview', 'book_details')),
    scope_id text NOT NULL CHECK (scope_id IN ('ALL_ELIGIBLE_AGG', 'SELECTED_DETAIL')),
    period date NOT NULL CHECK (extract(day FROM period) = 1),
    status text NOT NULL DEFAULT 'COMPLETE' CHECK (status = 'COMPLETE'),
    source_url text NOT NULL CHECK (length(trim(source_url)) > 0),
    dataset_id text NOT NULL CHECK (length(trim(dataset_id)) > 0),
    manifest_path text NOT NULL UNIQUE CHECK (length(trim(manifest_path)) > 0),
    manifest_sha256 text NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    config_sha256 text NOT NULL CHECK (config_sha256 ~ '^[0-9a-f]{64}$'),
    extractor_version text NOT NULL CHECK (length(trim(extractor_version)) > 0),
    mapping_version text NOT NULL CHECK (length(trim(mapping_version)) > 0),
    extracted_at timestamptz NOT NULL,
    completed_at timestamptz NOT NULL,
    row_count bigint NOT NULL CHECK (row_count >= 0),
    source_row_count bigint CHECK (source_row_count >= 0),
    checkouts bigint NOT NULL CHECK (checkouts >= 0),
    registered_at timestamptz NOT NULL DEFAULT now(),
    CHECK (completed_at >= extracted_at),
    CHECK (
        (stream = 'monthly_overview' AND scope_id = 'ALL_ELIGIBLE_AGG'
            AND source_row_count IS NOT NULL)
        OR (stream = 'book_details' AND scope_id = 'SELECTED_DETAIL'
            AND source_row_count IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS data_snapshots_period_stream_idx
    ON ops.data_snapshots (period, stream, completed_at DESC);

CREATE TABLE IF NOT EXISTS bronze.load_batches (
    batch_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    snapshot_id text NOT NULL REFERENCES ops.data_snapshots (snapshot_id),
    run_id text REFERENCES ops.pipeline_runs (run_id),
    status text NOT NULL CHECK (status IN ('STARTED', 'COMPLETE', 'FAILED')),
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    inserted_rows bigint NOT NULL DEFAULT 0 CHECK (inserted_rows >= 0),
    error_message text,
    UNIQUE (batch_id, snapshot_id),
    CHECK ((status = 'STARTED' AND finished_at IS NULL)
        OR (status IN ('COMPLETE', 'FAILED') AND finished_at IS NOT NULL)),
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);

-- A replay may record failed attempts, but only one completed load per snapshot.
CREATE UNIQUE INDEX IF NOT EXISTS load_batches_one_complete_per_snapshot_idx
    ON bronze.load_batches (snapshot_id) WHERE status = 'COMPLETE';

CREATE INDEX IF NOT EXISTS load_batches_snapshot_idx
    ON bronze.load_batches (snapshot_id, started_at DESC);

CREATE TABLE IF NOT EXISTS bronze.checkout_records (
    snapshot_id text NOT NULL,
    source_row_number bigint NOT NULL CHECK (source_row_number > 0),
    load_batch_id bigint NOT NULL,
    raw_path text NOT NULL CHECK (length(trim(raw_path)) > 0),
    source_payload jsonb NOT NULL CHECK (jsonb_typeof(source_payload) = 'object'),
    loaded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (snapshot_id, source_row_number),
    FOREIGN KEY (load_batch_id, snapshot_id)
        REFERENCES bronze.load_batches (batch_id, snapshot_id)
);

CREATE INDEX IF NOT EXISTS checkout_records_load_batch_idx
    ON bronze.checkout_records (load_batch_id);

COMMENT ON TABLE bronze.checkout_records IS
    'One unmodified Raw JSON object per snapshot and source_row_number.';

COMMIT;
