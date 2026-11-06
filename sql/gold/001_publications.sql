-- Gold storage is append-only by data_version. dbt builds in a private schema.
CREATE SCHEMA IF NOT EXISTS gold;

CREATE TABLE IF NOT EXISTS ops.gold_builds (
    build_id uuid PRIMARY KEY,
    dq_run_id uuid NOT NULL REFERENCES ops.dq_runs(run_id),
    selection_sha256 char(64) NOT NULL CHECK (selection_sha256 ~ '^[0-9a-f]{64}$'),
    source_state jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('PINNED', 'TESTED', 'PUBLISHED', 'REUSED', 'FAILED')),
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    data_version char(64),
    code_sha256 char(64),
    output_sha256 char(64),
    dbt_results jsonb,
    validation_results jsonb,
    error_text text
);
ALTER TABLE ops.gold_builds ADD COLUMN IF NOT EXISTS code_sha256 char(64);
ALTER TABLE ops.gold_builds ADD COLUMN IF NOT EXISTS output_sha256 char(64);
ALTER TABLE ops.gold_builds ADD COLUMN IF NOT EXISTS dbt_results jsonb;
ALTER TABLE ops.gold_builds ADD COLUMN IF NOT EXISTS validation_results jsonb;

CREATE TABLE IF NOT EXISTS ops.gold_build_pins (
    build_id uuid NOT NULL REFERENCES ops.gold_builds(build_id),
    stream text NOT NULL CHECK (stream IN ('monthly_overview', 'book_details')),
    scope_id text NOT NULL CHECK (scope_id IN ('ALL_ELIGIBLE_AGG', 'SELECTED_DETAIL')),
    month_start date NOT NULL CHECK (extract(day FROM month_start) = 1),
    snapshot_id text NOT NULL REFERENCES ops.data_snapshots(snapshot_id),
    manifest_sha256 char(64) NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (build_id, stream, scope_id, month_start),
    UNIQUE (build_id, snapshot_id),
    CHECK ((stream = 'monthly_overview' AND scope_id = 'ALL_ELIGIBLE_AGG')
        OR (stream = 'book_details' AND scope_id = 'SELECTED_DETAIL'))
);

CREATE TABLE IF NOT EXISTS ops.data_publications (
    data_version char(64) PRIMARY KEY CHECK (data_version ~ '^[0-9a-f]{64}$'),
    status text NOT NULL CHECK (status IN ('STAGED', 'PUBLISHED')),
    dq_run_id uuid NOT NULL REFERENCES ops.dq_runs(run_id),
    selection_sha256 char(64) NOT NULL CHECK (selection_sha256 ~ '^[0-9a-f]{64}$'),
    cohort_sha256 char(64) NOT NULL CHECK (cohort_sha256 ~ '^[0-9a-f]{64}$'),
    silver_contract_version text NOT NULL,
    mapping_version text NOT NULL,
    dq_version text NOT NULL,
    dq_code_sha256 char(64) NOT NULL,
    dq_config_sha256 char(64) NOT NULL,
    dq_source_state jsonb NOT NULL,
    gold_contract_version text NOT NULL,
    identity_version text NOT NULL,
    code_sha256 char(64) NOT NULL CHECK (code_sha256 ~ '^[0-9a-f]{64}$'),
    output_sha256 char(64) NOT NULL CHECK (output_sha256 ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE ops.data_publications DROP CONSTRAINT IF EXISTS data_publications_status_check;
ALTER TABLE ops.data_publications ADD CONSTRAINT data_publications_status_check
    CHECK (status IN ('STAGED', 'PUBLISHED'));
ALTER TABLE ops.data_publications ADD COLUMN IF NOT EXISTS dq_code_sha256 char(64);
ALTER TABLE ops.data_publications ADD COLUMN IF NOT EXISTS dq_config_sha256 char(64);
ALTER TABLE ops.data_publications ADD COLUMN IF NOT EXISTS dq_source_state jsonb;
ALTER TABLE ops.data_publications ALTER COLUMN dq_code_sha256 SET NOT NULL;
ALTER TABLE ops.data_publications ALTER COLUMN dq_config_sha256 SET NOT NULL;
ALTER TABLE ops.data_publications ALTER COLUMN dq_source_state SET NOT NULL;

CREATE TABLE IF NOT EXISTS ops.publication_snapshots (
    data_version char(64) NOT NULL REFERENCES ops.data_publications(data_version),
    stream text NOT NULL,
    scope_id text NOT NULL,
    month_start date NOT NULL,
    snapshot_id text NOT NULL REFERENCES ops.data_snapshots(snapshot_id),
    manifest_sha256 char(64) NOT NULL,
    PRIMARY KEY (data_version, stream, scope_id, month_start),
    UNIQUE (data_version, stream, scope_id, month_start, snapshot_id),
    UNIQUE (data_version, scope_id, month_start, snapshot_id),
    CHECK ((stream = 'monthly_overview' AND scope_id = 'ALL_ELIGIBLE_AGG')
        OR (stream = 'book_details' AND scope_id = 'SELECTED_DETAIL'))
);

CREATE TABLE IF NOT EXISTS ops.current_publication (
    id integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    data_version char(64) NOT NULL REFERENCES ops.data_publications(data_version),
    switched_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS gold.dim_month (
    month_start date PRIMARY KEY CHECK (extract(day FROM month_start) = 1),
    year_number smallint NOT NULL,
    month_number smallint NOT NULL CHECK (month_number BETWEEN 1 AND 12),
    year_month char(7) NOT NULL UNIQUE,
    CHECK (year_number = extract(year FROM month_start)),
    CHECK (month_number = extract(month FROM month_start))
);

CREATE TABLE IF NOT EXISTS gold.dim_format (
    format_key text PRIMARY KEY CHECK (format_key IN ('EBOOK', 'PRINT_BOOK')),
    format_label text NOT NULL
);

CREATE TABLE IF NOT EXISTS gold.dim_book_record (
    book_record_key char(64) PRIMARY KEY CHECK (book_record_key ~ '^[0-9a-f]{64}$'),
    identity_version text NOT NULL,
    title_raw text NOT NULL,
    creator_raw text,
    materialtype_cohort text NOT NULL,
    checkouttype_raw text NOT NULL,
    format_key text NOT NULL REFERENCES gold.dim_format(format_key),
    cohort_sha256 char(64) NOT NULL CHECK (cohort_sha256 ~ '^[0-9a-f]{64}$'),
    title_display text NOT NULL,
    UNIQUE NULLS NOT DISTINCT (identity_version, title_raw, creator_raw,
        materialtype_cohort, checkouttype_raw)
);

CREATE TABLE IF NOT EXISTS gold.monthly_overview (
    data_version char(64) NOT NULL REFERENCES ops.data_publications(data_version),
    scope_id text NOT NULL DEFAULT 'ALL_ELIGIBLE_AGG' CHECK (scope_id = 'ALL_ELIGIBLE_AGG'),
    month_start date NOT NULL REFERENCES gold.dim_month(month_start),
    format_key text NOT NULL REFERENCES gold.dim_format(format_key),
    source_snapshot_id text NOT NULL,
    checkouts bigint NOT NULL CHECK (checkouts >= 0),
    source_row_count bigint NOT NULL CHECK (source_row_count >= 0),
    source_group_count bigint NOT NULL CHECK (source_group_count >= 0),
    detail_checkouts bigint NOT NULL CHECK (detail_checkouts >= 0),
    active_book_records integer NOT NULL CHECK (active_book_records >= 0),
    cohort_coverage numeric,
    format_share numeric,
    mom_change numeric,
    yoy_change numeric,
    rolling_3m_sum bigint,
    rolling_3m_avg numeric,
    metric_status jsonb NOT NULL,
    PRIMARY KEY (data_version, scope_id, month_start, format_key),
    FOREIGN KEY (data_version, scope_id, month_start, source_snapshot_id)
        REFERENCES ops.publication_snapshots(data_version, scope_id, month_start, snapshot_id),
    CHECK (cohort_coverage IS NULL OR cohort_coverage BETWEEN 0 AND 1),
    CHECK (format_share IS NULL OR format_share BETWEEN 0 AND 1)
);

CREATE TABLE IF NOT EXISTS gold.book_monthly_stats (
    data_version char(64) NOT NULL REFERENCES ops.data_publications(data_version),
    scope_id text NOT NULL DEFAULT 'SELECTED_DETAIL' CHECK (scope_id = 'SELECTED_DETAIL'),
    book_record_key char(64) NOT NULL REFERENCES gold.dim_book_record(book_record_key),
    month_start date NOT NULL REFERENCES gold.dim_month(month_start),
    format_key text NOT NULL REFERENCES gold.dim_format(format_key),
    source_snapshot_id text NOT NULL,
    checkouts bigint NOT NULL CHECK (checkouts >= 0),
    source_record_count bigint NOT NULL CHECK (source_record_count > 0),
    PRIMARY KEY (data_version, scope_id, book_record_key, month_start, format_key),
    FOREIGN KEY (data_version, scope_id, month_start, source_snapshot_id)
        REFERENCES ops.publication_snapshots(data_version, scope_id, month_start, snapshot_id)
);

CREATE TABLE IF NOT EXISTS gold.book_monthly_features (
    data_version char(64) NOT NULL REFERENCES ops.data_publications(data_version),
    book_record_key char(64) NOT NULL REFERENCES gold.dim_book_record(book_record_key),
    month_start date NOT NULL REFERENCES gold.dim_month(month_start),
    format_key text NOT NULL REFERENCES gold.dim_format(format_key),
    observed boolean NOT NULL,
    checkouts bigint CHECK (checkouts >= 0),
    rolling_3m_sum bigint,
    rolling_3m_avg numeric,
    mom_change numeric,
    yoy_change numeric,
    book_growth_3m numeric,
    metric_status jsonb NOT NULL,
    PRIMARY KEY (data_version, book_record_key, month_start),
    CHECK (observed = (checkouts IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS gold.overview_source_lineage (
    data_version char(64) NOT NULL,
    snapshot_id text NOT NULL,
    source_row_number bigint NOT NULL,
    scope_id text NOT NULL CHECK (scope_id = 'ALL_ELIGIBLE_AGG'),
    month_start date NOT NULL,
    format_key text NOT NULL,
    checkouts bigint NOT NULL CHECK (checkouts >= 0),
    source_row_count bigint NOT NULL CHECK (source_row_count >= 0),
    PRIMARY KEY (data_version, snapshot_id, source_row_number),
    FOREIGN KEY (data_version, scope_id, month_start, format_key)
        REFERENCES gold.monthly_overview(data_version, scope_id, month_start, format_key),
    FOREIGN KEY (data_version, scope_id, month_start, snapshot_id)
        REFERENCES ops.publication_snapshots(data_version, scope_id, month_start, snapshot_id)
);

CREATE TABLE IF NOT EXISTS gold.book_source_lineage (
    data_version char(64) NOT NULL,
    snapshot_id text NOT NULL,
    source_row_number bigint NOT NULL,
    scope_id text NOT NULL CHECK (scope_id = 'SELECTED_DETAIL'),
    month_start date NOT NULL,
    format_key text NOT NULL,
    book_record_key char(64) NOT NULL,
    checkouts bigint NOT NULL CHECK (checkouts >= 0),
    PRIMARY KEY (data_version, snapshot_id, source_row_number),
    FOREIGN KEY (data_version, scope_id, book_record_key, month_start, format_key)
        REFERENCES gold.book_monthly_stats(data_version, scope_id, book_record_key, month_start, format_key),
    FOREIGN KEY (data_version, scope_id, month_start, snapshot_id)
        REFERENCES ops.publication_snapshots(data_version, scope_id, month_start, snapshot_id)
);

CREATE INDEX IF NOT EXISTS gold_book_stats_month_idx ON gold.book_monthly_stats(data_version, month_start, format_key);
CREATE INDEX IF NOT EXISTS gold_book_features_month_idx ON gold.book_monthly_features(data_version, month_start, format_key);
CREATE INDEX IF NOT EXISTS gold_overview_lineage_fact_idx ON gold.overview_source_lineage(data_version, month_start, format_key);
CREATE INDEX IF NOT EXISTS gold_book_lineage_fact_idx ON gold.book_source_lineage(data_version, month_start, format_key, book_record_key);

CREATE OR REPLACE FUNCTION ops.protect_gold_publication() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE version_id char(64);
BEGIN
    version_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.data_version ELSE NEW.data_version END;
    IF TG_OP = 'UPDATE' AND OLD.data_version <> NEW.data_version THEN
        RAISE EXCEPTION 'Gold data_version cannot be changed';
    END IF;
    IF EXISTS (SELECT 1 FROM ops.data_publications
               WHERE data_version = version_id AND status = 'PUBLISHED') THEN
        RAISE EXCEPTION 'Published Gold version % is immutable', version_id;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION ops.protect_gold_truncate() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Gold publication storage cannot be truncated';
END;
$$;

-- Dimension rows are shared by every data_version. Serialize their mutation
-- with promotion and preserve any row already used by a published version.
CREATE OR REPLACE FUNCTION ops.protect_published_gold_dimension() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE published_use boolean;
DECLARE row_id text;
BEGIN
    IF NOT pg_try_advisory_xact_lock(87261104) THEN
        RAISE EXCEPTION 'Gold publication is in progress; dimension mutation refused';
    END IF;
    IF TG_TABLE_NAME = 'dim_month' THEN
        row_id := OLD.month_start::text;
        SELECT EXISTS (
            SELECT 1 FROM gold.monthly_overview f
            JOIN ops.data_publications p USING (data_version)
            WHERE f.month_start = OLD.month_start AND p.status = 'PUBLISHED'
            UNION ALL
            SELECT 1 FROM gold.book_monthly_stats f
            JOIN ops.data_publications p USING (data_version)
            WHERE f.month_start = OLD.month_start AND p.status = 'PUBLISHED'
            UNION ALL
            SELECT 1 FROM gold.book_monthly_features f
            JOIN ops.data_publications p USING (data_version)
            WHERE f.month_start = OLD.month_start AND p.status = 'PUBLISHED'
        ) INTO published_use;
    ELSIF TG_TABLE_NAME = 'dim_format' THEN
        row_id := OLD.format_key;
        SELECT EXISTS (
            SELECT 1 FROM gold.monthly_overview f
            JOIN ops.data_publications p USING (data_version)
            WHERE f.format_key = OLD.format_key AND p.status = 'PUBLISHED'
            UNION ALL
            SELECT 1 FROM gold.book_monthly_stats f
            JOIN ops.data_publications p USING (data_version)
            WHERE f.format_key = OLD.format_key AND p.status = 'PUBLISHED'
            UNION ALL
            SELECT 1 FROM gold.book_monthly_features f
            JOIN ops.data_publications p USING (data_version)
            WHERE f.format_key = OLD.format_key AND p.status = 'PUBLISHED'
        ) INTO published_use;
    ELSIF TG_TABLE_NAME = 'dim_book_record' THEN
        row_id := OLD.book_record_key;
        SELECT EXISTS (
            SELECT 1 FROM gold.book_monthly_stats f
            JOIN ops.data_publications p USING (data_version)
            WHERE f.book_record_key = OLD.book_record_key AND p.status = 'PUBLISHED'
            UNION ALL
            SELECT 1 FROM gold.book_monthly_features f
            JOIN ops.data_publications p USING (data_version)
            WHERE f.book_record_key = OLD.book_record_key AND p.status = 'PUBLISHED'
        ) INTO published_use;
    ELSE
        RAISE EXCEPTION 'Unexpected protected dimension: %', TG_TABLE_NAME;
    END IF;
    IF published_use THEN
        RAISE EXCEPTION 'Published Gold dimension %.% is immutable',
            TG_TABLE_NAME, row_id;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;

DO $$
DECLARE relation_name text;
BEGIN
    FOREACH relation_name IN ARRAY ARRAY[
        'gold.dim_month', 'gold.dim_format', 'gold.dim_book_record'] LOOP
        EXECUTE format('CREATE OR REPLACE TRIGGER protect_published_dimension '
            || 'BEFORE UPDATE OR DELETE ON %s '
            || 'FOR EACH ROW EXECUTE FUNCTION ops.protect_published_gold_dimension()',
            relation_name::regclass);
        EXECUTE format('CREATE OR REPLACE TRIGGER protect_truncate '
            || 'BEFORE TRUNCATE ON %s '
            || 'FOR EACH STATEMENT EXECUTE FUNCTION ops.protect_gold_truncate()',
            relation_name::regclass);
    END LOOP;
END;
$$;

DO $$
DECLARE relation_name text;
BEGIN
    FOREACH relation_name IN ARRAY ARRAY[
        'ops.publication_snapshots', 'gold.monthly_overview',
        'gold.book_monthly_stats', 'gold.book_monthly_features',
        'gold.overview_source_lineage', 'gold.book_source_lineage'] LOOP
        EXECUTE format('CREATE OR REPLACE TRIGGER protect_publication '
            || 'BEFORE INSERT OR UPDATE OR DELETE ON %s '
            || 'FOR EACH ROW EXECUTE FUNCTION ops.protect_gold_publication()',
            relation_name::regclass);
        EXECUTE format('CREATE OR REPLACE TRIGGER protect_truncate '
            || 'BEFORE TRUNCATE ON %s '
            || 'FOR EACH STATEMENT EXECUTE FUNCTION ops.protect_gold_truncate()',
            relation_name::regclass);
    END LOOP;
END;
$$;

CREATE OR REPLACE FUNCTION ops.protect_publication_metadata() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'PUBLISHED' THEN
        RAISE EXCEPTION 'Published Gold metadata is immutable';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER protect_publication_metadata
BEFORE UPDATE OR DELETE ON ops.data_publications
FOR EACH ROW EXECUTE FUNCTION ops.protect_publication_metadata();

CREATE OR REPLACE FUNCTION ops.require_published_pointer() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM ops.data_publications
                   WHERE data_version = NEW.data_version AND status = 'PUBLISHED') THEN
        RAISE EXCEPTION 'Publication pointer requires a PUBLISHED version';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER require_published_pointer
BEFORE INSERT OR UPDATE ON ops.current_publication
FOR EACH ROW EXECUTE FUNCTION ops.require_published_pointer();

CREATE OR REPLACE VIEW gold.current_monthly_overview AS
SELECT m.* FROM gold.monthly_overview m JOIN ops.current_publication p ON p.data_version = m.data_version;
CREATE OR REPLACE VIEW gold.current_book_monthly_stats AS
SELECT m.* FROM gold.book_monthly_stats m JOIN ops.current_publication p ON p.data_version = m.data_version;
CREATE OR REPLACE VIEW gold.current_book_monthly_features AS
SELECT m.* FROM gold.book_monthly_features m JOIN ops.current_publication p ON p.data_version = m.data_version;
