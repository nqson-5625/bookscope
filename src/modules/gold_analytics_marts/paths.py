"""Project paths and the immutable Gold model inventory."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DBT_PROJECT = ROOT / "dbt_bookscope"
MIGRATION = ROOT / "sql/gold/001_publications.sql"
COHORT = ROOT / "data/raw/seattle_checkouts/cohort_2023.jsonl"

MODELS = (
    "dim_month", "dim_format", "dim_book_record", "monthly_overview",
    "book_monthly_stats", "book_monthly_features",
    "overview_source_lineage", "book_source_lineage",
)
VERSIONED_MODELS = MODELS[3:]
GOLD_CONTRACT_VERSION = "v1_2026_11_04"
IDENTITY_VERSION = "cohort_tuple_v1"
BUILD_LOCK_ID = 87261105
# Release inventory: any test change must update this digest deliberately.
GOLD_DATA_TEST_IDS_SHA256 = "56b9a8842bedffd1c4e65abb7620fff8fae50ff84039e1cd210dcb825b63cdcc"
GOLD_UNIT_TEST_IDS = frozenset({
    "unit_test.dbt_bookscope.book_monthly_features.book_features_missing_vs_zero",
})
