\set ON_ERROR_STOP on
BEGIN;

INSERT INTO ops.pipeline_runs (run_id, operation, status)
VALUES ('schema-smoke-run', 'schema-smoke', 'RAW_READY');

INSERT INTO ops.data_snapshots (
    snapshot_id, run_id, stream, scope_id, period, source_url, dataset_id,
    manifest_path, manifest_sha256, config_sha256, extractor_version,
    mapping_version, extracted_at, completed_at, row_count, checkouts
) VALUES (
    'schema-smoke-snapshot', 'schema-smoke-run', 'book_details',
    'SELECTED_DETAIL', DATE '2023-01-01', 'https://example.test/query.json',
    'test-dataset', 'test/snapshot/manifest.json', repeat('a', 64),
    repeat('b', 64), 'schema-smoke', 'v1', now() - interval '1 minute',
    now(), 2, 3
);

INSERT INTO bronze.load_batches (snapshot_id, run_id, status)
VALUES ('schema-smoke-snapshot', 'schema-smoke-run', 'STARTED');

INSERT INTO bronze.checkout_records (
    snapshot_id, source_row_number, load_batch_id, raw_path, source_payload
)
SELECT 'schema-smoke-snapshot', numbers.n, batches.batch_id,
       'test/part-0001.jsonl.gz',
       jsonb_build_object('checkouts', numbers.n::text)
FROM (VALUES (1), (2)) AS numbers(n)
CROSS JOIN bronze.load_batches AS batches
WHERE batches.snapshot_id = 'schema-smoke-snapshot';

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM bronze.snapshot_load_reconciliation
        WHERE snapshot_id = 'schema-smoke-snapshot' AND ready_for_transform
    ) THEN
        RAISE EXCEPTION 'STARTED batch was treated as ready';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM bronze.snapshot_load_reconciliation
        WHERE snapshot_id = 'schema-smoke-snapshot'
          AND loaded_rows = 0 AND non_complete_batch_rows = 2
          AND loaded_checkouts = 0
    ) THEN
        RAISE EXCEPTION 'STARTED batch rows entered reconciliation totals';
    END IF;
END
$$;

UPDATE bronze.load_batches
SET status = 'COMPLETE', finished_at = now(), inserted_rows = 2
WHERE snapshot_id = 'schema-smoke-snapshot';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM bronze.snapshot_load_reconciliation
        WHERE snapshot_id = 'schema-smoke-snapshot'
          AND expected_rows = 2 AND loaded_rows = 2
          AND expected_checkouts = 3 AND loaded_checkouts = 3
          AND row_count_matches AND checkouts_match AND ready_for_transform
    ) THEN
        RAISE EXCEPTION 'Bronze reconciliation did not pass';
    END IF;
    UPDATE bronze.checkout_records
    SET source_payload = '{"checkouts": "9"}'::jsonb
    WHERE snapshot_id = 'schema-smoke-snapshot' AND source_row_number = 2;
    IF NOT EXISTS (
        SELECT 1 FROM bronze.snapshot_load_reconciliation
        WHERE snapshot_id = 'schema-smoke-snapshot'
          AND row_count_matches AND NOT checkouts_match
          AND expected_checkouts = 3 AND loaded_checkouts = 10
          AND NOT ready_for_transform
    ) THEN
        RAISE EXCEPTION 'Checkout mismatch did not block transform';
    END IF;
    UPDATE bronze.checkout_records
    SET source_payload = '{"checkouts": "invalid"}'::jsonb
    WHERE snapshot_id = 'schema-smoke-snapshot' AND source_row_number = 2;
    IF NOT EXISTS (
        SELECT 1 FROM bronze.snapshot_load_reconciliation
        WHERE snapshot_id = 'schema-smoke-snapshot'
          AND valid_checkout_rows = 1 AND NOT checkouts_match
          AND NOT ready_for_transform
    ) THEN
        RAISE EXCEPTION 'Invalid checkout value did not block transform';
    END IF;
    UPDATE bronze.checkout_records
    SET source_payload = '{"checkouts": "2"}'::jsonb
    WHERE snapshot_id = 'schema-smoke-snapshot' AND source_row_number = 2;
    BEGIN
        INSERT INTO bronze.checkout_records (
            snapshot_id, source_row_number, load_batch_id, raw_path, source_payload
        )
        SELECT 'schema-smoke-snapshot', 1, batch_id, 'test/part-0001.jsonl.gz',
               '{}'::jsonb
        FROM bronze.load_batches
        WHERE snapshot_id = 'schema-smoke-snapshot';
        RAISE EXCEPTION 'Duplicate Bronze key was accepted';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;
    BEGIN
        INSERT INTO bronze.load_batches (
            snapshot_id, run_id, status, finished_at, inserted_rows
        ) VALUES (
            'schema-smoke-snapshot', 'schema-smoke-run', 'COMPLETE', now(), 2
        );
        RAISE EXCEPTION 'Second COMPLETE batch was accepted';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;
END
$$;

INSERT INTO ops.data_snapshots (
    snapshot_id, run_id, stream, scope_id, period, source_url, dataset_id,
    manifest_path, manifest_sha256, config_sha256, extractor_version,
    mapping_version, extracted_at, completed_at, row_count,
    source_row_count, checkouts
) VALUES (
    'schema-smoke-overview', 'schema-smoke-run', 'monthly_overview',
    'ALL_ELIGIBLE_AGG', DATE '2023-01-01', 'https://example.test/query.json',
    'test-dataset', 'test/overview/manifest.json', repeat('c', 64),
    repeat('d', 64), 'schema-smoke', 'v1', now() - interval '1 minute',
    now(), 2, 5, 3
);

INSERT INTO bronze.load_batches (snapshot_id, run_id, status)
VALUES ('schema-smoke-overview', 'schema-smoke-run', 'STARTED');

INSERT INTO bronze.checkout_records (
    snapshot_id, source_row_number, load_batch_id, raw_path, source_payload
)
SELECT 'schema-smoke-overview', source_groups.n, batches.batch_id,
       'test/overview/part-0001.jsonl.gz',
       jsonb_build_object('row_count', source_groups.row_count,
                          'checkouts', source_groups.checkouts)
FROM (VALUES (1, '2', '1'), (2, '3', '2')) AS source_groups(n, row_count, checkouts)
CROSS JOIN bronze.load_batches AS batches
WHERE batches.snapshot_id = 'schema-smoke-overview';

UPDATE bronze.load_batches
SET status = 'COMPLETE', finished_at = now(), inserted_rows = 2
WHERE snapshot_id = 'schema-smoke-overview';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM bronze.snapshot_load_reconciliation
        WHERE snapshot_id = 'schema-smoke-overview'
          AND expected_source_row_count = 5 AND loaded_source_row_count = 5
          AND valid_source_row_count_rows = 2 AND source_row_count_matches
          AND ready_for_transform
    ) THEN
        RAISE EXCEPTION 'Overview source row count did not pass';
    END IF;
    UPDATE bronze.checkout_records
    SET source_payload = jsonb_set(source_payload, '{row_count}', '"4"')
    WHERE snapshot_id = 'schema-smoke-overview' AND source_row_number = 2;
    IF NOT EXISTS (
        SELECT 1 FROM bronze.snapshot_load_reconciliation
        WHERE snapshot_id = 'schema-smoke-overview'
          AND loaded_source_row_count = 6 AND NOT source_row_count_matches
          AND checkouts_match AND NOT ready_for_transform
    ) THEN
        RAISE EXCEPTION 'Overview source row mismatch did not block transform';
    END IF;
    UPDATE bronze.checkout_records
    SET source_payload = jsonb_set(source_payload, '{row_count}', '"invalid"')
    WHERE snapshot_id = 'schema-smoke-overview' AND source_row_number = 2;
    IF NOT EXISTS (
        SELECT 1 FROM bronze.snapshot_load_reconciliation
        WHERE snapshot_id = 'schema-smoke-overview'
          AND valid_source_row_count_rows = 1 AND NOT source_row_count_matches
          AND NOT ready_for_transform
    ) THEN
        RAISE EXCEPTION 'Invalid overview row_count did not block transform';
    END IF;
    UPDATE bronze.checkout_records
    SET source_payload = jsonb_set(source_payload, '{row_count}', '"3"')
    WHERE snapshot_id = 'schema-smoke-overview' AND source_row_number = 2;
END
$$;

INSERT INTO bronze.load_batches (snapshot_id, run_id, status, finished_at)
VALUES ('schema-smoke-overview', 'schema-smoke-run', 'FAILED', now());

INSERT INTO bronze.load_batches (snapshot_id, run_id, status)
VALUES ('schema-smoke-overview', 'schema-smoke-run', 'STARTED');

INSERT INTO bronze.checkout_records (
    snapshot_id, source_row_number, load_batch_id, raw_path, source_payload
)
SELECT 'schema-smoke-overview',
       CASE WHEN batches.status = 'FAILED' THEN 3 ELSE 4 END,
       batches.batch_id, 'test/overview/part-0001.jsonl.gz',
       '{"row_count": "100", "checkouts": "100"}'::jsonb
FROM bronze.load_batches AS batches
WHERE batches.snapshot_id = 'schema-smoke-overview'
  AND batches.status IN ('FAILED', 'STARTED');

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM bronze.snapshot_load_reconciliation
        WHERE snapshot_id = 'schema-smoke-overview'
          AND loaded_rows = 2 AND loaded_source_row_count = 5
          AND loaded_checkouts = 3 AND non_complete_batch_rows = 2
          AND row_count_matches AND source_row_count_matches
          AND checkouts_match AND NOT ready_for_transform
    ) THEN
        RAISE EXCEPTION 'Non-COMPLETE batch rows entered totals or passed gate';
    END IF;
END
$$;

ROLLBACK;
