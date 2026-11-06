"""Gold KPI fixtures and PostgreSQL publication integration checks."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from uuid import uuid4

from dotenv import load_dotenv
import psycopg
from psycopg import sql

from modules.bronze_loading import connect_database
from modules.gold_analytics_marts.__main__ import _build
from modules.gold_analytics_marts.digest import output_sha256
from modules.gold_analytics_marts.gate import read_pin, recheck_pin, save_pin
from modules.gold_analytics_marts.kpi_reference import (
    growth_three, rate, rolling_three, share,
)
from modules.gold_analytics_marts.paths import MODELS, ROOT, VERSIONED_MODELS
from modules.gold_analytics_marts.publish import publish


class RollbackFixture(Exception):
    pass


class KpiFixtures(unittest.TestCase):
    def test_rates_and_zero(self):
        self.assertEqual(rate(20, 10), (Decimal(1), "OK"))
        self.assertEqual(rate(5, 0), (None, "NEW_ACTIVITY"))
        self.assertEqual(rate(0, 0), (None, "NO_ACTIVITY"))
        self.assertEqual(rate(5, None), (None, "INSUFFICIENT_HISTORY"))

    def test_rolling_and_growth(self):
        self.assertEqual(rolling_three([10, 20, 30]), (60, Decimal(20), "OK"))
        self.assertEqual(rolling_three([None, 20, 30])[2], "INSUFFICIENT_HISTORY")
        self.assertEqual(growth_three([1, 2, 3, 2, 4, 6]), (Decimal(1), "OK"))
        self.assertEqual(growth_three([0, 0, 0, 0, 0, 5]), (None, "NEW_ACTIVITY"))
        self.assertEqual(growth_three([0, 0, 0, 0, 0, 0]), (None, "NO_ACTIVITY"))
        self.assertEqual(growth_three([0, 0, None, 0, 0, 5])[1], "INSUFFICIENT_HISTORY")

    def test_share(self):
        self.assertEqual(share(25, 100), (Decimal("0.25"), "OK"))
        self.assertEqual(share(0, 0), (None, "ZERO_DENOMINATOR"))
        self.assertEqual(share(0, None), (None, "MISSING_MONTH"))


@unittest.skipUnless(os.environ.get("BOOKSCOPE_TEST_DB") == "1",
                     "set BOOKSCOPE_TEST_DB=1 for PostgreSQL integration")
class GoldPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_dotenv(ROOT / ".env")
        cls.conn = connect_database()
        row = cls.conn.execute("SELECT data_version,output_sha256 FROM ops.data_publications WHERE data_version=(SELECT data_version FROM ops.current_publication)").fetchone()
        if row is None:
            raise AssertionError("Gold publication required for integration tests")
        cls.version, cls.digest = row

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def assertClose(self, actual, expected):
        if expected is None:
            self.assertIsNone(actual)
        else:
            self.assertIsNotNone(actual)
            self.assertLess(abs(Decimal(actual) - Decimal(expected)), Decimal("0.000000000001"))

    def test_dimensions_identity_and_grain(self):
        c = self.conn
        self.assertEqual(c.execute("SELECT count(*) FROM gold.dim_month").fetchone()[0], 36)
        self.assertEqual(c.execute("SELECT count(*) FROM gold.dim_format").fetchone()[0], 2)
        self.assertEqual(c.execute("SELECT count(*) FROM gold.dim_book_record").fetchone()[0], 1000)
        self.assertEqual(c.execute("SELECT count(*) FROM gold.monthly_overview WHERE data_version=%s", (self.version,)).fetchone()[0], 72)
        self.assertEqual(c.execute("SELECT count(*) FROM gold.book_monthly_stats WHERE data_version=%s", (self.version,)).fetchone()[0], 31402)
        self.assertEqual(c.execute("SELECT count(*) FROM gold.book_monthly_features WHERE data_version=%s", (self.version,)).fetchone()[0], 36000)
        self.assertEqual(c.execute("""SELECT count(*) FROM gold.dim_book_record b WHERE
            book_record_key <> encode(sha256(convert_to(jsonb_build_array(
            'cohort_tuple_v1',title_raw,creator_raw,materialtype_cohort,
            checkouttype_raw)::text,'UTF8')),'hex')""").fetchone()[0], 0)
        hashes = c.execute("""SELECT
            encode(sha256(convert_to(jsonb_build_array('cohort_tuple_v1','T',NULL,'EBOOK','O')::text,'UTF8')),'hex'),
            encode(sha256(convert_to(jsonb_build_array('cohort_tuple_v1','T','','EBOOK','O')::text,'UTF8')),'hex'),
            encode(sha256(convert_to(jsonb_build_array('cohort_tuple_v1','T',NULL,'BOOK','O')::text,'UTF8')),'hex'),
            encode(sha256(convert_to(jsonb_build_array('cohort_tuple_v1','T',NULL,'REGPRINT','O')::text,'UTF8')),'hex'),
            encode(sha256(convert_to(jsonb_build_array('cohort_tuple_v1','t',NULL,'EBOOK','O')::text,'UTF8')),'hex')""").fetchone()
        self.assertEqual(len(set(hashes)), 5)

    def test_source_reconciliation(self):
        c = self.conn
        overview = c.execute("""SELECT count(*),sum(checkouts),sum(source_row_count)
            FROM gold.overview_source_lineage WHERE data_version=%s""", (self.version,)).fetchone()
        detail = c.execute("""SELECT count(*),sum(checkouts)
            FROM gold.book_source_lineage WHERE data_version=%s""", (self.version,)).fetchone()
        self.assertEqual(overview, (144, 17042420, 5745441))
        self.assertEqual(detail, (34886, 1495317))
        self.assertEqual(c.execute("""SELECT count(*) FROM (
            SELECT f.month_start,f.format_key FROM gold.monthly_overview f
            JOIN LATERAL (SELECT sum(checkouts) AS checkouts,sum(source_row_count) AS rows,
                count(*) AS groups FROM silver.dq_approved_overview_groups s
                WHERE s.month_start=f.month_start AND s.format_key=f.format_key
                  AND s.snapshot_id=f.source_snapshot_id) x ON true
            WHERE f.data_version=%s AND (f.checkouts::numeric IS DISTINCT FROM x.checkouts
                OR f.source_row_count::numeric IS DISTINCT FROM x.rows
                OR f.source_group_count IS DISTINCT FROM x.groups)
        ) q""", (self.version,)).fetchone()[0], 0)
        self.assertEqual(c.execute("""SELECT count(*) FROM (
            SELECT f.book_record_key FROM gold.book_monthly_stats f
            JOIN LATERAL (SELECT sum(checkouts) AS checkouts,count(*) AS rows
                FROM gold.book_source_lineage l WHERE l.data_version=f.data_version
                AND l.book_record_key=f.book_record_key AND l.month_start=f.month_start
                AND l.format_key=f.format_key) x ON true
            WHERE f.data_version=%s AND (f.checkouts::numeric IS DISTINCT FROM x.checkouts
                OR f.source_record_count IS DISTINCT FROM x.rows)
        ) q""", (self.version,)).fetchone()[0], 0)

    def test_kpis_against_python_reference(self):
        c = self.conn
        rows = c.execute("""SELECT month_start,format_key,checkouts,detail_checkouts,
            active_book_records,format_share,cohort_coverage,mom_change,yoy_change,
            rolling_3m_sum,rolling_3m_avg,metric_status
            FROM gold.monthly_overview WHERE data_version=%s
            ORDER BY format_key,month_start""", (self.version,)).fetchall()
        series = defaultdict(list)
        for row in rows:
            series[row[1]].append(row)
        total_by_month = defaultdict(int)
        detail_by_month = defaultdict(int)
        for row in rows:
            total_by_month[row[0]] += row[2]
            detail_by_month[(row[0], row[1])] = row[3]
        active = {(m, f): n for m, f, n in c.execute("""SELECT month_start,format_key,count(*)
            FROM gold.book_monthly_stats WHERE data_version=%s
            GROUP BY month_start,format_key""", (self.version,)).fetchall()}
        for fmt, values in series.items():
            for i, row in enumerate(values):
                month, _, amount, detail, active_count, fmt_share, coverage, mom, yoy, roll, avg, status = row
                self.assertEqual(active_count, active[(month, fmt)])
                self.assertClose(fmt_share, share(amount, total_by_month[month])[0])
                self.assertClose(coverage, share(detail_by_month[(month, fmt)], amount)[0])
                self.assertClose(mom, rate(amount, values[i-1][2] if i else None)[0])
                self.assertClose(yoy, rate(amount, values[i-12][2] if i >= 12 else None)[0])
                expected_roll = rolling_three([r[2] for r in values[i-2:i+1]] if i >= 2 else [])
                self.assertEqual(roll, expected_roll[0])
                self.assertClose(avg, expected_roll[1])
                self.assertEqual(status["mom_change"], rate(amount, values[i-1][2] if i else None)[1])
        features = c.execute("""SELECT book_record_key,month_start,observed,checkouts,
            rolling_3m_sum,rolling_3m_avg,mom_change,yoy_change,book_growth_3m,
            metric_status FROM gold.book_monthly_features WHERE data_version=%s
            ORDER BY book_record_key,month_start""", (self.version,)).fetchall()
        books = defaultdict(list)
        for row in features:
            books[row[0]].append(row)
        self.assertEqual(len(books), 1000)
        for values in books.values():
            self.assertEqual(len(values), 36)
            for i, row in enumerate(values):
                _, _, observed, amount, roll, avg, mom, yoy, growth, status = row
                self.assertEqual(observed, amount is not None)
                exp_roll = rolling_three([v[3] for v in values[i-2:i+1]] if i >= 2 else [])
                exp_growth = growth_three([v[3] for v in values[i-5:i+1]] if i >= 5 else [])
                self.assertEqual(roll, exp_roll[0])
                self.assertClose(avg, exp_roll[1])
                self.assertClose(mom, rate(amount, values[i-1][3] if i else None)[0])
                self.assertClose(yoy, rate(amount, values[i-12][3] if i >= 12 else None)[0])
                self.assertClose(growth, exp_growth[0])
                self.assertEqual(status["book_growth_3m"], exp_growth[1])

    def test_quality_gate_and_pin_fail_closed(self):
        c = self.conn
        with self.assertRaises(RollbackFixture):
            with c.transaction():
                c.execute("""UPDATE ops.dq_source_revisions SET revision=revision+1
                    WHERE source_table='silver.detail_records'""")
                with self.assertRaisesRegex(ValueError, "DQ gate"):
                    read_pin(c)
                raise RollbackFixture()
        with self.assertRaises(RollbackFixture):
            with c.transaction():
                pin = read_pin(c)
                save_pin(c, pin)
                c.execute("""DELETE FROM ops.gold_build_pins WHERE build_id=%s
                    AND month_start='2023-01-01' AND stream='book_details'""",
                    (pin.build_id,))
                with self.assertRaisesRegex(ValueError, "persisted Gold snapshot pin"):
                    recheck_pin(c, pin)
                raise RollbackFixture()
        self.assertEqual(c.execute("SELECT status FROM ops.dq_gate").fetchone()[0], "PASS")

    def test_new_snapshot_blocks_publication(self):
        c = self.conn
        fake = "gold-gate-fixture-" + uuid4().hex
        with self.assertRaises(RollbackFixture):
            with c.transaction():
                c.execute("""INSERT INTO ops.data_snapshots
                    (snapshot_id,run_id,stream,scope_id,period,status,source_url,
                     dataset_id,manifest_path,manifest_sha256,config_sha256,
                     extractor_version,mapping_version,extracted_at,completed_at,
                     row_count,source_row_count,checkouts)
                    SELECT %s,run_id,stream,scope_id,period,status,source_url,
                     dataset_id,%s,manifest_sha256,config_sha256,
                     extractor_version,mapping_version,now(),now()+interval '1 day',
                     row_count,source_row_count,checkouts
                    FROM ops.data_snapshots WHERE snapshot_id=(
                       SELECT snapshot_id FROM silver.int_snapshot_selection
                       WHERE stream='monthly_overview' ORDER BY month_start LIMIT 1)""",
                    (fake, fake + ".json"))
                with self.assertRaisesRegex(ValueError, "DQ gate"):
                    read_pin(c)
                raise RollbackFixture()
        self.assertEqual(c.execute("SELECT count(*) FROM ops.data_snapshots WHERE snapshot_id=%s", (fake,)).fetchone()[0], 0)

    def test_source_revision_after_pin_blocks(self):
        c = self.conn
        with self.assertRaises(RollbackFixture):
            with c.transaction():
                pin = read_pin(c)
                save_pin(c, pin)
                c.execute("""UPDATE ops.dq_source_revisions SET revision=revision+1
                    WHERE source_table='bronze.checkout_records'""")
                with self.assertRaisesRegex(ValueError, "DQ gate, run, or source revision"):
                    recheck_pin(c, pin)
                raise RollbackFixture()

    def test_version_collision_is_blocked(self):
        c = self.conn
        pointer = c.execute("SELECT data_version FROM ops.current_publication").fetchone()[0]
        with self.assertRaises(RollbackFixture):
            with c.transaction():
                pin = read_pin(c)
                save_pin(c, pin)
                with self.assertRaisesRegex(ValueError, "provenance differs"):
                    publish(c, pin, "gold", self.version, "f" * 64, self.digest)
                self.assertEqual(c.execute("SELECT data_version FROM ops.current_publication").fetchone()[0], pointer)
                raise RollbackFixture()

    def test_published_rows_immutable(self):
        with self.assertRaises(psycopg.Error):
            with self.conn.transaction():
                self.conn.execute("""UPDATE gold.monthly_overview SET checkouts=checkouts+1
                    WHERE data_version=%s AND month_start='2023-01-01'""", (self.version,))

    def test_published_dimensions_immutable(self):
        c = self.conn
        before = c.execute("""SELECT data_version,output_sha256 FROM ops.data_publications
            WHERE status='PUBLISHED' ORDER BY data_version""").fetchall()
        book_key = c.execute("""SELECT book_record_key FROM gold.book_monthly_features
            WHERE data_version=%s LIMIT 1""", (self.version,)).fetchone()[0]
        operations = (
            ("UPDATE gold.dim_month SET year_month=year_month WHERE month_start=%s",
             ("2023-01-01",)),
            ("DELETE FROM gold.dim_month WHERE month_start=%s", ("2023-01-01",)),
            ("UPDATE gold.dim_format SET format_label=format_label WHERE format_key=%s",
             ("EBOOK",)),
            ("DELETE FROM gold.dim_format WHERE format_key=%s", ("EBOOK",)),
            ("UPDATE gold.dim_book_record SET title_display=title_display WHERE book_record_key=%s",
             (book_key,)),
            ("DELETE FROM gold.dim_book_record WHERE book_record_key=%s", (book_key,)),
        )
        for statement, params in operations:
            with self.subTest(statement=statement):
                with self.assertRaisesRegex(psycopg.Error, "Published Gold dimension"):
                    with c.transaction():
                        c.execute(statement, params)
        self.assertEqual(c.execute("""SELECT data_version,output_sha256 FROM
            ops.data_publications WHERE status='PUBLISHED'
            ORDER BY data_version""").fetchall(), before)
        self.assertEqual(c.execute("SELECT count(*) FROM gold.dim_month").fetchone()[0], 36)
        self.assertEqual(c.execute("SELECT count(*) FROM gold.dim_format").fetchone()[0], 2)
        self.assertEqual(c.execute("SELECT count(*) FROM gold.dim_book_record").fetchone()[0], 1000)
        for version, expected_digest in before:
            with self.subTest(data_version=version):
                self.assertEqual(output_sha256(c, "gold", version)[0],
                                 expected_digest)

    def test_unpublished_dimension_inserts_allowed(self):
        c = self.conn
        title = "Gold fixture " + uuid4().hex
        key = c.execute("""SELECT encode(sha256(convert_to(jsonb_build_array(
            'cohort_tuple_v1',%s::text,NULL,'EBOOK','OverDrive')::text,'UTF8')),'hex')""",
            (title,)).fetchone()[0]
        cohort_sha = c.execute("""SELECT cohort_sha256 FROM ops.data_publications
            WHERE data_version=%s""", (self.version,)).fetchone()[0]
        with self.assertRaises(RollbackFixture):
            with c.transaction():
                c.execute("""INSERT INTO gold.dim_month
                    (month_start,year_number,month_number,year_month)
                    VALUES ('2026-01-01',2026,1,'2026-01')""")
                c.execute("""INSERT INTO gold.dim_book_record
                    (book_record_key,identity_version,title_raw,creator_raw,
                     materialtype_cohort,checkouttype_raw,format_key,
                     cohort_sha256,title_display)
                    VALUES (%s,'cohort_tuple_v1',%s,NULL,'EBOOK',
                            'OverDrive','EBOOK',%s,%s)""",
                    (key, title, cohort_sha, title))
                c.execute("""UPDATE gold.dim_book_record SET title_display=%s
                    WHERE book_record_key=%s""", (title + " updated", key))
                self.assertEqual(c.execute("""SELECT title_display FROM gold.dim_book_record
                    WHERE book_record_key=%s""", (key,)).fetchone()[0],
                    title + " updated")
                c.execute("DELETE FROM gold.dim_book_record WHERE book_record_key=%s", (key,))
                c.execute("DELETE FROM gold.dim_month WHERE month_start='2026-01-01'")
                raise RollbackFixture()
        self.assertEqual(c.execute("SELECT count(*) FROM gold.dim_book_record WHERE book_record_key=%s",
                                   (key,)).fetchone()[0], 0)

    def test_zz_concurrent_builds_are_serialized(self):
        before_pointer = self.conn.execute("""SELECT data_version FROM
            ops.current_publication""").fetchone()[0]
        before_versions = self.conn.execute("""SELECT data_version,output_sha256
            FROM ops.data_publications WHERE status='PUBLISHED'
            ORDER BY data_version""").fetchall()
        before_rows = self.conn.execute("""SELECT count(*) FROM
            gold.book_source_lineage WHERE data_version=%s""", (self.version,)).fetchone()[0]
        command = [sys.executable, "-B", "-m", "modules.gold_analytics_marts", "replay"]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT / "src")
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        processes = [subprocess.Popen(command, cwd=ROOT, env=environment,
                                      stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True)
                     for _ in range(2)]
        try:
            results = []
            for process in processes:
                stdout, stderr = process.communicate(timeout=600)
                self.assertEqual(process.returncode, 0, stderr)
                results.append(json.loads(stdout))
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
        self.assertEqual([r["outcome"] for r in results], ["REUSED", "REUSED"])
        self.assertEqual({r["data_version"] for r in results}, {self.version})
        self.assertEqual({r["output_sha256"] for r in results}, {self.digest})
        self.assertEqual(len({r["artifact_dir"] for r in results}), 2)
        build_ids = [r["build_id"] for r in results]
        timings = self.conn.execute("""SELECT build_id::text,started_at,finished_at,
            dbt_results->>'invocation_id',dbt_results->>'artifact_dir',
            dbt_results->>'run_results_sha256'
            FROM ops.gold_builds WHERE build_id::text=ANY(%s)
            ORDER BY started_at""", (build_ids,)).fetchall()
        self.assertEqual(len(timings), 2)
        self.assertGreaterEqual(timings[1][1], timings[0][2])
        self.assertNotEqual(timings[0][3], timings[1][3])
        for build_id, _, _, invocation, path, digest in timings:
            self.assertEqual(Path(path), Path(next(r["artifact_dir"] for r in results
                                                   if r["build_id"] == build_id)))
            artifact = Path(path) / "run_results.json"
            self.assertTrue(artifact.is_file())
            self.assertEqual(sha256(artifact.read_bytes()).hexdigest(), digest)
            self.assertEqual(json.loads(artifact.read_text(encoding="utf-8"))
                             ["metadata"]["invocation_id"], invocation)
        self.assertEqual(self.conn.execute("""SELECT data_version FROM
            ops.current_publication""").fetchone()[0], before_pointer)
        self.assertEqual(self.conn.execute("""SELECT data_version,output_sha256
            FROM ops.data_publications WHERE status='PUBLISHED'
            ORDER BY data_version""").fetchall(), before_versions)
        self.assertEqual(self.conn.execute("""SELECT count(*) FROM gold.book_source_lineage
            WHERE data_version=%s""", (self.version,)).fetchone()[0], before_rows)

    def test_publication_failure_rolls_back(self):
        c = self.conn
        fake_version = sha256(uuid4().bytes).hexdigest()
        old_pointer = c.execute("SELECT data_version FROM ops.current_publication").fetchone()[0]
        with self.assertRaises(RollbackFixture):
            with c.transaction():
                pin = read_pin(c)
                save_pin(c, pin)
                schema = "gold_fixture_" + pin.build_id.hex
                c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
                for name in MODELS:
                    tail = sql.SQL(" WHERE data_version=%s") if name in VERSIONED_MODELS else sql.SQL("")
                    query = sql.SQL("CREATE TABLE {}.{} AS SELECT * FROM gold.{}").format(
                        sql.Identifier(schema), sql.Identifier(name), sql.Identifier(name)) + tail
                    c.execute(query, (self.version,) if name in VERSIONED_MODELS else ())
                output_hash, _ = output_sha256(c, schema)
                self.assertEqual(output_hash, self.digest)
                for failpoint in ("after_storage", "before_pointer"):
                    with self.assertRaisesRegex(RuntimeError, "injected failure"):
                        publish(c, pin, schema, fake_version, "a" * 64,
                                output_hash, failpoint=failpoint)
                    self.assertEqual(c.execute("SELECT data_version FROM ops.current_publication").fetchone()[0], old_pointer)
                    self.assertEqual(c.execute("SELECT count(*) FROM ops.data_publications WHERE data_version=%s", (fake_version,)).fetchone()[0], 0)
                raise RollbackFixture()
        self.assertEqual(c.execute("SELECT data_version FROM ops.current_publication").fetchone()[0], old_pointer)

    def test_z_replay_idempotent(self):
        before = self.conn.execute("""SELECT count(*) FROM gold.book_source_lineage
            WHERE data_version=%s""", (self.version,)).fetchone()[0]
        result = _build(self.conn, replay=True)
        self.assertEqual(result["outcome"], "REUSED")
        self.assertEqual(result["data_version"], self.version)
        self.assertEqual(result["output_sha256"], self.digest)
        self.assertEqual(self.conn.execute("""SELECT count(*) FROM gold.book_source_lineage
            WHERE data_version=%s""", (self.version,)).fetchone()[0], before)
        self.assertEqual(self.conn.execute("SELECT data_version FROM ops.current_publication").fetchone()[0], self.version)
