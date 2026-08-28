from __future__ import annotations

import sqlite3

import pytest

import backfill_asset_allocation_v51 as wrapper


def _row(code: str, fund_type: str, name: str) -> dict[str, object]:
    return {
        "trade_date": "20200102",
        "ts_code": code,
        "fund_name": name,
        "open": 9.9,
        "high": 10.1,
        "low": 9.8,
        "close": 10.0,
        "pct_chg": 0.2,
        "vol": 100.0,
        "amount": 1000.0,
        "fund_type": fund_type,
    }


@pytest.mark.parametrize(
    "code,fund_type,name",
    [
        ("510300.SH", "股票型", "沪深300ETF华泰柏瑞"),
        ("511010.SH", "CEF", "国债ETF国泰"),
        ("511010.SH", "债券型", "国债ETF国泰"),
        ("518880.SH", "商品型", "黄金ETF华安"),
        ("159980.SZ", "商品型", "有色ETF大成"),
    ],
)
def test_historical_provider_types_are_code_and_name_validated(
    code: str, fund_type: str, name: str
) -> None:
    normalised = wrapper._normalise_v51(_row(code, fund_type, name))
    assert normalised["ts_code"] == code
    assert normalised["fund_type"] == "ETF"


def test_wrong_security_name_is_blocked() -> None:
    with pytest.raises(wrapper.base.BackfillError, match="security-name validation failed"):
        wrapper._normalise_v51(_row("511010.SH", "CEF", "可转债ETF"))


def test_unapproved_type_is_blocked() -> None:
    with pytest.raises(wrapper.base.BackfillError, match="unapproved fund_type"):
        wrapper._normalise_v51(_row("518880.SH", "股票型", "黄金ETF华安"))


def test_patch_context_restores_base_functions() -> None:
    original = (wrapper.base._fetch_source_rows, wrapper.base._duplicate_count)
    with wrapper._patched_source_admission():
        assert wrapper.base._fetch_source_rows is wrapper._fetch_source_rows_v51
    assert (wrapper.base._fetch_source_rows, wrapper.base._duplicate_count) == original
