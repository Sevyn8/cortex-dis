"""PII detection (AC4): field-name / pattern based, over a caller-supplied mapping."""

from __future__ import annotations

from dis_pii.detectors import detect_pii_columns, is_pii_column


def test_recognises_canonical_pii_field_names() -> None:
    for name in (
        "email",
        "customer_email",
        "phone",
        "phone_number",
        "mobile",
        "msisdn",
        "loyalty_id",
        "customer_pan",
        "aadhaar",
        "aadhar_no",
    ):
        assert is_pii_column(name), name


def test_short_tokens_match_only_as_whole_token() -> None:
    # "pan" is PII as a token, but must not fire on substrings like "company"/"japan".
    assert is_pii_column("pan")
    assert is_pii_column("customer_pan")
    assert not is_pii_column("company")
    assert not is_pii_column("japan_region")


def test_benign_columns_are_not_flagged() -> None:
    for name in ("sku_id", "units_sold", "store_code", "unit_retail_price", "qty"):
        assert not is_pii_column(name), name


def test_detect_reads_mapping_rules_shape() -> None:
    mapping = {
        "source_id": "manual_csv_upload",
        "mapping_rules": {
            "version": 1,
            "rename": {"cust_email": "email", "qty": "units_sold"},
            "cast": {"customer_phone": "str"},
            "normalize": {},
            "derive": {},
        },
    }
    # Both the source name (cust_email, customer_phone) and the canonical target (email)
    # are considered; benign columns are excluded.
    assert detect_pii_columns(mapping) == frozenset({"cust_email", "email", "customer_phone"})


def test_detect_accepts_bare_rules_object() -> None:
    rules = {"rename": {"loyalty_id": "loyalty_id"}}
    assert detect_pii_columns(rules) == frozenset({"loyalty_id"})


def test_no_pii_mapping_returns_empty() -> None:
    mapping = {"mapping_rules": {"rename": {"qty": "units_sold"}, "cast": {"price": "float"}}}
    assert detect_pii_columns(mapping) == frozenset()
