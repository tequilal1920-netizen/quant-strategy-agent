import math

import numpy as np
import pandas as pd

from model.portfolio_optimization import broad_index_timing_research as timing


def _synthetic_raw(n: int = 900) -> pd.DataFrame:
    dates = pd.bdate_range("2020-01-01", periods=n)
    rng = np.random.default_rng(7)
    drift = np.where(np.arange(n) < n * 0.55, 0.0004, -0.0001)
    close = 100.0 * np.cumprod(1.0 + drift + rng.normal(0.0, 0.010, n))
    return pd.DataFrame(
        {
            "trade_date": dates.strftime("%Y%m%d"),
            "date": dates,
            "open": close * (1.0 + rng.normal(0.0, 0.001, n)),
            "high": close * 1.012,
            "low": close * 0.988,
            "close": close,
            "pre_close": pd.Series(close).shift(1).fillna(close[0]),
            "amount": np.linspace(1.0e6, 2.2e6, n) * (1.0 + rng.normal(0.0, 0.04, n)),
            "ret": pd.Series(close).pct_change().fillna(0.0),
        }
    )


def _synthetic_context(raw: pd.DataFrame) -> pd.DataFrame:
    n = len(raw)
    x = np.linspace(0.0, 1.0, n)
    return pd.DataFrame(
        {
            "trade_date": raw["trade_date"],
            "ctx_turnover_pct252": np.clip(0.35 + 0.35 * x, 0, 1),
            "ctx_turnover_z120": np.sin(x * 6.0),
            "ctx_volume_ratio_pct252": np.clip(0.45 + 0.20 * np.sin(x * 8.0), 0, 1),
            "ctx_moneyflow_z120": np.cos(x * 5.0),
            "ctx_large_moneyflow_z120": np.sin(x * 4.0),
            "ctx_market_value_pct756": np.clip(0.65 - 0.25 * x, 0, 1),
            "ctx_market_pb_guard756": np.clip(0.60 - 0.20 * x, 0, 1),
            "pmi_composite": 50.0 + 1.5 * np.sin(x * 3.0),
            "pmi_manufacturing": 49.8 + 1.2 * np.sin(x * 3.0),
            "m1_yoy": 4.0 + 2.0 * x,
            "m2_yoy": 7.0 + 0.5 * x,
            "sf_inc_month": 10000.0 + 3000.0 * x,
            "sf_stock_endval": 300000.0 + 50000.0 * x,
            "cpi_national_yoy": 1.5 + 0.3 * np.sin(x * 2.0),
            "ppi_yoy": 0.5 + 0.8 * np.cos(x * 2.0),
        }
    )


def test_four_factor_framework_and_five_bucket_contract():
    raw = _synthetic_raw()
    features, groups = timing._prepare_features(raw, None, _synthetic_context(raw))
    assert set(["macro", "price_volume", "sentiment", "valuation", "risk"]).issubset(groups)
    assert len(groups["macro"]) >= 5
    assert len(groups["price_volume"]) >= 10
    assert len(groups["sentiment"]) >= 5
    assert len(groups["valuation"]) >= 5
    diagnostics = timing._factor_test_summary(features, groups)
    assert diagnostics["factor_count"] >= 20
    assert {row["family"] for row in diagnostics["families"]} == {"macro", "price_volume", "sentiment", "valuation"}
    signal = timing._four_dimension_fusion_candidate(features, groups, timing.FUSION_PROFILES[0])
    buckets = set(signal["bucket_position"].dropna().round(2).unique())
    assert buckets.issubset({0.0, 0.25, 0.5, 0.75, 1.0})
    assert signal["attack_score"].between(0, 1).all()
    assert signal["defense_score"].between(0, 1).all()
    bt = timing._backtest(signal)
    metrics = timing._metrics(bt)
    assert math.isfinite(metrics["strategy_sharpe"])
    assert math.isfinite(metrics["excess_ann"])
