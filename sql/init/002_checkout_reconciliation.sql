-- Runs after 001 on a new volume; apply this file directly to an existing DB.
BEGIN;

CREATE OR REPLACE VIEW bronze.snapshot_load_reconciliation AS
SELECT
    s.snapshot_id,
    s.stream,
    s.scope_id,
    s.period,
    s.row_count AS expected_rows,
    loaded.loaded_rows,
    b.batch_id AS completed_batch_id,
    (loaded.loaded_rows = s.row_count) AS row_count_matches,
    (b.batch_id IS NOT NULL
        AND loaded.non_complete_batch_rows = 0
        AND b.inserted_rows = loaded.loaded_rows
        AND loaded.loaded_rows = s.row_count
        AND loaded.valid_checkout_rows = loaded.loaded_rows
        AND loaded.loaded_checkouts = s.checkouts
        AND (s.stream <> 'monthly_overview' OR (
            loaded.valid_source_row_count_rows = loaded.loaded_rows
            AND loaded.loaded_source_row_count = s.source_row_count
        ))) AS ready_for_transform,
    s.checkouts AS expected_checkouts,
    loaded.loaded_checkouts,
    loaded.valid_checkout_rows,
    (loaded.valid_checkout_rows = loaded.loaded_rows
        AND loaded.loaded_checkouts = s.checkouts) AS checkouts_match,
    s.source_row_count AS expected_source_row_count,
    loaded.loaded_source_row_count,
    loaded.valid_source_row_count_rows,
    CASE WHEN s.stream = 'monthly_overview' THEN
        (loaded.valid_source_row_count_rows = loaded.loaded_rows
            AND loaded.loaded_source_row_count = s.source_row_count)
    END AS source_row_count_matches,
    loaded.non_complete_batch_rows
FROM ops.data_snapshots AS s
LEFT JOIN bronze.load_batches AS b
    ON b.snapshot_id = s.snapshot_id AND b.status = 'COMPLETE'
LEFT JOIN LATERAL (
    SELECT
        count(*) FILTER (WHERE r.load_batch_id = b.batch_id) AS loaded_rows,
        count(*) FILTER (
            WHERE r.load_batch_id IS DISTINCT FROM b.batch_id
        ) AS non_complete_batch_rows,
        count(*) FILTER (
            WHERE r.load_batch_id = b.batch_id
                AND (r.source_payload ->> 'checkouts') ~ '^[0-9]+$'
        ) AS valid_checkout_rows,
        COALESCE(sum(
            CASE
                WHEN r.load_batch_id = b.batch_id
                    AND (r.source_payload ->> 'checkouts') ~ '^[0-9]+$'
                THEN (r.source_payload ->> 'checkouts')::numeric
            END
        ), 0) AS loaded_checkouts,
        count(*) FILTER (
            WHERE r.load_batch_id = b.batch_id
                AND (r.source_payload ->> 'row_count') ~ '^[0-9]+$'
        ) AS valid_source_row_count_rows,
        COALESCE(sum(
            CASE
                WHEN r.load_batch_id = b.batch_id
                    AND (r.source_payload ->> 'row_count') ~ '^[0-9]+$'
                THEN (r.source_payload ->> 'row_count')::numeric
            END
        ), 0) AS loaded_source_row_count
    FROM bronze.checkout_records AS r
    WHERE r.snapshot_id = s.snapshot_id
) AS loaded ON true;

COMMENT ON VIEW bronze.snapshot_load_reconciliation IS
    'Raw-to-Bronze gate: all rows belong to the COMPLETE batch; row counts, overview source rows and valid checkout total match.';

COMMIT;
