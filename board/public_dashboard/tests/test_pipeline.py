from __future__ import annotations

import copy
import json
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import pipeline  # noqa: E402


NOW = datetime(2026, 7, 11, 8, 0, tzinfo=timezone.utc)


def _catalog() -> dict:
    boundaries = [
        (87, "宏观"),
        (151, "全球市场"),
        (228, "申万行业"),
        (266, "商品"),
        (328, "个股"),
        (367, "新闻事件"),
    ]
    rows = []
    for number in range(1, 368):
        module_name = next(name for boundary, name in boundaries if number <= boundary)
        rows.append({
            "id": f"M{number:04d}",
            "module": module_name,
            "variable": f"fixture_variable_{number:04d}",
            "metric": f"fixture metric {number:04d}",
        })
    return {"schema_version": "fixture-1", "as_of": "2026-07-11", "rows": rows}


def _write_catalog(tmp_path: Path, rows: int = 367) -> Path:
    catalog = _catalog()
    catalog["rows"] = catalog["rows"][:rows]
    path = tmp_path / "indicator_catalog.json"
    path.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
    return path


def _market_frame(scale: float = 1.0) -> pd.DataFrame:
    dates = pd.date_range("2025-05-01", "2026-07-10", freq="B")
    values = [(100.0 + index * 0.2 + (index % 7) * 0.03) * scale for index in range(len(dates))]
    return pd.DataFrame({
        "date": dates,
        "open": [value - 0.1 * scale for value in values],
        "high": [value + 0.5 * scale for value in values],
        "low": [value - 0.5 * scale for value in values],
        "close": values,
    })


class FixtureClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, endpoint: str, **kwargs):
        self.calls.append((endpoint, kwargs))
        if endpoint == "macro_china_gdp":
            return pd.DataFrame({
                "季度": ["2025年第4季度", "2026年第1季度"],
                "国内生产总值-绝对值": [1_200_000.0, 320_000.0],
                "国内生产总值-同比增长": [5.0, 5.2],
            })
        if endpoint == "macro_china_pmi":
            return pd.DataFrame({"月份": ["2026年05月份", "2026年06月份"], "制造业-指数": [49.8, 50.1], "非制造业-指数": [50.3, 50.5]})
        if endpoint == "macro_china_cpi":
            return pd.DataFrame({"月份": ["2026年05月份", "2026年06月份"], "全国-同比增长": [0.3, 0.5]})
        if endpoint == "macro_china_ppi":
            return pd.DataFrame({"月份": ["2026年05月份", "2026年06月份"], "当月同比增长": [-1.1, -0.8]})
        if endpoint == "macro_china_money_supply":
            return pd.DataFrame({"月份": ["2026年05月份", "2026年06月份"], "货币和准货币(M2)-同比增长": [7.2, 7.5]})
        if endpoint == "macro_china_shibor_all":
            return pd.DataFrame({"日期": ["2026-07-09", "2026-07-10"], "O/N-定价": [1.42, 1.45]})
        if endpoint in {"index_us_stock_sina", "index_global_hist_sina", "stock_hk_index_daily_sina", "stock_zh_index_daily"}:
            symbol = str(kwargs.get("symbol", "x"))
            scale = 1.0 + (sum(ord(char) for char in symbol) % 9) / 10.0
            return _market_frame(scale)
        if endpoint == "index_hist_sw":
            frame = _market_frame(1.0)
            return frame.rename(columns={"date": "日期", "close": "收盘"})
        if endpoint == "futures_spot_price":
            return pd.DataFrame({
                "symbol": ["AU", "CU", "RB"],
                "sp": [820.0, 80_000.0, 3_200.0],
                "near_symbol": ["AU2608", "CU2608", "RB2608"],
                "near_price": [818.0, 79_500.0, 3_180.0],
                "dom_symbol": ["AU2610", "CU2609", "RB2610"],
                "dom_price": [825.0, 80_100.0, 3_230.0],
                "near_basis": [-2.0, -500.0, -20.0],
                "dom_basis": [5.0, 100.0, 30.0],
                "near_basis_rate": [-0.24, -0.63, -0.63],
                "dom_basis_rate": [0.61, 0.13, 0.94],
                "date": ["20260710"] * 3,
            })
        if endpoint == "futures_main_sina":
            frame = _market_frame(10.0)
            return frame.rename(columns={"date": "日期", "close": "收盘价"})
        if endpoint == "stock_zh_a_spot_em":
            return pd.DataFrame({
                "代码": ["000001", "600519", "300750"],
                "名称": ["平安银行", "贵州茅台", "宁德时代"],
                "最新价": [12.3, 1_550.0, 280.0],
                "涨跌幅": [0.5, -0.2, 1.1],
                "成交量": [1_000_000.0, 120_000.0, 800_000.0],
                "成交额": [12_300_000.0, 186_000_000.0, 224_000_000.0],
                "换手率": [0.8, 0.3, 1.5],
                "市盈率-动态": [6.2, 22.0, 18.5],
                "市净率": [0.7, 7.5, 4.1],
            })
        if endpoint == "stock_zh_a_hist":
            frame = _market_frame(0.1)
            return frame.rename(columns={"date": "日期", "close": "收盘"})
        if endpoint == "stock_news_em":
            symbol = kwargs["symbol"]
            return pd.DataFrame({
                "新闻标题": [f"{symbol} 经营动态", f"{symbol} 行业信息"],
                "发布时间": ["2026-07-10 10:00:00", "2026-07-10 09:00:00"],
                "文章来源": ["公开财经源", "公开财经源"],
                "新闻链接": [f"https://example.test/{symbol}/1", f"https://example.test/{symbol}/2"],
            })
        if endpoint == "news_economic_baidu":
            return pd.DataFrame({"时间": ["2026-07-11 09:30"], "地区": ["中国"], "事件": ["公开经济日历事件"]})
        if endpoint == "stock_individual_notice_report":
            return pd.DataFrame({
                "代码": ["000001"], "名称": ["平安银行"], "公告标题": ["董事会决议公告"],
                "公告类型": ["公司治理"], "公告日期": ["2026-07-10"], "网址": ["https://example.test/notice/1"],
            })
        raise AssertionError(f"unexpected endpoint: {endpoint}")


class FallbackStockClient(FixtureClient):
    def call(self, endpoint: str, **kwargs):
        if endpoint in {"stock_zh_a_spot_em", "stock_zh_a_hist"}:
            self.calls.append((endpoint, kwargs))
            raise ConnectionError("primary stock source unavailable")
        if endpoint == "stock_zh_a_spot":
            self.calls.append((endpoint, kwargs))
            return pd.DataFrame({
                "代码": ["sz000001", "sh600519", "sz300750"],
                "名称": ["平安银行", "贵州茅台", "宁德时代"],
                "最新价": [12.3, 1_550.0, 280.0],
                "涨跌幅": [0.5, -0.2, 1.1],
                "成交量": [1_000_000.0, 120_000.0, 800_000.0],
                "成交额": [12_300_000.0, 186_000_000.0, 224_000_000.0],
                "时间戳": ["15:30:01", "15:30:01", "15:30:01"],
            })
        if endpoint == "stock_zh_a_daily":
            self.calls.append((endpoint, kwargs))
            frame = _market_frame(0.1)
            frame["turnover"] = 0.012
            return frame
        return super().call(endpoint, **kwargs)


class SinaSpotOnlyStockClient(FallbackStockClient):
    def call(self, endpoint: str, **kwargs):
        if endpoint in {"stock_zh_a_hist", "stock_zh_a_daily", "stock_zh_a_hist_tx"}:
            self.calls.append((endpoint, kwargs))
            raise ConnectionError("every history source unavailable")
        return super().call(endpoint, **kwargs)


class SchemaFallbackStockClient(FallbackStockClient):
    def call(self, endpoint: str, **kwargs):
        if endpoint == "stock_zh_a_spot_em":
            self.calls.append((endpoint, kwargs))
            return pd.DataFrame({"unrecognised_code": ["000001"], "price": [12.3]})
        if endpoint in {"stock_zh_a_hist", "stock_zh_a_daily"}:
            self.calls.append((endpoint, kwargs))
            return pd.DataFrame({"date": ["2026-07-10"], "pre_close": [12.3]})
        if endpoint == "stock_zh_a_hist_tx":
            self.calls.append((endpoint, kwargs))
            return _market_frame(0.1)
        return super().call(endpoint, **kwargs)


class SpotOnlyStockClient(FixtureClient):
    def call(self, endpoint: str, **kwargs):
        if endpoint in {"stock_zh_a_hist", "stock_zh_a_daily", "stock_zh_a_hist_tx"}:
            self.calls.append((endpoint, kwargs))
            raise ConnectionError("every history source unavailable")
        return super().call(endpoint, **kwargs)


class HistoryOnlyStockClient(FixtureClient):
    def call(self, endpoint: str, **kwargs):
        if endpoint in {"stock_zh_a_spot_em", "stock_zh_a_spot"}:
            self.calls.append((endpoint, kwargs))
            raise ConnectionError("every spot source unavailable")
        return super().call(endpoint, **kwargs)


class TencentOnlyStockClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, endpoint: str, **kwargs):
        self.calls.append((endpoint, kwargs))
        if endpoint in {"stock_zh_a_spot_em", "stock_zh_a_spot", "stock_zh_a_hist", "stock_zh_a_daily"}:
            raise ConnectionError("upstream unavailable")
        if endpoint == "stock_zh_a_hist_tx":
            frame = _market_frame(0.1)
            frame["amount"] = 1_234.0
            return frame
        raise AssertionError(f"unexpected endpoint: {endpoint}")


class FailClient:
    def __init__(self) -> None:
        self.calls = 0

    def call(self, endpoint: str, **kwargs):
        del endpoint, kwargs
        self.calls += 1
        raise ConnectionError("fixture outage")


class BombClient:
    def call(self, endpoint: str, **kwargs):
        del endpoint, kwargs
        raise AssertionError("fresh TTL cache should prevent every source call")


def _run(tmp_path: Path, client, *, now: datetime = NOW, force: bool = True):
    catalog_path = _write_catalog(tmp_path)
    return pipeline.run_update(
        project_dir=PROJECT_DIR,
        data_dir=tmp_path / "data",
        catalog_path=catalog_path,
        force=force,
        client=client,
        now=now,
        environ={},
    )


def test_fixture_refresh_satisfies_contract_and_publishes_all_367_rows(tmp_path: Path):
    client = FixtureClient()
    snapshot = _run(tmp_path, client)

    pipeline.validate_snapshot(snapshot)
    assert snapshot["status"] == "partial"
    assert list(snapshot["modules"]) == list(pipeline.MODULE_KEYS)
    assert snapshot["modules"]["sw_industries"]["status"] == "ok"
    assert all(snapshot["modules"][key]["status"] in pipeline.VALID_STATUSES for key in pipeline.MODULE_KEYS)
    sw_calls = [kwargs["symbol"] for endpoint, kwargs in client.calls if endpoint == "index_hist_sw"]
    assert len(sw_calls) == 31
    assert set(sw_calls) == set(pipeline.SW_L1_INDUSTRIES)
    assert len(snapshot["modules"]["sw_industries"]["tables"][0]["rows"]) == 31
    assert all(isinstance(snapshot["modules"][key]["series"], list) for key in pipeline.MODULE_KEYS)
    assert all(snapshot["modules"][key]["as_of"] for key in pipeline.MODULE_KEYS)
    assert all(
        item["status"] in {"live", "stale", "unavailable"}
        for module in snapshot["modules"].values()
        for item in module["series"]
    )
    assert len(snapshot["catalog"]["rows"]) == 367
    assert len(snapshot["catalog_status"]) == 367
    statuses = {row["status"] for row in snapshot["catalog_status"]}
    assert "live" in statuses
    assert "metadata_only" in statuses
    assert snapshot["summary"]["fabricated_observations"] == 0
    assert (tmp_path / "data" / "dashboard_snapshot.json").exists()
    assert (tmp_path / "data" / "indicator_catalog.json").exists()
    assert len(list((tmp_path / "data" / "history").glob("snapshot_*.json"))) == 1
    assert "index_realtime_sw" not in {endpoint for endpoint, _ in client.calls}
    assert all(chart["status"] == "ok" for module in snapshot["modules"].values() for chart in module["charts"])
    assert all(table["status"] == "ok" for module in snapshot["modules"].values() for table in module["tables"])
    assert all(table["as_of"] for module in snapshot["modules"].values() for table in module["tables"])
    commodity_basis = next(
        table for table in snapshot["modules"]["commodities"]["tables"]
        if table["id"] == "commodity_basis_snapshot"
    )
    assert commodity_basis["source"] == "AKShare futures_spot_price"
    stock_close = next(
        item for item in snapshot["modules"]["stock"]["series"]
        if item["id"] == "stock_000001_qfq_close"
    )
    assert set(stock_close["data"][-1]) == {"date", "value", "open", "high", "low", "close"}
    assert stock_close["data"][-1]["value"] == stock_close["data"][-1]["close"]


def test_public_series_ohlc_validator_rejects_partial_and_unknown_keys(tmp_path: Path):
    snapshot = _run(tmp_path, FixtureClient())
    point = next(
        item for item in snapshot["modules"]["stock"]["series"]
        if item["id"] == "stock_000001_qfq_close"
    )["data"][0]
    assert set(point) == {"date", "value", "open", "high", "low", "close"}
    pipeline.validate_snapshot(snapshot)

    partial = copy.deepcopy(snapshot)
    partial_point = next(
        item for item in partial["modules"]["stock"]["series"]
        if item["id"] == "stock_000001_qfq_close"
    )["data"][0]
    partial_point.pop("low")
    with pytest.raises(pipeline.PipelineError, match="malformed public series point"):
        pipeline.validate_snapshot(partial)

    unknown = copy.deepcopy(snapshot)
    unknown_point = next(
        item for item in unknown["modules"]["stock"]["series"]
        if item["id"] == "stock_000001_qfq_close"
    )["data"][0]
    unknown_point["vendor_flag"] = 1
    with pytest.raises(pipeline.PipelineError, match="malformed public series point"):
        pipeline.validate_snapshot(unknown)

    non_numeric = copy.deepcopy(snapshot)
    non_numeric_point = next(
        item for item in non_numeric["modules"]["stock"]["series"]
        if item["id"] == "stock_000001_qfq_close"
    )["data"][0]
    non_numeric_point["open"] = True
    with pytest.raises(pipeline.PipelineError, match="invalid public series OHLC point"):
        pipeline.validate_snapshot(non_numeric)


def test_series_item_keeps_close_when_upstream_ohlc_is_incomplete():
    item = pipeline._series_item(
        identifier="stock_fixture_qfq_close",
        label="fixture",
        submodule="stock",
        unit="元",
        frequency="日",
        source="fixture",
        points=[{
            "date": "2026-07-10",
            "value": 12.3,
            "open": 12.0,
            "high": 12.5,
            "close": 12.3,
        }],
    )
    assert item["status"] == "live"
    assert item["data"] == [{"date": "2026-07-10", "value": 12.3}]


def test_finalize_module_backfills_chart_and_table_metadata():
    module = pipeline._module("stock")
    module["charts"] = [{
        "id": "chart",
        "title": "chart",
        "kind": "line",
        "unit": "元",
        "series": [{
            "name": "fixture",
            "data": [
                {"date": "2026-07-08", "value": 1.0},
                {"date": "2026-07-10", "value": 1.2},
            ],
        }],
    }]
    module["tables"] = [{
        "id": "table",
        "title": "table",
        "columns": ["date", "source"],
        "rows": [
            {"date": "2026-07-09", "source": "source-a"},
            {"published_at": "2026-07-11 08:00:00", "文章来源": "source-b"},
            {"time": "15:30:00", "source": "source-c"},
        ],
    }]
    result = pipeline._finalize_module(module)
    assert result["charts"][0]["as_of"] == "2026-07-10"
    assert result["tables"][0]["as_of"] == "2026-07-11"
    assert result["tables"][0]["source"] == "source-a / source-b / source-c"
    assert result["as_of"] == "2026-07-11"


def test_explicit_iso_day_is_not_collapsed_to_month_start():
    assert pipeline._parse_date("2026-07-10") == date(2026, 7, 10)
    assert pipeline._parse_date("2026/07/10") == date(2026, 7, 10)
    assert pipeline._parse_date("2026Q2") == date(2026, 6, 1)


def test_customs_amounts_are_scaled_to_hundred_million_usd_and_balance_change_is_percent():
    class CustomsClient:
        def call(self, endpoint: str, **kwargs):
            del kwargs
            assert endpoint == "macro_china_hgjck"
            return pd.DataFrame({
                "月份": ["2025年04月份", "2025年05月份"],
                "当月出口额-金额": [300_000_000.0, 315_635_924.659],
                "当月进口额-金额": [200_000_000.0, 212_921_086.862],
            })

    rows = [
        {"id": "M0068", "module": "宏观", "submodule": "外贸", "metric": "出口额", "variable": "cn_export_value", "frequency": "月", "primary_source": "AKShare", "api_field": "macro_china_hgjck/当月出口额-金额", "unit": "亿美元"},
        {"id": "M0070", "module": "宏观", "submodule": "外贸", "metric": "进口额", "variable": "cn_import_value", "frequency": "月", "primary_source": "AKShare", "api_field": "macro_china_hgjck/当月进口额-金额", "unit": "亿美元"},
        {"id": "M0072", "module": "宏观", "submodule": "外贸", "metric": "贸易差额", "variable": "cn_trade_balance", "frequency": "月", "primary_source": "本地派生", "api_field": "出口额-进口额", "unit": "亿美元"},
    ]
    module = pipeline.fetch_macro(CustomsClient(), NOW, rows)
    series = {item["id"]: item for item in module["series"]}
    assert series["cn_export_value"]["data"][-1]["value"] == pytest.approx(3156.35924659)
    assert series["cn_import_value"]["data"][-1]["value"] == pytest.approx(2129.21086862)
    assert series["cn_trade_balance"]["data"][-1]["value"] == pytest.approx(1027.14837797)
    assert "按1e5换算为亿美元" in series["cn_export_value"]["source"]
    balance_kpi = next(item for item in module["kpis"] if item["id"] == "cn_trade_balance")
    assert balance_kpi["change"] == pytest.approx(2.714837797)
    assert balance_kpi["display"] == "1,027.15亿美元"


def test_series_level_stale_fallback_preserves_healthy_sibling():
    previous = {
        "status": "ok",
        "series": [
            {"id": "healthy", "status": "live", "data": [{"date": "2026-07-09", "value": 1.0}]},
            {"id": "failed", "status": "live", "data": [{"date": "2026-07-09", "value": 2.0}]},
        ],
        "alerts": [],
    }
    current = {
        "status": "partial",
        "series": [
            {"id": "healthy", "status": "live", "data": [{"date": "2026-07-10", "value": 1.1}]},
            {"id": "failed", "status": "unavailable", "data": []},
        ],
        "alerts": [],
    }
    merged = pipeline._merge_series_stale(current, previous)
    by_id = {item["id"]: item for item in merged["series"]}
    assert by_id["healthy"]["status"] == "live"
    assert by_id["healthy"]["data"][-1]["date"] == "2026-07-10"
    assert by_id["failed"]["status"] == "stale"
    assert by_id["failed"]["data"][-1]["value"] == 2.0
    assert merged["status"] == "partial"


def test_runtime_catalog_status_keeps_explicit_unavailable_and_live_priority():
    runtime = pipeline._runtime_ids_from_module({
        "status": "partial",
        "series": [
            {"catalog_id": "M0001", "status": "unavailable", "data": []},
            {"catalog_id": "M0002", "status": "unavailable", "data": []},
            {"catalog_id": "M0002", "status": "live", "data": [{"date": "2026-07-10", "value": 1.0}]},
        ],
        "kpis": [],
    })
    assert runtime == {"M0001": "unavailable", "M0002": "live"}


def test_news_only_refresh_keeps_other_modules_byte_equivalent(tmp_path: Path):
    first = _run(tmp_path, FixtureClient(), now=NOW, force=True)
    client = FixtureClient()
    second = pipeline.run_update(
        project_dir=PROJECT_DIR,
        data_dir=tmp_path / "data",
        catalog_path=tmp_path / "indicator_catalog.json",
        force=True,
        client=client,
        now=NOW + timedelta(hours=1),
        environ={},
        modules={"news_events"},
    )
    for key in pipeline.MODULE_KEYS:
        if key != "news_events":
            assert second["modules"][key] == first["modules"][key]
    assert second["summary"]["refreshed_modules"] == ["news_events"]
    assert all(
        endpoint in {"stock_news_em", "news_economic_baidu", "stock_individual_notice_report"}
        for endpoint, _ in client.calls
    )


def test_partial_refresh_requires_previous_valid_snapshot(tmp_path: Path):
    catalog_path = _write_catalog(tmp_path)
    with pytest.raises(pipeline.PipelineError, match="requires a previous valid snapshot"):
        pipeline.run_update(
            project_dir=PROJECT_DIR,
            data_dir=tmp_path / "empty-data",
            catalog_path=catalog_path,
            force=True,
            client=FixtureClient(),
            now=NOW,
            environ={},
            modules={"news_events"},
        )


def test_stock_sources_fall_back_to_sina_with_actual_provenance():
    client = FallbackStockClient()
    module = pipeline.fetch_stocks(client, NOW)

    assert module["status"] == "partial"
    assert len(module["tables"][0]["rows"]) == 3
    assert all(
        row["source"] == "AKShare stock_zh_a_daily/AKShare stock_zh_a_spot"
        for row in module["tables"][0]["rows"]
    )
    calls = [(endpoint, kwargs) for endpoint, kwargs in client.calls]
    daily_symbols = [kwargs["symbol"] for endpoint, kwargs in calls if endpoint == "stock_zh_a_daily"]
    assert daily_symbols == ["sz000001", "sh600519", "sz300750"]
    assert "stock_zh_a_hist_tx" not in {endpoint for endpoint, _ in calls}
    assert all(row["turnover"] == pytest.approx(1.2) for row in module["tables"][0]["rows"])
    assert "M0275" not in module["_live_catalog_ids"]
    assert any(alert["error_type"] == "ConnectionError" for alert in module["alerts"])


def test_stock_schema_drift_reaches_tencent_and_records_schema_error():
    client = SchemaFallbackStockClient()
    module = pipeline.fetch_stocks(client, NOW)

    assert len(module["tables"][0]["rows"]) == 3
    assert all(
        row["source"] == "AKShare stock_zh_a_hist_tx/AKShare stock_zh_a_spot"
        for row in module["tables"][0]["rows"]
    )
    assert any(alert["error_type"] == "SchemaError" for alert in module["alerts"])
    assert sum(endpoint == "stock_zh_a_hist_tx" for endpoint, _ in client.calls) == 3


def test_stock_source_never_claims_a_failed_history_or_spot_source():
    spot_only = pipeline.fetch_stocks(SpotOnlyStockClient(), NOW)
    assert all(row["source"] == "AKShare stock_zh_a_spot_em" for row in spot_only["tables"][0]["rows"])
    assert not spot_only["charts"]
    assert "M0290" not in spot_only["_live_catalog_ids"]
    assert "M0275" not in spot_only["_live_catalog_ids"]

    history_only = pipeline.fetch_stocks(HistoryOnlyStockClient(), NOW)
    assert all(row["source"] == "AKShare stock_zh_a_hist" for row in history_only["tables"][0]["rows"])
    assert all(row["close_basis"] == "qfq" for row in history_only["tables"][0]["rows"])
    assert "M0278" not in history_only["_live_catalog_ids"]
    assert "M0290" in history_only["_live_catalog_ids"]
    assert history_only["charts"]


def test_sina_prefixed_codes_match_and_time_only_timestamp_does_not_invent_date():
    module = pipeline.fetch_stocks(SinaSpotOnlyStockClient(), NOW)
    assert len(module["tables"][0]["rows"]) == 3
    assert all(row["source"] == "AKShare stock_zh_a_spot" for row in module["tables"][0]["rows"])
    assert all(row["as_of"] is None for row in module["tables"][0]["rows"])


def test_bse_920_code_uses_bj_prefix(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DASHBOARD_STOCKS", "920001")
    client = FallbackStockClient()
    pipeline.fetch_stocks(client, NOW)
    daily_symbols = [kwargs["symbol"] for endpoint, kwargs in client.calls if endpoint == "stock_zh_a_daily"]
    assert daily_symbols == ["bj920001"]


def test_stock_units_and_pe_semantics_follow_endpoint_documentation():
    eastmoney = pipeline.fetch_stocks(FixtureClient(), NOW)
    first = eastmoney["tables"][0]["rows"][0]
    assert first["volume"] == pytest.approx(100_000_000.0)
    assert first["close_basis"] == "raw"
    assert first["pe"] is None
    assert "M0285" not in eastmoney["_live_catalog_ids"]

    tencent = pipeline.fetch_stocks(TencentOnlyStockClient(), NOW)
    assert all(row["volume"] == pytest.approx(123_400.0) for row in tencent["tables"][0]["rows"])
    assert all(row["amount"] is None for row in tencent["tables"][0]["rows"])
    assert all(row["source"] == "AKShare stock_zh_a_hist_tx" for row in tencent["tables"][0]["rows"])
    assert "M0281" in tencent["_live_catalog_ids"]
    assert "M0282" not in tencent["_live_catalog_ids"]


def test_atomic_json_never_leaves_temp_and_rejects_nan(tmp_path: Path):
    target = tmp_path / "atomic.json"
    pipeline.atomic_write_json(target, {"ok": 1})
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": 1}
    assert not list(tmp_path.glob(".atomic.json.*.tmp"))
    with pytest.raises(ValueError):
        pipeline.atomic_write_json(target, {"bad": math.nan})
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": 1}


def test_fresh_ttl_returns_cache_without_network(tmp_path: Path):
    first = _run(tmp_path, FixtureClient(), now=NOW, force=True)
    catalog_path = tmp_path / "indicator_catalog.json"
    second = pipeline.run_update(
        project_dir=PROJECT_DIR,
        data_dir=tmp_path / "data",
        catalog_path=catalog_path,
        force=False,
        ttl_seconds=20 * 60 * 60,
        client=BombClient(),
        now=NOW + timedelta(hours=1),
        environ={},
    )
    assert second["generated_at"] == first["generated_at"]
    assert second["summary"]["cache"] == "fresh_cache"


def test_source_failure_uses_last_valid_values_and_marks_every_module_stale(tmp_path: Path):
    first = _run(tmp_path, FixtureClient(), now=NOW, force=True)
    failing = FailClient()
    second = pipeline.run_update(
        project_dir=PROJECT_DIR,
        data_dir=tmp_path / "data",
        catalog_path=tmp_path / "indicator_catalog.json",
        force=True,
        client=failing,
        now=NOW + timedelta(days=1),
        environ={},
    )
    assert failing.calls > 0
    assert second["status"] == "stale"
    assert all(module["status"] == "stale" for module in second["modules"].values())
    assert all(
        kpi["quality"] == "stale_cache" and kpi["status"] == "stale"
        for module in second["modules"].values()
        for kpi in module["kpis"]
    )
    assert second["modules"]["stock"]["kpis"][0]["value"] == first["modules"]["stock"]["kpis"][0]["value"]
    assert second["summary"]["fabricated_observations"] == 0


def test_invalid_catalog_count_blocks_refresh_before_source_calls(tmp_path: Path):
    catalog_path = _write_catalog(tmp_path, rows=366)
    client = FailClient()
    with pytest.raises(pipeline.PipelineError, match="exactly 367"):
        pipeline.run_update(
            project_dir=PROJECT_DIR,
            data_dir=tmp_path / "data",
            catalog_path=catalog_path,
            force=True,
            client=client,
            now=NOW,
            environ={},
        )
    assert client.calls == 0


def test_premium_configuration_is_boolean_only_and_never_called():
    marker = "sensitive-value-must-not-be-serialized"
    state = pipeline.premium_source_state({
        "DASHBOARD_ENABLE_TUSHARE": "0",
        "DASHBOARD_ENABLE_IFIND": "0",
        "DASHBOARD_ENABLE_WIND": "0",
        "TUSHARE_TOKEN": marker,
        "IFIND_ACCESS_TOKEN": marker,
        "WIND_SQL_SERVER": marker,
    })
    assert all(item["configured"] for item in state.values())
    assert all(not item["enabled"] and not item["called"] for item in state.values())
    assert marker not in json.dumps(state)


def test_snapshot_validation_rejects_nonfinite_observation(tmp_path: Path):
    snapshot = _run(tmp_path, FixtureClient())
    broken = copy.deepcopy(snapshot)
    broken["modules"]["macro"]["kpis"][0]["value"] = math.inf
    with pytest.raises(ValueError):
        pipeline.validate_snapshot(broken)
