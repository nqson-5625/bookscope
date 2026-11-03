CREATE TABLE IF NOT EXISTS ops.dq_runs (
    run_id uuid PRIMARY KEY,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('PASS', 'BLOCK')),
    contract_version text NOT NULL,
    mapping_version text NOT NULL,
    selection_sha256 text NOT NULL CHECK (selection_sha256 ~ '^[0-9a-f]{64}$'),
    result_count integer NOT NULL CHECK (result_count >= 0),
    block_count integer NOT NULL CHECK (block_count >= 0),
    warning_count integer NOT NULL CHECK (warning_count >= 0)
);

ALTER TABLE ops.dq_runs ADD COLUMN IF NOT EXISTS dq_version text;
ALTER TABLE ops.dq_runs ADD COLUMN IF NOT EXISTS code_sha256 text;
ALTER TABLE ops.dq_runs ADD COLUMN IF NOT EXISTS config_sha256 text;
ALTER TABLE ops.dq_runs ADD COLUMN IF NOT EXISTS source_state jsonb;

CREATE TABLE IF NOT EXISTS ops.dq_source_revisions (
    source_table text PRIMARY KEY,
    revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0)
);

INSERT INTO ops.dq_source_revisions (source_table) VALUES
    ('ops.data_snapshots'),
    ('bronze.load_batches'),
    ('bronze.checkout_records'),
    ('silver.format_map'),
    ('silver.overview_groups'),
    ('silver.detail_records'),
    ('silver.quarantine_records'),
    ('silver.snapshot_quality'),
    ('silver.run_snapshot_selection')
ON CONFLICT (source_table) DO NOTHING;

CREATE OR REPLACE FUNCTION ops.bump_dq_source_revision() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE ops.dq_source_revisions SET revision = revision + 1
    WHERE source_table = TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME;
    RETURN NULL;
END;
$$;

DO $$
DECLARE relation_name text;
BEGIN
    FOR relation_name IN SELECT source_table FROM ops.dq_source_revisions LOOP
        EXECUTE format(
            'CREATE OR REPLACE TRIGGER dq_source_revision '
            || 'AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON %s '
            || 'FOR EACH STATEMENT EXECUTE FUNCTION ops.bump_dq_source_revision()',
            relation_name::regclass
        );
    END LOOP;
END;
$$;

CREATE OR REPLACE FUNCTION ops.dq_current_source_state() RETURNS jsonb
LANGUAGE sql STABLE AS $$
    SELECT coalesce(jsonb_object_agg(source_table, jsonb_build_object(
        'revision', revision,
        'relation_oid', to_regclass(source_table)::oid,
        'relation_file', (
            SELECT relfilenode FROM pg_class WHERE oid = to_regclass(source_table)
        ),
        'trigger_enabled', EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgrelid = to_regclass(source_table)
              AND tgname = 'dq_source_revision' AND tgenabled = 'O'
        )
    )), '{}'::jsonb)
    FROM ops.dq_source_revisions
$$;

CREATE TABLE IF NOT EXISTS ops.dq_results (
    run_id uuid NOT NULL REFERENCES ops.dq_runs (run_id),
    result_no integer NOT NULL CHECK (result_no > 0),
    metric_code text NOT NULL,
    severity text NOT NULL CHECK (severity IN ('BLOCK', 'WARN', 'CONTEXT')),
    status text NOT NULL CHECK (status IN ('PASS', 'BLOCK', 'WARN', 'CONTEXT')),
    stream text,
    month_start date,
    snapshot_id text,
    format_key text,
    expected_value numeric,
    observed_value numeric,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (run_id, result_no)
);

CREATE UNIQUE INDEX IF NOT EXISTS dq_results_selection_key
    ON ops.dq_results (run_id, stream, month_start)
    WHERE metric_code = 'SNAPSHOT_SELECTION';

CREATE INDEX IF NOT EXISTS dq_results_lookup
    ON ops.dq_results (metric_code, month_start, stream, format_key);

CREATE OR REPLACE VIEW ops.dq_gate AS
WITH latest AS (
    SELECT * FROM ops.dq_runs
    ORDER BY finished_at DESC, run_id DESC LIMIT 1
), selection_check AS (
    SELECT count(*)::bigint AS expected_period_scopes,
        count(*) FILTER (WHERE r.result_no IS NOT NULL
            AND r.snapshot_id IS NOT DISTINCT FROM s.snapshot_id
            AND r.details ->> 'selection_status' = s.selection_status
            AND r.details ->> 'manifest_sha256' IS NOT DISTINCT FROM s.manifest_sha256
            AND r.details ->> 'scope_id' = s.scope_id
        )::bigint AS matched_period_scopes
    FROM silver.int_snapshot_selection AS s
    LEFT JOIN ops.dq_results AS r
        ON r.run_id = (SELECT run_id FROM latest)
        AND r.metric_code = 'SNAPSHOT_SELECTION'
        AND r.stream = s.stream AND r.month_start = s.month_start
)
SELECT l.run_id, l.finished_at, l.contract_version,
    c.expected_period_scopes, c.matched_period_scopes,
    l.block_count, l.warning_count,
    CASE WHEN l.status = 'PASS'
        AND l.source_state = ops.dq_current_source_state()
        AND l.contract_version = (SELECT contract_version FROM silver.pipeline_gate)
        AND (SELECT status FROM silver.pipeline_gate) = 'PASS'
        AND (SELECT r.details ->> 'selected_invocation_id'
             FROM ops.dq_results AS r
             WHERE r.run_id = l.run_id AND r.metric_code = 'DBT_BUILD'
             LIMIT 1) = (
            SELECT invocation_id FROM silver.run_snapshot_selection
            ORDER BY selected_at DESC, invocation_id DESC LIMIT 1
        )
        AND c.expected_period_scopes = c.matched_period_scopes
        AND c.expected_period_scopes = (
            SELECT count(*) FROM ops.dq_results
            WHERE run_id = l.run_id AND metric_code = 'SNAPSHOT_SELECTION'
        )
        THEN 'PASS' ELSE 'BLOCK' END AS status,
    l.source_state = ops.dq_current_source_state() AS source_state_matches
FROM selection_check AS c
LEFT JOIN latest AS l ON true;

CREATE OR REPLACE VIEW silver.dq_approved_overview_groups AS
SELECT g.* FROM silver.active_overview_groups AS g
CROSS JOIN ops.dq_gate AS gate WHERE gate.status = 'PASS';

CREATE OR REPLACE VIEW silver.dq_approved_detail_records AS
SELECT d.* FROM silver.active_detail_records AS d
CROSS JOIN ops.dq_gate AS gate WHERE gate.status = 'PASS';
