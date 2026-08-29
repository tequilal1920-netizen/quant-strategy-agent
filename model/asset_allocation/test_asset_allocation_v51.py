from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np

import asset_allocation_v5 as base
from asset_allocation_v51 import build_snapshot_v51, research_shadow_config_v51
from asset_data_authoritative_v51 import (
    EXECUTION_CODES_V51,
    load_local_authoritative_execution_prices_v51,
)
from asset_data_v5 import COMMODITY_EXECUTION_CODES_V5


def _prices(months: int = 84) -> dict[str, list[dict[str, object]]]:
    output: dict[str, list[dict[str, object]]] = {}
    for position, asset in enumerate(("equity", "bond", "gold", "commodity")):
        level = 100.0
        rows = []
        for month in range(months):
            year = 2019 + month // 12
            number = month % 12 + 1
            shock = 0.004 + 0.002 * np.sin(month / (4.0 + position))
            level *= 1.0 + shock + 0.0005 * position
            rows.append({"date": f"{year:04d}{number:02d}28", "close": level})
        output[asset] = rows
    return output


def test_v51_restores_base_call_sites_and_serialises_governed_evidence() -> None:
    original = (
        base.build_macro_cycle_probabilities_v5,
        base.build_pring_market_probabilities_v5,
        base.merge_cycle_history_v5,
        base.forecast_cycle_views_v5,
        base._allocate_at_v5,
    )
    macro = [
        {
            "month": f"{2018 + index // 12:04d}{index % 12 + 1:02d}",
            "pmi_manufacturing": 50.0 + np.sin(index / 5.0),
            "cpi_national_yoy": 2.0 + 0.2 * np.sin(index / 8.0),
            "ppi_yoy": 1.0 + 0.4 * np.cos(index / 7.0),
            "sf_stock_yoy": 10.0 + np.sin(index / 9.0),
            "m2_yoy": 9.0 + np.cos(index / 10.0),
            "equity_risk_premium": 3.0 + np.sin(index / 6.0),
            "stock_bond_relative_momentum": np.sin(index / 4.0),
            "industrial_finished_goods_inventory_yoy": 5.0 + np.cos(index / 8.0),
            "industrial_revenue_yoy": 7.0 + np.sin(index / 8.0),
            "manufacturing_fai_yoy": 6.0 + np.sin(index / 10.0),
            "enterprise_medium_long_loan_yoy": 8.0 + np.cos(index / 10.0),
            "capacity_utilization": 75.0 + np.sin(index / 12.0),
            "industrial_profit_yoy": 7.0 + np.cos(index / 9.0),
            "observation_period": f"{2018 + index // 12:04d}{index % 12 + 1:02d}",
            "available_time": f"{2018 + index // 12:04d}{index % 12 + 1:02d}",
            "vintage": "first_release",
            "_pit_verified": True,
        }
        for index in range(96)
    ]
    config = research_shadow_config_v51("201901", "202512")
    snapshot = build_snapshot_v51(macro, _prices(), config=config)
    assert snapshot["schema_version"] == "5.1"
    assert snapshot["engine_version"].endswith("v5.1-governed-shadow")
    assert snapshot["optimization"]["cycle_views"]["view_labels"] == [
        "equity-minus-bond",
        "commodity-minus-bond",
        "gold-minus-bond",
    ]
    assert len(snapshot["optimization"]["cycle_views"]["omega"]) == 3
    assert snapshot["cycle_factor_availability"]["factor_schema_version"] == "5.1"
    assert (
        base.build_macro_cycle_probabilities_v5,
        base.build_pring_market_probabilities_v5,
        base.merge_cycle_history_v5,
        base.forecast_cycle_views_v5,
        base._allocate_at_v5,
    ) == original


def test_v51_local_loader_uses_518880_and_excludes_gold_from_commodity(tmp_path: Path) -> None:
    database = tmp_path / "warehouse.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE etf_ohlcv_daily (trade_date TEXT, ts_code TEXT, close REAL, pct_chg REAL, fund_name TEXT)"
        )
        codes = list(EXECUTION_CODES_V51.values()) + list(COMMODITY_EXECUTION_CODES_V5)
        for day in range(30):
            for code_index, code in enumerate(codes):
                connection.execute(
                    "INSERT INTO etf_ohlcv_daily VALUES (?, ?, ?, ?, ?)",
                    (f"202001{day + 1:02d}", code, 100.0 + day + code_index, 0.1, code),
                )
        connection.commit()
    panel, lineage = load_local_authoritative_execution_prices_v51(database)
    assert panel["gold"][0]["source_code"] == "518880.SH"
    assert lineage["execution_codes"]["gold"] == "518880.SH"
    assert lineage["commodity_gold_weight"] == 0.0
    assert panel["commodity"][0]["gold_weight"] == 0.0
