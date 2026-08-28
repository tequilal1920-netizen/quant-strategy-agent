"""Authoritative local execution-panel adapter for allocation v5.1.

This module is intentionally separate from the v5.0 adapter so the existing
shadow implementation remains reproducible.  It reads only the four approved
local execution sleeves and never mutates the warehouse:

* 510300.SH: Chinese equity execution proxy;
* 511010.SH: Chinese government-bond ETF execution proxy;
* 518880.SH: RMB gold ETF execution proxy;
* an equal-weight basket of 159980/159981/159985 as the ex-gold commodity
  execution proxy.

These are execution proxies, not a claim that the unresolved Wind research
total-return series have passed the D3 production gate.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from asset_data_v5 import (
    COMMODITY_EXECUTION_CODES_V5,
    build_execution_commodity_basket_v5,
)


EXECUTION_CODES_V51 = {
    "equity": "510300.SH",
    "bond": "511010.SH",
    "gold": "518880.SH",
}


def _read_code_v51(connection: sqlite3.Connection, code: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT trade_date, close, pct_chg, fund_name FROM etf_ohlcv_daily "
        "WHERE ts_code=? AND close>0 ORDER BY trade_date",
        (code,),
    ).fetchall()
    return [
        {
            "date": str(row[0]),
            "close": float(row[1]),
            "pct_chg": None if row[2] is None else float(row[2]),
            "fund_name": str(row[3] or ""),
            "source_code": code,
            "source_table": "etf_ohlcv_daily",
            "research_only_proxy": True,
        }
        for row in rows
    ]


def load_local_authoritative_execution_prices_v51(
    path: str | Path,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Read the approved local four-sleeve execution panel in read-only mode."""

    database = Path(path).resolve()
    if not database.exists():
        raise FileNotFoundError(f"warehouse_not_found:{database}")
    uri = f"file:{database.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='etf_ohlcv_daily'"
        ).fetchone()
        if table is None:
            raise ValueError("etf_ohlcv_daily_missing")
        equity = _read_code_v51(connection, EXECUTION_CODES_V51["equity"])
        bond = _read_code_v51(connection, EXECUTION_CODES_V51["bond"])
        gold = _read_code_v51(connection, EXECUTION_CODES_V51["gold"])
        component_rows = {
            code: _read_code_v51(connection, code)
            for code in COMMODITY_EXECUTION_CODES_V5
        }
    commodity = build_execution_commodity_basket_v5(component_rows)
    panel = {
        "equity": equity,
        "bond": bond,
        "gold": gold,
        "commodity": commodity,
    }
    coverage = {
        asset: {
            "rows": len(rows),
            "first": rows[0]["date"] if rows else None,
            "last": rows[-1]["date"] if rows else None,
        }
        for asset, rows in panel.items()
    }
    return panel, {
        "status": "research_only_execution_panel",
        "production_ready": False,
        "warehouse": str(database),
        "coverage": coverage,
        "execution_codes": {
            **EXECUTION_CODES_V51,
            "commodity": "BASKET:" + "|".join(COMMODITY_EXECUTION_CODES_V5),
        },
        "commodity_excludes_gold": True,
        "commodity_gold_weight": 0.0,
        "warnings": [
            "execution_ETFs_do_not_replace_D3_Wind_research_total_return_series",
            "bond_ETF_is_an_execution_proxy_not_a_government_bond_wealth_index",
            "gold_ETF_is_an_execution_proxy_for_RMB_gold_spot",
            "commodity_basket_is_short_history_and_not_a_verified_futures_total_return_index",
        ],
    }


__all__ = [
    "EXECUTION_CODES_V51",
    "load_local_authoritative_execution_prices_v51",
]
