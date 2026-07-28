# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import textwrap
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
VENDOR = PROJECT_ROOT / "environment" / "vendor"
APP_VENDOR = PROJECT_ROOT / "environment" / "vendor" / "docx_runtime"
for vendor_path in [APP_VENDOR, VENDOR]:
    if vendor_path.exists() and str(vendor_path) not in sys.path:
        sys.path.insert(0, str(vendor_path))

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
from matplotlib.patches import FancyBboxPatch

from data_quality import IndustryDataQualityGate


DB_DEFAULT = PROJECT_ROOT / "database" / "research_warehouse.db"
OUT_DEFAULT = PROJECT_ROOT / "output" / "industry_depth_report"

SW_L1_INDUSTRIES = [
    "农林牧渔", "煤炭", "石油石化", "基础化工", "钢铁", "有色金属", "电子", "电力设备",
    "国防军工", "计算机", "传媒", "通信", "汽车", "机械设备", "家用电器", "食品饮料",
    "纺织服饰", "轻工制造", "美容护理", "医药生物", "公用事业", "环保", "交通运输",
    "房地产", "建筑材料", "建筑装饰", "商贸零售", "社会服务", "银行", "非银金融",
]

INDUSTRY_TYPE = {
    "煤炭": "资源周期", "石油石化": "资源周期", "基础化工": "资源周期", "钢铁": "资源周期", "有色金属": "资源周期",
    "机械设备": "中游制造", "电力设备": "中游制造", "汽车": "中游制造", "国防军工": "中游制造", "电子": "科技制造",
    "计算机": "科技成长", "通信": "科技成长", "传媒": "科技成长",
    "食品饮料": "消费", "家用电器": "消费", "纺织服饰": "消费", "轻工制造": "消费", "美容护理": "消费",
    "商贸零售": "消费", "社会服务": "消费",
    "银行": "金融", "非银金融": "金融",
    "房地产": "地产链", "建筑材料": "地产链", "建筑装饰": "地产链",
    "医药生物": "医药", "公用事业": "稳定现金流", "环保": "稳定现金流", "交通运输": "稳定现金流",
    "农林牧渔": "农业周期",
}

TYPE_FRAMEWORK = {
    "资源周期": {
        "core": "产品价格，库存，供给约束，成本曲线，资本开支",
        "profit": "行业利润取决于产品价格减原料成本，能源成本，运费和加工成本。",
        "demand": "需求需要拆成地产基建，制造业，出口和补库。",
        "supply": "供给需要看新增产能，开工率，环保安全约束，进口和库存。",
    },
    "中游制造": {
        "core": "订单，排产，客户资本开支，产能利用率，原材料传导",
        "profit": "行业利润取决于订单增速，产能利用率，原材料价格和费用率。",
        "demand": "需求需要拆成国内设备更新，出口，产业升级和下游资本开支。",
        "supply": "供给需要看扩产节奏，客户认证，规模效应和价格竞争。",
    },
    "科技制造": {
        "core": "产品周期，库存周期，资本开支，技术路线，国产替代",
        "profit": "行业利润取决于产品价格周期，稼动率，研发效率和客户结构。",
        "demand": "需求需要拆成终端出货，AI和算力资本开支，消费电子换机，车载和工业应用。",
        "supply": "供给需要看全球产能，库存位置，技术节点和供应链安全。",
    },
    "科技成长": {
        "core": "渗透率，政策采购，AI应用，付费率，估值分母",
        "profit": "行业利润取决于收入增长，研发费用率，销售费用率和规模化兑现。",
        "demand": "需求需要拆成政府IT，企业数字化，AI应用，流量和内容消费。",
        "supply": "供给需要看技术壁垒，项目交付能力，生态和客户粘性。",
    },
    "消费": {
        "core": "居民收入，价格带，渠道库存，品牌力，客流，复购",
        "profit": "行业利润取决于销量，客单价，渠道费用，促销强度和品牌定价权。",
        "demand": "需求需要拆成人群，渗透率，购买频次，价格带和消费场景。",
        "supply": "供给需要看品牌集中度，渠道效率，库存和费用投放。",
    },
    "金融": {
        "core": "利率，利差，资产质量，资本市场成交，风险偏好",
        "profit": "行业利润取决于资产规模，利差或费率，信用成本和投资收益。",
        "demand": "需求需要拆成信贷，财富管理，保险保障，交易和融资。",
        "supply": "供给需要看资本约束，监管要求，渠道和风险定价能力。",
    },
    "地产链": {
        "core": "销售，开工，竣工，库存，融资，财政",
        "profit": "行业利润取决于地产销售和建设链条的量，价格，回款和信用风险。",
        "demand": "需求需要拆成新房销售，二手房，开工，竣工，基建和存量改造。",
        "supply": "供给需要看库存，土地，房企融资，地方财政和产能出清。",
    },
    "稳定现金流": {
        "core": "价格机制，利用率，成本，现金流，分红",
        "profit": "行业利润取决于价格机制，资产利用率，燃料或运营成本和资本开支。",
        "demand": "需求需要拆成用电，用水，出行，物流，环保运营和公共服务。",
        "supply": "供给需要看新增资产，准许收益率，政策监管和成本传导。",
    },
    "医药": {
        "core": "产品周期，医保支付，院内需求，研发兑现，集采风险",
        "profit": "行业利润取决于产品放量，价格降幅，研发费用率和支付端政策。",
        "demand": "需求需要拆成患者人数，渗透率，治疗周期，支付能力和院内恢复。",
        "supply": "供给需要看研发管线，注册审批，生产质量，集采格局和渠道。",
    },
    "农业周期": {
        "core": "猪周期，粮价，养殖利润，疫病，供给去化",
        "profit": "行业利润取决于农产品价格，饲料成本，养殖效率和库存。",
        "demand": "需求需要拆成居民消费，食品加工，出口和替代品。",
        "supply": "供给需要看能繁母猪，存栏，疫病，天气和进口。",
    },
}

FIXED_RGB_PALETTE = (
    (192, 0, 0),
    (255, 192, 0),
    (47, 117, 181),
    (128, 128, 128),
    (237, 125, 49),
    (112, 48, 160),
    (0, 176, 80),
    (91, 155, 213),
    (165, 165, 165),
    (255, 0, 0),
)

PALETTE = ["#%02X%02X%02X" % c for c in FIXED_RGB_PALETTE]
GRID = "#BFBFBF"
BLACK = "#000000"

plt.rcParams.update({
    "font.sans-serif": ["KaiTi", "SimSun", "Microsoft YaHei", "Arial"],
    "font.family": "sans-serif",
    "axes.unicode_minus": False,
    "axes.edgecolor": BLACK,
    "axes.linewidth": 0.8,
    "axes.grid": False,
    "figure.facecolor": "white",
    "savefig.facecolor": "white",
    "figure.dpi": 160,
    "savefig.dpi": 220,
})


@dataclass
class ReportResult:
    job_id: str
    industry: str
    as_of: str
    output_dir: Path
    docx_path: Path
    payload_path: Path
    figures: list[Path]
    status: str = "done"


class DataQualityError(RuntimeError):
    pass


def ymd(date_value: str | None) -> str:
    if not date_value:
        return ""
    s = str(date_value).strip().replace("-", "")
    if len(s) >= 8:
        return s[:8]
    if len(s) == 6:
        return s + "01"
    return s


def pretty_date(s: str | None) -> str:
    t = ymd(s)
    if len(t) == 8:
        return f"{t[:4]}-{t[4:6]}-{t[6:8]}"
    return str(s or "")


def to_datetime_series(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s.astype(str), format="%Y%m%d", errors="coerce")


def years_before(as_of: str, years: int) -> str:
    dt = pd.to_datetime(ymd(as_of), format="%Y%m%d", errors="coerce")
    if pd.isna(dt):
        return ""
    return (dt - pd.DateOffset(years=years)).strftime("%Y%m%d")


def safe_float(v: Any, default: float | None = None) -> float | None:
    try:
        if v is None or v == "":
            return default
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except Exception:
        return default


def fmt_num(v: Any, digits: int = 2) -> str:
    x = safe_float(v)
    if x is None:
        return "-"
    return f"{x:.{digits}f}"


def fmt_pct_ratio(v: Any, digits: int = 1) -> str:
    x = safe_float(v)
    if x is None:
        return "-"
    return f"{x * 100:.{digits}f}%"


def fmt_pct_value(v: Any, digits: int = 1) -> str:
    x = safe_float(v)
    if x is None:
        return "-"
    return f"{x:.{digits}f}%"


def json_clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(k): json_clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_clean(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.strftime("%Y-%m-%d")
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        value = float(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def payload_to_json(payload: dict[str, Any]) -> str:
    return json.dumps(json_clean(payload), ensure_ascii=False, indent=2, default=str, allow_nan=False)


def clean_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip("_")


def first_existing(*paths: Path) -> Path:
    for p in paths:
        if p.exists():
            return p
    return paths[0]


class SafeAIWriter:
    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled
        self.api_key = os.environ.get("AI_ROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
        self.base_url = (os.environ.get("AI_ROUTER_BASE_URL") or "https://ai.router.team").rstrip("/")
        self.model = os.environ.get("AI_ROUTER_MODEL", "gpt-5.5")
        self.reasoning_effort = os.environ.get("AI_ROUTER_REASONING_EFFORT", "xhigh")

    def available(self) -> bool:
        return bool(self.enabled and self.api_key)

    def rewrite(self, section_name: str, evidence: dict[str, Any], fallback: str) -> str:
        if not self.available():
            return fallback
        system = (
            "你是中国卖方行业研究员。只根据用户提供的JSON证据写段落。"
            "禁止生成JSON之外的数字。禁止编造外部事实。"
            "语言专业，短句，少形容词，不口语化。"
            "不要使用顿号，不要使用引号，不要写不是而是。"
        )
        user = {
            "section": section_name,
            "style": "五号楷体正文，专业研报表达，260到420字。必须保留数据源、指标、图形判读、结论推导。不写行业定义。",
            "evidence": evidence,
            "draft": fallback,
        }
        try:
            resp = requests.post(
                f"{self.base_url}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
                    ],
                    "reasoning_effort": self.reasoning_effort,
                    "temperature": 0.1,
                },
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"].strip()
            min_len = min(260, max(180, int(len(fallback) * 0.65)))
            if not text or len(text) < min_len or "?" in text or "行业定义" in text:
                return fallback
            return text
        except Exception:
            return fallback


class IndustryDepthReportAgent:
    def __init__(
        self,
        db_path: Path | str = DB_DEFAULT,
        output_root: Path | str = OUT_DEFAULT,
        use_ai: bool = False,
    ) -> None:
        self.db_path = Path(db_path)
        self.output_root = Path(output_root)
        self.ai = SafeAIWriter(enabled=use_ai)

    def generate(self, industry: str, as_of: str | None = None, lookback_years: int = 3, job_id: str | None = None) -> ReportResult:
        industry = industry.strip()
        if industry == "综合" or industry not in SW_L1_INDUSTRIES:
            raise ValueError(f"行业必须为非综合申万一级行业，当前输入为 {industry}")
        if not self.db_path.exists():
            raise FileNotFoundError(f"数据库不存在：{self.db_path}")
        job_id = job_id or datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
        as_of = ymd(as_of) if as_of else self.latest_trade_date()
        start_date = years_before(as_of, max(1, int(lookback_years)))
        out_dir = self.output_root / clean_filename(industry) / f"{as_of}_{job_id}"
        fig_dir = out_dir / "figures"
        out_dir.mkdir(parents=True, exist_ok=True)
        fig_dir.mkdir(parents=True, exist_ok=True)

        payload = self.collect_payload(industry, as_of, start_date)
        payload["job_id"] = job_id
        payload["output_dir"] = str(out_dir)
        gate = IndustryDataQualityGate(PROJECT_ROOT)
        gate_result = gate.evaluate(payload)
        gate_path, missing_path = gate.write_outputs(gate_result, out_dir)
        payload["quality_gate"] = {
            "passed": gate_result.passed,
            "blockers": gate_result.blockers,
            "warnings": gate_result.warnings,
            "metrics": gate_result.metrics,
            "gate_path": str(gate_path),
            "missing_data_plan": str(missing_path),
        }
        partial_payload_path = out_dir / "payload.partial.json"
        partial_payload_path.write_text(payload_to_json(payload), encoding="utf-8")
        if not gate_result.passed:
            raise DataQualityError(f"数据质量门未通过，已生成缺口清单：{missing_path}")
        payload["figures"] = self.make_figures(payload, fig_dir)
        payload["sections"] = self.make_sections(payload)
        payload_path = out_dir / "payload.json"
        payload_path.write_text(payload_to_json(payload), encoding="utf-8")
        docx_path = self.run_docx_writer(payload_path, out_dir)

        return ReportResult(
            job_id=job_id,
            industry=industry,
            as_of=as_of,
            output_dir=out_dir,
            docx_path=docx_path,
            payload_path=payload_path,
            figures=[Path(p) for p in payload["figures"].values() if p],
        )

    def run_docx_writer(self, payload_path: Path, out_dir: Path) -> Path:
        writer = Path(__file__).resolve().parent / "docx_writer.py"
        bundled_python = Path(os.environ.get(
            "INDUSTRY_DOCX_PYTHON",
            sys.executable,
        ))
        python_exe = bundled_python if bundled_python.exists() else Path(sys.executable)
        proc = subprocess.run(
            [str(python_exe), str(writer), "--payload", str(payload_path), "--out-dir", str(out_dir)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=False,
            timeout=180,
        )
        stdout = self.decode_process_bytes(proc.stdout)
        stderr = self.decode_process_bytes(proc.stderr)
        if proc.returncode != 0:
            raise RuntimeError(f"Word写入失败：{stderr or stdout}")
        data = json.loads(stdout)
        return Path(data["docx"])

    def decode_process_bytes(self, data: bytes | None) -> str:
        if not data:
            return ""
        for enc in ["utf-8-sig", "utf-8", "gbk", "cp936"]:
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="ignore")

    def latest_trade_date(self) -> str:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("select max(trade_date) from stock_ohlcv_daily").fetchone()
        return str(row[0])

    def q(self, sql: str, params: tuple[Any, ...] = ()) -> pd.DataFrame:
        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql_query(sql, conn, params=params)

    def table_exists(self, table_name: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "select 1 from sqlite_master where type='table' and name=?",
                (table_name,),
            ).fetchone()
        return row is not None

    def q_optional(self, table_name: str, sql: str, params: tuple[Any, ...] = ()) -> pd.DataFrame:
        if not self.table_exists(table_name):
            return pd.DataFrame()
        return self.q(sql, params)

    def q_codes(self, table: str, cols: list[str], codes: list[str], start_date: str, as_of: str) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        for i in range(0, len(codes), 500):
            chunk = codes[i:i + 500]
            ph = ",".join("?" for _ in chunk)
            sql = f"select {','.join(cols)} from {table} where ts_code in ({ph}) and trade_date between ? and ?"
            frames.append(self.q(sql, tuple(chunk + [start_date, as_of])))
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=cols)

    def collect_payload(self, industry: str, as_of: str, start_date: str) -> dict[str, Any]:
        members = self.get_members(industry, as_of)
        codes = members["ts_code"].dropna().unique().tolist()
        if not codes:
            raise ValueError(f"在 {as_of} 未找到 {industry} 成分股")

        ohlcv = self.q_codes(
            "stock_ohlcv_daily",
            ["trade_date", "ts_code", "stock_name", "pct_chg", "close", "amount"],
            codes,
            start_date,
            as_of,
        )
        valuation = self.q_codes(
            "stock_valuation_daily",
            ["trade_date", "ts_code", "pe_ttm", "pb", "ps_ttm", "dv_ttm", "total_mv", "turnover_rate"],
            codes,
            start_date,
            as_of,
        )
        money = self.q_codes(
            "stock_moneyflow_daily",
            ["trade_date", "ts_code", "net_mf_amount", "buy_lg_amount", "sell_lg_amount", "buy_elg_amount", "sell_elg_amount"],
            codes,
            start_date,
            as_of,
        )
        benchmark = self.q(
            "select trade_date, avg(pct_chg) as pct_chg from stock_ohlcv_daily where trade_date between ? and ? group by trade_date order by trade_date",
            (start_date, as_of),
        )
        fin = self.get_financial(codes, start_date, as_of)
        macro = self.q(
            "select * from macro_monthly where month between ? and ? order by month",
            (start_date[:6], as_of[:6]),
        )
        signal = self.q_optional(
            "v3_industry_signal",
            "select * from v3_industry_signal where industry_name=? and rebalance_date between ? and ? order by rebalance_date",
            (industry, start_date, as_of),
        )
        reports = self.q(
            "select report_date, org, title, researcher, link, model_tag from broker_report_index "
            "where title like ? or model_tag like ? order by report_date desc limit 15",
            (f"%{industry}%", f"%{industry}%"),
        )

        payload: dict[str, Any] = {
            "industry": industry,
            "industry_type": INDUSTRY_TYPE.get(industry, "综合研究"),
            "framework": TYPE_FRAMEWORK.get(INDUSTRY_TYPE.get(industry, ""), TYPE_FRAMEWORK["中游制造"]),
            "as_of": as_of,
            "start_date": start_date,
            "db_path": str(self.db_path),
            "data_policy": "本报告使用本地 research_warehouse.db。外部付费接口默认不消耗。AI只基于结构化数据摘要写解释。",
            "members": {"count": int(len(codes)), "sample": members.head(20).to_dict("records")},
        }
        payload["market"] = self.summarize_market(ohlcv, benchmark, valuation, money)
        payload["financial"] = self.summarize_financial(fin)
        payload["macro"] = self.summarize_macro(macro)
        payload["signal"] = self.summarize_signal(signal)
        payload["reports"] = reports.fillna("").to_dict("records")

        data_dir = Path(payload.get("output_dir", self.output_root)) / "data"
        return payload

    def get_members(self, industry: str, as_of: str) -> pd.DataFrame:
        df = self.q(
            """
            select ts_code, industry_code, industry_name, start_date, end_date
            from sw_l1_industry_daily
            where industry_name=? and start_date<=? and (end_date is null or end_date='' or end_date>=?)
            order by ts_code
            """,
            (industry, as_of, as_of),
        )
        if df.empty:
            df = self.q(
                """
                select ts_code, industry_code, industry_name, start_date, end_date
                from sw_l1_industry_daily
                where industry_name=? and (end_date is null or end_date='')
                order by ts_code
                """,
                (industry,),
            )
        return df

    def get_financial(self, codes: list[str], start_date: str, as_of: str) -> pd.DataFrame:
        frames = []
        for i in range(0, len(codes), 500):
            chunk = codes[i:i + 500]
            ph = ",".join("?" for _ in chunk)
            sql = (
                "select ts_code, visible_date, end_date, report_type, total_revenue, n_income_attr_p, gross_margin, "
                "netprofit_margin, roe, roa, debt_to_assets, current_ratio, assets_turn, op_yoy, tr_yoy, netprofit_yoy "
                f"from financial_report_visible where ts_code in ({ph}) and visible_date<=? and end_date>=? "
                "order by ts_code, end_date, visible_date"
            )
            frames.append(self.q(sql, tuple(chunk + [as_of, start_date])))
        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames, ignore_index=True)
        if df.empty:
            return df
        df = df.sort_values(["ts_code", "end_date", "visible_date"]).drop_duplicates(["ts_code", "end_date"], keep="last")
        return df

    def summarize_market(self, ohlcv: pd.DataFrame, benchmark: pd.DataFrame, valuation: pd.DataFrame, money: pd.DataFrame) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if not ohlcv.empty:
            ohlcv = ohlcv.copy()
            ohlcv["date"] = to_datetime_series(ohlcv["trade_date"])
            ohlcv["ret"] = pd.to_numeric(ohlcv["pct_chg"], errors="coerce") / 100
            ind_daily = ohlcv.groupby("trade_date", as_index=False).agg(
                pct_chg=("pct_chg", "mean"),
                amount=("amount", "sum"),
                stocks=("ts_code", "nunique"),
            )
            ind_daily["ret"] = pd.to_numeric(ind_daily["pct_chg"], errors="coerce") / 100
            ind_daily["cum_return"] = (1 + ind_daily["ret"].fillna(0)).cumprod() - 1
            out["industry_daily"] = ind_daily.to_dict("records")
            latest = ind_daily.tail(1).iloc[0]
            out["latest_trade_date"] = str(latest["trade_date"])
            out["cum_return"] = safe_float(latest["cum_return"])
            out["latest_amount_yi"] = safe_float(latest["amount"], 0) / 1e5 if safe_float(latest["amount"]) is not None else None
            for days, key in [(21, "ret_1m"), (63, "ret_3m"), (126, "ret_6m"), (252, "ret_12m")]:
                if len(ind_daily) > days:
                    s = ind_daily["cum_return"]
                    out[key] = float((1 + s.iloc[-1]) / (1 + s.iloc[-days - 1]) - 1)
        if not benchmark.empty:
            bench = benchmark.copy()
            bench["ret"] = pd.to_numeric(bench["pct_chg"], errors="coerce") / 100
            bench["cum_return"] = (1 + bench["ret"].fillna(0)).cumprod() - 1
            out["benchmark_daily"] = bench.to_dict("records")
            out["benchmark_cum_return"] = safe_float(bench["cum_return"].iloc[-1]) if not bench.empty else None
        if not valuation.empty:
            val = valuation.copy()
            for c in ["pe_ttm", "pb", "ps_ttm", "dv_ttm", "total_mv", "turnover_rate"]:
                val[c] = pd.to_numeric(val[c], errors="coerce")
            daily_val = val.groupby("trade_date", as_index=False).agg(
                median_pe=("pe_ttm", "median"),
                median_pb=("pb", "median"),
                median_ps=("ps_ttm", "median"),
                median_dv=("dv_ttm", "median"),
                median_turnover=("turnover_rate", "median"),
                total_mv=("total_mv", "sum"),
            )
            out["valuation_daily"] = daily_val.to_dict("records")
            last = daily_val.tail(1).iloc[0]
            out["median_pe"] = safe_float(last["median_pe"])
            out["median_pb"] = safe_float(last["median_pb"])
            out["median_ps"] = safe_float(last["median_ps"])
            out["median_dv"] = safe_float(last["median_dv"])
            out["median_turnover"] = safe_float(last["median_turnover"])
            out["total_mv_yi"] = safe_float(last["total_mv"], 0) / 1e4
            for col in ["median_pe", "median_pb", "median_ps"]:
                s = pd.to_numeric(daily_val[col], errors="coerce").dropna()
                v = safe_float(last[col])
                out[f"{col}_pctile"] = float((s <= v).mean()) if len(s) and v is not None else None
        if not money.empty:
            m = money.copy()
            for c in ["net_mf_amount", "buy_lg_amount", "sell_lg_amount", "buy_elg_amount", "sell_elg_amount"]:
                m[c] = pd.to_numeric(m[c], errors="coerce")
            daily_m = m.groupby("trade_date", as_index=False).agg(
                net_mf_amount=("net_mf_amount", "sum"),
                buy_lg_amount=("buy_lg_amount", "sum"),
                sell_lg_amount=("sell_lg_amount", "sum"),
                buy_elg_amount=("buy_elg_amount", "sum"),
                sell_elg_amount=("sell_elg_amount", "sum"),
            )
            daily_m["net_mf_20d"] = daily_m["net_mf_amount"].rolling(20, min_periods=5).sum()
            out["moneyflow_daily"] = daily_m.to_dict("records")
            out["latest_net_mf_yi"] = safe_float(daily_m["net_mf_amount"].iloc[-1], 0) / 1e8 if not daily_m.empty else None
            out["net_mf_20d_yi"] = safe_float(daily_m["net_mf_20d"].iloc[-1], 0) / 1e8 if not daily_m.empty else None
        return out

    def summarize_financial(self, fin: pd.DataFrame) -> dict[str, Any]:
        if fin.empty:
            return {"available": False}
        df = fin.copy()
        for c in ["total_revenue", "n_income_attr_p", "gross_margin", "netprofit_margin", "roe", "roa", "debt_to_assets", "assets_turn", "op_yoy", "tr_yoy", "netprofit_yoy"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        gross_raw = df["gross_margin"]
        revenue = df["total_revenue"].replace(0, np.nan)
        gross_from_amount = gross_raw / revenue * 100
        df["gross_margin_pct"] = np.where(gross_raw.abs() > 200, gross_from_amount, gross_raw)
        df.loc[(df["gross_margin_pct"] < -100) | (df["gross_margin_pct"] > 100), "gross_margin_pct"] = np.nan
        agg = df.groupby("end_date", as_index=False).agg(
            sample=("ts_code", "nunique"),
            revenue_sum=("total_revenue", "sum"),
            profit_sum=("n_income_attr_p", "sum"),
            median_gross_margin=("gross_margin_pct", "median"),
            median_net_margin=("netprofit_margin", "median"),
            median_roe=("roe", "median"),
            median_roa=("roa", "median"),
            median_debt_to_assets=("debt_to_assets", "median"),
            median_assets_turn=("assets_turn", "median"),
            median_op_yoy=("op_yoy", "median"),
            median_tr_yoy=("tr_yoy", "median"),
            median_netprofit_yoy=("netprofit_yoy", "median"),
        ).sort_values("end_date")
        latest = agg.tail(1).iloc[0]
        annual = agg[agg["end_date"].astype(str).str.endswith("1231")]
        return {
            "available": True,
            "financial_series": agg.to_dict("records"),
            "annual_series": annual.to_dict("records"),
            "latest_end_date": str(latest["end_date"]),
            "latest_sample": int(latest["sample"]),
            "revenue_sum_yi": safe_float(latest["revenue_sum"], 0) / 1e8,
            "profit_sum_yi": safe_float(latest["profit_sum"], 0) / 1e8,
            "median_gross_margin": safe_float(latest["median_gross_margin"]),
            "median_net_margin": safe_float(latest["median_net_margin"]),
            "median_roe": safe_float(latest["median_roe"]),
            "median_roa": safe_float(latest["median_roa"]),
            "median_debt_to_assets": safe_float(latest["median_debt_to_assets"]),
            "median_assets_turn": safe_float(latest["median_assets_turn"]),
            "median_op_yoy": safe_float(latest["median_op_yoy"]),
            "median_tr_yoy": safe_float(latest["median_tr_yoy"]),
            "median_netprofit_yoy": safe_float(latest["median_netprofit_yoy"]),
            "gross_margin_source": "computed_from_gross_profit_amount_when_raw_value_exceeds_200",
        }

    def summarize_macro(self, macro: pd.DataFrame) -> dict[str, Any]:
        if macro.empty:
            return {"available": False}
        out: dict[str, Any] = {"available": True, "series": macro.fillna("").to_dict("records")}
        for col in ["pmi_manufacturing", "pmi_non_manufacturing", "ppi_yoy", "cpi_national_yoy", "m1_yoy", "m2_yoy", "sf_stock_endval"]:
            if col in macro.columns:
                s = pd.to_numeric(macro[col], errors="coerce")
                valid = macro.loc[s.notna(), ["month", col]]
                if not valid.empty:
                    out[col] = safe_float(valid[col].iloc[-1])
                    out[f"{col}_month"] = str(valid["month"].iloc[-1])
        return out

    def summarize_signal(self, signal: pd.DataFrame) -> dict[str, Any]:
        if signal.empty:
            return {"available": False}
        df = signal.copy()
        for c in ["score", "target_weight"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        latest = df.tail(1).iloc[0]
        return {
            "available": True,
            "series": df.fillna("").to_dict("records"),
            "latest_date": str(latest.get("rebalance_date", "")),
            "latest_score": safe_float(latest.get("score")),
            "latest_target_weight": safe_float(latest.get("target_weight")),
        }

    def make_figures(self, payload: dict[str, Any], fig_dir: Path) -> dict[str, str]:
        figs = {
            "framework": self.plot_framework(payload, fig_dir / "01_framework.png"),
            "returns": self.plot_returns(payload, fig_dir / "02_returns.png"),
            "demand": self.plot_demand(payload, fig_dir / "03_demand.png"),
            "supply": self.plot_supply(payload, fig_dir / "04_supply.png"),
            "profit": self.plot_profit(payload, fig_dir / "05_profit.png"),
            "valuation": self.plot_valuation(payload, fig_dir / "06_valuation.png"),
            "moneyflow": self.plot_moneyflow(payload, fig_dir / "07_moneyflow.png"),
            "financial": self.plot_financial(payload, fig_dir / "08_financial.png"),
            "macro": self.plot_macro(payload, fig_dir / "09_macro.png"),
            "signal": self.plot_signal(payload, fig_dir / "10_signal.png"),
            "scenario": self.plot_scenario_matrix(payload, fig_dir / "11_scenario.png"),
            "data_map": self.plot_data_map(payload, fig_dir / "12_data_map.png"),
        }
        return {k: str(v) for k, v in figs.items() if v}

    def chart_source(self, fig: plt.Figure, text: str) -> None:
        fig.text(0.01, 0.01, text, ha="left", va="bottom", fontsize=8.5, color="#666666")

    def title(self, ax, title: str, subtitle: str = "") -> None:
        ax.set_title(title, loc="left", fontsize=14, fontweight="bold", color=BLACK, pad=12)
        if subtitle:
            ax.text(0, 1.02, subtitle, transform=ax.transAxes, ha="left", va="bottom", fontsize=9.2, color="#666666")

    def plot_framework(self, payload: dict[str, Any], path: Path) -> Path:
        industry = payload["industry"]
        f = payload["framework"]
        fig, ax = plt.subplots(figsize=(12, 3.8))
        ax.axis("off")
        steps = [
            ("行业边界", payload["industry_type"]),
            ("需求拆解", f["demand"]),
            ("供给拆解", f["supply"]),
            ("利润机制", f["profit"]),
            ("财务验证", "收入，利润率，ROE，现金流"),
            ("定价结论", "估值分位，资金位置，情景推演"),
        ]
        xs = np.linspace(0.035, 0.84, len(steps))
        y = 0.50
        w = 0.135
        for i, (name, desc) in enumerate(steps):
            box = FancyBboxPatch((xs[i], y - 0.18), w, 0.34, boxstyle="round,pad=0.012,rounding_size=0.012",
                                 facecolor="white", edgecolor=PALETTE[i % len(PALETTE)], linewidth=1.4)
            ax.add_patch(box)
            ax.text(xs[i] + w / 2, y + 0.07, name, ha="center", va="center", fontsize=10.5, fontweight="bold", color=BLACK)
            wrapped = "\n".join(textwrap.wrap(desc, width=12))
            ax.text(xs[i] + w / 2, y - 0.07, wrapped, ha="center", va="center", fontsize=8.7, color=BLACK, linespacing=1.25)
            if i < len(steps) - 1:
                ax.annotate("", xy=(xs[i + 1] - 0.008, y), xytext=(xs[i] + w + 0.008, y),
                            arrowprops=dict(arrowstyle="->", lw=1.2, color="#666666"))
        ax.text(0.01, 0.92, f"图1：{industry}行业深度研究链条", fontsize=14, fontweight="bold", color=BLACK, ha="left")
        ax.text(0.01, 0.84, "先确定行业边界，再把景气，利润，财务和定价放在同一条证据链上。", fontsize=9.2, color="#666666", ha="left")
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        return path

    def plot_returns(self, payload: dict[str, Any], path: Path) -> Path | None:
        m = payload["market"]
        if not m.get("industry_daily"):
            return None
        ind = pd.DataFrame(m["industry_daily"])
        bench = pd.DataFrame(m.get("benchmark_daily", []))
        ind["date"] = to_datetime_series(ind["trade_date"])
        fig, ax = plt.subplots(figsize=(12, 4.7))
        ax.plot(ind["date"], ind["cum_return"], color=PALETTE[0], lw=1.7, label=f"{payload['industry']}等权")
        if not bench.empty:
            bench["date"] = to_datetime_series(bench["trade_date"])
            ax.plot(bench["date"], bench["cum_return"], color=PALETTE[3], lw=1.2, label="全市场等权")
        ax.axhline(0, color=BLACK, lw=0.8)
        ax.yaxis.set_major_formatter(lambda x, pos: f"{x * 100:.0f}%")
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.28), ncol=2, frameon=False, fontsize=9)
        self.title(ax, f"图2：{payload['industry']}行业相对收益", "行业价格验证用于观察市场定价，不替代产业链判断。")
        self.chart_source(fig, "数据来源：research_warehouse.db，stock_ohlcv_daily。")
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        return path

    def plot_demand(self, payload: dict[str, Any], path: Path) -> Path | None:
        fin = payload["financial"].get("financial_series")
        macro = payload["macro"].get("series")
        market = payload["market"].get("industry_daily")
        if not fin and not macro and not market:
            return None
        fig, axes = plt.subplots(3, 1, figsize=(12, 8.0), sharex=False)
        if fin:
            df = pd.DataFrame(fin)
            df["date"] = to_datetime_series(df["end_date"])
            axes[0].plot(df["date"], pd.to_numeric(df["median_tr_yoy"], errors="coerce"), color=PALETTE[2], lw=1.4, label="收入同比中位数")
            axes[0].plot(df["date"], pd.to_numeric(df["median_netprofit_yoy"], errors="coerce"), color=PALETTE[0], lw=1.4, label="净利润同比中位数")
            axes[0].axhline(0, color=BLACK, lw=0.8)
            axes[0].yaxis.set_major_formatter(lambda x, pos: f"{x:.0f}%")
            axes[0].legend(loc="lower center", bbox_to_anchor=(0.5, -0.38), ncol=2, frameon=False, fontsize=9)
        if macro:
            md = pd.DataFrame(macro)
            md["date"] = pd.to_datetime(md["month"].astype(str) + "01", format="%Y%m%d", errors="coerce")
            axes[1].plot(md["date"], pd.to_numeric(md["pmi_manufacturing"], errors="coerce"), color=PALETTE[4], lw=1.3, label="制造业PMI")
            axes[1].plot(md["date"], pd.to_numeric(md["ppi_yoy"], errors="coerce"), color=PALETTE[0], lw=1.3, label="PPI同比")
            axes[1].axhline(50, color="#999999", lw=0.8, ls="--")
            axes[1].axhline(0, color=BLACK, lw=0.8)
            axes[1].legend(loc="lower center", bbox_to_anchor=(0.5, -0.38), ncol=2, frameon=False, fontsize=9)
        if market:
            mt = pd.DataFrame(market)
            mt["date"] = to_datetime_series(mt["trade_date"])
            amount = pd.to_numeric(mt["amount"], errors="coerce") / 1e8
            axes[2].plot(mt["date"], amount.rolling(20, min_periods=5).mean(), color=PALETTE[6], lw=1.3, label="20日成交额均值")
            axes[2].plot(mt["date"], pd.to_numeric(mt["cum_return"], errors="coerce") * 100, color=PALETTE[3], lw=1.1, label="区间累计收益")
            axes[2].legend(loc="lower center", bbox_to_anchor=(0.5, -0.38), ncol=2, frameon=False, fontsize=9)
        for ax in axes:
            ax.grid(True, axis="y", color=GRID, alpha=0.25)
        self.title(axes[0], f"图3：{payload['industry']}需求侧验证链", "需求判断先看财务收入，再看宏观景气，最后用成交和价格确认市场是否提前定价。")
        self.chart_source(fig, "数据来源：research_warehouse.db，financial_report_visible、macro_monthly、stock_ohlcv_daily。")
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        return path

    def plot_supply(self, payload: dict[str, Any], path: Path) -> Path | None:
        data = payload["financial"].get("financial_series")
        if not data:
            return None
        df = pd.DataFrame(data)
        df["date"] = to_datetime_series(df["end_date"])
        fig, axes = plt.subplots(2, 1, figsize=(12, 6.2), sharex=True)
        axes[0].plot(df["date"], pd.to_numeric(df["median_assets_turn"], errors="coerce"), color=PALETTE[2], lw=1.4, label="资产周转率")
        axes[0].plot(df["date"], pd.to_numeric(df["median_debt_to_assets"], errors="coerce"), color=PALETTE[3], lw=1.2, label="资产负债率")
        axes[0].legend(loc="lower center", bbox_to_anchor=(0.5, -0.35), ncol=2, frameon=False, fontsize=9)
        axes[1].plot(df["date"], pd.to_numeric(df["median_roe"], errors="coerce"), color=PALETTE[0], lw=1.4, label="ROE")
        axes[1].plot(df["date"], pd.to_numeric(df["median_roa"], errors="coerce"), color=PALETTE[6], lw=1.2, label="ROA")
        axes[1].axhline(0, color=BLACK, lw=0.8)
        axes[1].yaxis.set_major_formatter(lambda x, pos: f"{x:.0f}%")
        axes[1].legend(loc="lower center", bbox_to_anchor=(0.5, -0.35), ncol=2, frameon=False, fontsize=9)
        axes[1].xaxis.set_major_locator(mdates.MonthLocator(interval=6))
        axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        self.title(axes[0], f"图4：{payload['industry']}供给与资本周期代理指标", "数据库未直接接入产能和库存时，用资产周转、负债率、ROE、ROA先判断资本周期位置。")
        self.chart_source(fig, "数据来源：research_warehouse.db，financial_report_visible；产能、库存、开工率为后续行业专属接口补充项。")
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        return path

    def plot_profit(self, payload: dict[str, Any], path: Path) -> Path | None:
        data = payload["financial"].get("financial_series")
        if not data:
            return None
        df = pd.DataFrame(data)
        df["date"] = to_datetime_series(df["end_date"])
        fig, axes = plt.subplots(2, 1, figsize=(12, 6.4), sharex=True)
        axes[0].plot(df["date"], pd.to_numeric(df["revenue_sum"], errors="coerce") / 1e8, color=PALETTE[2], lw=1.4, label="收入合计")
        axes[0].plot(df["date"], pd.to_numeric(df["profit_sum"], errors="coerce") / 1e8, color=PALETTE[0], lw=1.4, label="归母净利润合计")
        axes[0].set_ylabel("亿元")
        axes[0].legend(loc="lower center", bbox_to_anchor=(0.5, -0.35), ncol=2, frameon=False, fontsize=9)
        axes[1].plot(df["date"], pd.to_numeric(df["median_gross_margin"], errors="coerce"), color=PALETTE[6], lw=1.3, label="毛利率中位数")
        axes[1].plot(df["date"], pd.to_numeric(df["median_net_margin"], errors="coerce"), color=PALETTE[4], lw=1.3, label="净利率中位数")
        axes[1].plot(df["date"], pd.to_numeric(df["median_roe"], errors="coerce"), color=PALETTE[0], lw=1.2, label="ROE中位数")
        axes[1].yaxis.set_major_formatter(lambda x, pos: f"{x:.0f}%")
        axes[1].legend(loc="lower center", bbox_to_anchor=(0.5, -0.35), ncol=3, frameon=False, fontsize=9)
        axes[1].xaxis.set_major_locator(mdates.MonthLocator(interval=6))
        axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        self.title(axes[0], f"图5：{payload['industry']}利润池和利润率", "利润弹性需要同时看收入规模、净利润池、毛利率、净利率和ROE。")
        self.chart_source(fig, "数据来源：research_warehouse.db，financial_report_visible；毛利率对异常金额字段做收入归一化处理。")
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        return path

    def plot_valuation(self, payload: dict[str, Any], path: Path) -> Path | None:
        data = payload["market"].get("valuation_daily")
        if not data:
            return None
        df = pd.DataFrame(data)
        df["date"] = to_datetime_series(df["trade_date"])
        fig, axes = plt.subplots(2, 1, figsize=(12, 6.2), sharex=True)
        axes[0].plot(df["date"], df["median_pe"], color=PALETTE[2], lw=1.4, label="PE TTM中位数")
        axes[0].plot(df["date"], df["median_pb"], color=PALETTE[4], lw=1.4, label="PB中位数")
        axes[0].legend(loc="lower center", bbox_to_anchor=(0.5, -0.35), ncol=2, frameon=False, fontsize=9)
        axes[1].plot(df["date"], df["median_turnover"], color=PALETTE[0], lw=1.4, label="换手率中位数")
        axes[1].plot(df["date"], df["median_dv"], color=PALETTE[6], lw=1.2, label="股息率TTM中位数")
        axes[1].legend(loc="lower center", bbox_to_anchor=(0.5, -0.35), ncol=2, frameon=False, fontsize=9)
        axes[1].xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        self.title(axes[0], f"图3：{payload['industry']}估值与交易位置", "估值分位和换手率共同判断赔率与拥挤度。")
        self.chart_source(fig, "数据来源：research_warehouse.db，stock_valuation_daily。")
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        return path

    def plot_moneyflow(self, payload: dict[str, Any], path: Path) -> Path | None:
        data = payload["market"].get("moneyflow_daily")
        if not data:
            return None
        df = pd.DataFrame(data)
        df["date"] = to_datetime_series(df["trade_date"])
        fig, ax = plt.subplots(figsize=(12, 4.6))
        ax.plot(df["date"], df["net_mf_20d"] / 1e8, color=PALETTE[0], lw=1.5, label="20日净流入")
        ax.axhline(0, color=BLACK, lw=0.8)
        ax.set_ylabel("亿元")
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.25), frameon=False, fontsize=9)
        self.title(ax, f"图4：{payload['industry']}资金面跟踪", "净流入用于确认交易边际，不能单独作为行业推荐理由。")
        self.chart_source(fig, "数据来源：research_warehouse.db，stock_moneyflow_daily。")
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        return path

    def plot_financial(self, payload: dict[str, Any], path: Path) -> Path | None:
        f = payload["financial"]
        data = f.get("financial_series")
        if not data:
            return None
        df = pd.DataFrame(data)
        df["date"] = to_datetime_series(df["end_date"])
        fig, axes = plt.subplots(2, 1, figsize=(12, 6.2), sharex=True)
        axes[0].plot(df["date"], df["revenue_sum"] / 1e8, color=PALETTE[2], lw=1.4, label="收入合计")
        axes[0].plot(df["date"], df["profit_sum"] / 1e8, color=PALETTE[0], lw=1.4, label="归母净利润合计")
        axes[0].set_ylabel("亿元")
        axes[0].legend(loc="lower center", bbox_to_anchor=(0.5, -0.35), ncol=2, frameon=False, fontsize=9)
        axes[1].plot(df["date"], df["median_gross_margin"], color=PALETTE[6], lw=1.3, label="毛利率中位数")
        axes[1].plot(df["date"], df["median_roe"], color=PALETTE[4], lw=1.3, label="ROE中位数")
        axes[1].plot(df["date"], df["median_debt_to_assets"], color=PALETTE[3], lw=1.2, label="资产负债率中位数")
        axes[1].yaxis.set_major_formatter(lambda x, pos: f"{x:.0f}%")
        axes[1].legend(loc="lower center", bbox_to_anchor=(0.5, -0.35), ncol=3, frameon=False, fontsize=9)
        axes[1].xaxis.set_major_locator(mdates.MonthLocator(interval=6))
        axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        self.title(axes[0], f"图5：{payload['industry']}行业财务验证", "用行业聚合收入，利润和利润率验证产业逻辑是否落到报表。")
        self.chart_source(fig, "数据来源：research_warehouse.db，financial_report_visible。")
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        return path

    def plot_macro(self, payload: dict[str, Any], path: Path) -> Path | None:
        data = payload["macro"].get("series")
        if not data:
            return None
        df = pd.DataFrame(data)
        df["date"] = pd.to_datetime(df["month"].astype(str) + "01", format="%Y%m%d", errors="coerce")
        fig, axes = plt.subplots(2, 1, figsize=(12, 6.0), sharex=True)
        for col, label, color in [("pmi_manufacturing", "制造业PMI", PALETTE[2]), ("pmi_non_manufacturing", "非制造业PMI", PALETTE[4])]:
            if col in df:
                axes[0].plot(df["date"], pd.to_numeric(df[col], errors="coerce"), color=color, lw=1.4, label=label)
        axes[0].axhline(50, color=BLACK, lw=0.8)
        axes[0].legend(loc="lower center", bbox_to_anchor=(0.5, -0.35), ncol=2, frameon=False, fontsize=9)
        for col, label, color in [("ppi_yoy", "PPI同比", PALETTE[0]), ("m2_yoy", "M2同比", PALETTE[6]), ("m1_yoy", "M1同比", PALETTE[5])]:
            if col in df:
                axes[1].plot(df["date"], pd.to_numeric(df[col], errors="coerce"), color=color, lw=1.3, label=label)
        axes[1].axhline(0, color=BLACK, lw=0.8)
        axes[1].legend(loc="lower center", bbox_to_anchor=(0.5, -0.35), ncol=3, frameon=False, fontsize=9)
        axes[1].xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        self.title(axes[0], f"图6：{payload['industry']}研究的宏观背景", "宏观变量只用于解释需求和分母环境，不替代行业自身数据。")
        self.chart_source(fig, "数据来源：research_warehouse.db，macro_monthly。")
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        return path

    def plot_signal(self, payload: dict[str, Any], path: Path) -> Path | None:
        data = payload["signal"].get("series")
        if not data:
            return None
        df = pd.DataFrame(data)
        df["date"] = to_datetime_series(df["rebalance_date"])
        fig, ax = plt.subplots(figsize=(12, 4.4))
        ax.plot(df["date"], pd.to_numeric(df["score"], errors="coerce"), color=PALETTE[0], lw=1.5, label="行业信号得分")
        if "target_weight" in df:
            ax.plot(df["date"], pd.to_numeric(df["target_weight"], errors="coerce"), color=PALETTE[2], lw=1.2, label="目标权重")
        ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.28), ncol=2, frameon=False, fontsize=9)
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        self.title(ax, f"图7：{payload['industry']}内部模型信号", "模型信号用于辅助判断中期景气和配置权重，正文仍以可解释数据为主。")
        self.chart_source(fig, "数据来源：research_warehouse.db，v3_industry_signal。")
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        return path

    def plot_scenario_matrix(self, payload: dict[str, Any], path: Path) -> Path:
        rows = [
            ("乐观", "需求继续改善", "利润率和ROE同步修复", "估值分位仍有上修空间", PALETTE[6]),
            ("中性", "需求温和修复", "利润弹性弱于收入弹性", "估值围绕中枢震荡", PALETTE[2]),
            ("悲观", "需求弱于预期", "利润率回落且现金流转弱", "估值和资金同步收缩", PALETTE[0]),
        ]
        fig, ax = plt.subplots(figsize=(12, 4.0))
        ax.axis("off")
        ax.text(0.01, 0.93, f"图11：{payload['industry']}中期情景推演框架", fontsize=14, fontweight="bold", color=BLACK)
        headers = ["情景", "需求假设", "利润假设", "估值假设"]
        x = [0.02, 0.18, 0.43, 0.70]
        widths = [0.12, 0.21, 0.23, 0.24]
        for i, h0 in enumerate(headers):
            ax.text(x[i] + widths[i] / 2, 0.78, h0, ha="center", va="center", fontsize=10.5, fontweight="bold")
        for r, row in enumerate(rows):
            y = 0.62 - r * 0.20
            for i, val in enumerate(row[:4]):
                face = "#ffffff" if i else "#f7f2f0"
                box = FancyBboxPatch((x[i], y - 0.065), widths[i], 0.12, boxstyle="round,pad=0.008,rounding_size=0.006",
                                     facecolor=face, edgecolor=row[4], linewidth=1.2)
                ax.add_patch(box)
                ax.text(x[i] + widths[i] / 2, y, val, ha="center", va="center", fontsize=9.5, color=BLACK)
        ax.text(0.02, 0.08, "使用方式：每次更新报告时，将当前数据放入三种情景中，观察需求、利润、估值是否同向。", fontsize=9.2, color="#666666")
        self.chart_source(fig, "数据来源：报告模型情景模板，结合行情、财务、估值、资金和宏观指标更新。")
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        return path

    def plot_data_map(self, payload: dict[str, Any], path: Path) -> Path:
        nodes = [
            ("成分股", "sw_l1_industry_daily", "确定行业样本"),
            ("行情", "stock_ohlcv_daily", "收益、波动、成交"),
            ("估值", "stock_valuation_daily", "PE、PB、PS、股息率"),
            ("资金", "stock_moneyflow_daily", "净流入和拥挤度"),
            ("财务", "financial_report_visible", "收入、利润、ROE"),
            ("宏观", "macro_monthly", "PMI、PPI、M1/M2"),
            ("信号", "v3_industry_signal", "行业轮动得分"),
            ("输出", "payload/docx/html", "Word和网页预览"),
        ]
        fig, ax = plt.subplots(figsize=(12, 4.3))
        ax.axis("off")
        ax.text(0.01, 0.93, f"图12：{payload['industry']}报告数据链路", fontsize=14, fontweight="bold", color=BLACK)
        xs = np.linspace(0.03, 0.84, len(nodes))
        for i, (name, table, use) in enumerate(nodes):
            box = FancyBboxPatch((xs[i], 0.38), 0.105, 0.28, boxstyle="round,pad=0.008,rounding_size=0.008",
                                 facecolor="white", edgecolor=PALETTE[i % len(PALETTE)], linewidth=1.2)
            ax.add_patch(box)
            ax.text(xs[i] + 0.052, 0.58, name, ha="center", va="center", fontsize=10.2, fontweight="bold")
            ax.text(xs[i] + 0.052, 0.49, table, ha="center", va="center", fontsize=7.6, color="#555555")
            ax.text(xs[i] + 0.052, 0.42, use, ha="center", va="center", fontsize=8.2, color=BLACK)
            if i < len(nodes) - 1:
                ax.annotate("", xy=(xs[i + 1] - 0.006, 0.52), xytext=(xs[i] + 0.112, 0.52),
                            arrowprops=dict(arrowstyle="->", color="#666666", lw=1.0))
        ax.text(0.02, 0.16, "稳定获取逻辑：优先读取本地正式 warehouse；缺口进入质量门和缺口清单，不用缺失指标生成伪结论。", fontsize=9.2, color="#666666")
        self.chart_source(fig, "数据来源：research_warehouse.db 与模型输出文件；外部 API 仅作为数据库更新入口，不写入报告明文凭据。")
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        return path

    def make_sections(self, payload: dict[str, Any]) -> dict[str, str]:
        industry = payload["industry"]
        fw = payload["framework"]
        m = payload["market"]
        f = payload["financial"]
        sig = payload["signal"]
        macro = payload["macro"]
        evidence = {
            "industry": industry,
            "as_of": pretty_date(payload["as_of"]),
            "industry_type": payload["industry_type"],
            "members": payload["members"]["count"],
            "cum_return": fmt_pct_ratio(m.get("cum_return")),
            "benchmark_return": fmt_pct_ratio(m.get("benchmark_cum_return")),
            "excess_return": fmt_pct_ratio((safe_float(m.get("cum_return"), 0) or 0) - (safe_float(m.get("benchmark_cum_return"), 0) or 0)),
            "ret_1m": fmt_pct_ratio(m.get("ret_1m")),
            "ret_3m": fmt_pct_ratio(m.get("ret_3m")),
            "ret_6m": fmt_pct_ratio(m.get("ret_6m")),
            "ret_12m": fmt_pct_ratio(m.get("ret_12m")),
            "pe": fmt_num(m.get("median_pe"), 1),
            "pb": fmt_num(m.get("median_pb"), 2),
            "pe_pctile": fmt_pct_ratio(m.get("median_pe_pctile")),
            "pb_pctile": fmt_pct_ratio(m.get("median_pb_pctile")),
            "turnover": fmt_num(m.get("median_turnover"), 2),
            "amount": fmt_num(m.get("latest_amount_yi"), 1),
            "moneyflow20": fmt_num(m.get("net_mf_20d_yi"), 2),
            "roe": fmt_pct_value(f.get("median_roe")),
            "roa": fmt_pct_value(f.get("median_roa")),
            "gross_margin": fmt_pct_value(f.get("median_gross_margin")),
            "net_margin": fmt_pct_value(f.get("median_net_margin")),
            "debt": fmt_pct_value(f.get("median_debt_to_assets")),
            "asset_turn": fmt_num(f.get("median_assets_turn"), 2),
            "tr_yoy": fmt_pct_value(f.get("median_tr_yoy")),
            "netprofit_yoy": fmt_pct_value(f.get("median_netprofit_yoy")),
            "profit": fmt_num(f.get("profit_sum_yi"), 1),
            "revenue": fmt_num(f.get("revenue_sum_yi"), 1),
            "latest_end": pretty_date(f.get("latest_end_date")),
            "pmi": fmt_num(macro.get("pmi_manufacturing"), 1),
            "ppi": fmt_num(macro.get("ppi_yoy"), 1),
            "m1": fmt_num(macro.get("m1_yoy"), 1),
            "m2": fmt_num(macro.get("m2_yoy"), 1),
            "signal_score": fmt_num(sig.get("latest_score"), 3),
            "target_weight": fmt_pct_ratio(sig.get("latest_target_weight")),
        }
        conclusion = (
            f"截至{evidence['as_of']}，{industry}属于{payload['industry_type']}框架，核心变量是{fw['core']}。"
            f"本地数据库覆盖{evidence['members']}只行业成分。回看期行业等权收益为{evidence['cum_return']}，全市场等权收益为{evidence['benchmark_return']}，超额收益为{evidence['excess_return']}。"
            f"财务端最新可见报告期为{evidence['latest_end']}，收入同比中位数为{evidence['tr_yoy']}，净利润同比中位数为{evidence['netprofit_yoy']}，ROE中位数为{evidence['roe']}。"
            f"定价端PE中位数为{evidence['pe']}倍，处于回看期{evidence['pe_pctile']}分位，20日资金净流入为{evidence['moneyflow20']}亿元。"
            f"结论需要同时由图2收益、图3需求、图5利润、图6估值、图7资金和图10模型信号确认；若其中两条以上转弱，行业观点应下修。"
        )
        business_chain = (
            f"{industry}的商业模式先按上游资源和要素、中游生产或服务组织、下游客户场景拆分，再判断利润池留在哪一环节。"
            f"本报告用申万一级成分的收入、利润、利润率、ROE和市场定价做聚合验证，避免只写产业叙事。"
            f"图1给出研究链路：先看需求来源，再看供给约束，再看利润传导，最后落到财务和市场定价。"
            f"对该行业，需求侧关注{fw['demand']}；供给侧关注{fw['supply']}；利润公式可以概括为{fw['profit']}。"
        )
        demand = (
            f"需求端先看总量，再看结构，再看市场是否提前定价。数据库稳定读取financial_report_visible的收入同比、净利润同比，macro_monthly的PMI、PPI、M1和M2，stock_ohlcv_daily的行业成交额与收益。"
            f"当前收入同比中位数为{evidence['tr_yoy']}，净利润同比中位数为{evidence['netprofit_yoy']}；制造业PMI为{evidence['pmi']}，PPI同比为{evidence['ppi']}。"
            f"图3若出现收入同比上行、PMI处于扩张区间、PPI改善、成交额抬升四项共振，说明需求修复更容易传导到盈利；若只有股价上涨但收入和宏观指标未跟随，持续性较弱。"
            f"短期价格验证上，行业1个月、3个月、6个月、12个月收益分别为{evidence['ret_1m']}、{evidence['ret_3m']}、{evidence['ret_6m']}、{evidence['ret_12m']}，需要和基本面方向交叉验证。"
        )
        supply = (
            f"供给端判断需求能否转化为利润。当前数据库尚未直接把所有行业的产能、开工率和库存统一入库，因此先用资产周转率、资产负债率、ROE、ROA作为资本周期代理。"
            f"最新资产周转率中位数为{evidence['asset_turn']}，资产负债率中位数为{evidence['debt']}，ROA中位数为{evidence['roa']}，ROE中位数为{evidence['roe']}。"
            f"图4若显示ROE改善但资产周转率和负债率没有同步大幅扩张，说明行业处在供给纪律较好的修复期；若ROE高位伴随负债率和资产周转快速上行，则需要警惕扩产周期压低远期回报。"
            f"后续数据库应按行业接入专属高频数据：资源品接入产能、开工率、社会库存、港口库存；消费接入渠道库存和批价；制造接入订单、排产和交付。"
        )
        price_profit = (
            f"价格和利润章节需要把量、价、成本、费用率放在同一张表里。当前行业收入合计为{evidence['revenue']}亿元，归母净利润合计为{evidence['profit']}亿元。"
            f"毛利率中位数为{evidence['gross_margin']}，净利率中位数为{evidence['net_margin']}，ROE中位数为{evidence['roe']}。"
            f"图5若出现收入扩张、利润池扩大、毛利率和净利率同时改善，说明价格或成本变量已经进入报表；若收入改善但利润率下行，行业更可能处于有量无价或成本挤压阶段。"
            f"毛利率口径已做异常处理：当原始gross_margin字段表现为金额时，以gross_margin除以total_revenue重新计算百分比，不直接展示伪百分比。"
        )
        cycle = (
            f"库存周期和资本周期用于判断中期位置。当前统一库中可直接稳定获得财务周期和交易周期，库存和产能按行业专属接口补充。"
            f"左侧修复的特征是需求指标改善、资金从净流出转向收敛、估值仍处历史中低分位、ROE尚未明显兑现；右侧兑现的特征是利润率和ROE已高位，估值分位抬升，资金拥挤。"
            f"当前PE分位为{evidence['pe_pctile']}，PB分位为{evidence['pb_pctile']}，换手率中位数为{evidence['turnover']}，20日资金净流入为{evidence['moneyflow20']}亿元。"
            f"图6和图7若同时显示估值高分位、换手上行、资金净流入放缓，说明赔率下降；若估值分位不高但盈利和资金边际改善，说明中期配置价值上升。"
        )
        policy_macro = (
            f"政策和产业趋势不单独写利好清单，而是落到需求、供给、价格、成本和估值五个位置。"
            f"当前宏观变量中，PMI为{evidence['pmi']}，PPI同比为{evidence['ppi']}，M1同比为{evidence['m1']}，M2同比为{evidence['m2']}。"
            f"图9若显示PMI回升、PPI改善、M1或M2企稳，说明需求和分母端环境较前期改善；若PPI下行而行业股价上涨，需要检查是否只是风险偏好或短期资金推动。"
            f"行业专属政策数据应从本地研报索引、公开政策文本、产业协会和交易所公告进入数据库，最终映射到收入、成本、价格、供给约束和估值折现率。"
        )
        competition = (
            f"竞争格局决定行业增长能否沉淀为利润。当前版本以{evidence['members']}只申万一级成分作为样本，使用利润率分布、ROE、收入增速和市值结构观察分化。"
            f"如果龙头利润率和ROE改善快于行业中位数，同时行业整体资金和估值没有过度拥挤，说明利润可能向优势环节集中。"
            f"数据库层后续应补充CR3、CR5、龙头份额、细分产品价格和产能份额，特别是资源品、消费品和制造业。"
            f"在未接入份额字段前，报告不直接写集中度结论，只把竞争格局作为需要继续验证的投资假设。"
        )
        financial = (
            f"财务验证是产业逻辑的验钞机。数据稳定来自financial_report_visible，并按可见日期过滤，避免使用未来财报。"
            f"最新可见报告期为{evidence['latest_end']}，收入合计为{evidence['revenue']}亿元，归母净利润合计为{evidence['profit']}亿元。"
            f"收入同比中位数为{evidence['tr_yoy']}，净利润同比中位数为{evidence['netprofit_yoy']}，毛利率中位数为{evidence['gross_margin']}，净利率中位数为{evidence['net_margin']}，ROE中位数为{evidence['roe']}。"
            f"图8若显示收入、利润、毛利率、ROE同步改善，说明行业叙事已经落入报表；若利润增速靠少数公司贡献，正式报告应在竞争格局章节下调行业层面的确定性。"
        )
        pricing = (
            f"市场定价用估值、资金和模型信号三条线确认。当前PE中位数为{evidence['pe']}倍，PB中位数为{evidence['pb']}倍，PE分位为{evidence['pe_pctile']}，PB分位为{evidence['pb_pctile']}。"
            f"交易层面最新成交额为{evidence['amount']}亿元，20日资金净流入为{evidence['moneyflow20']}亿元。内部行业轮动信号最新得分为{evidence['signal_score']}，目标权重为{evidence['target_weight']}。"
            f"图6看估值分位，图7看资金边际，图10看模型信号。若估值仍可接受、资金未明显拥挤、信号得分上行，说明市场尚未完全透支基本面。"
            f"若估值高分位叠加资金放量拥挤，但盈利指标未继续上行，则报告结论应从超配观察降为中性跟踪。"
        )
        scenario = (
            f"中期情景演绎不写单一结论，而是把需求、利润、估值和跟踪动作放在同一张矩阵里。"
            f"乐观情景要求需求、利润率、ROE和资金信号共振；中性情景对应收入改善但利润率修复不足；悲观情景对应需求弱于预期、利润率回落、估值和资金同步收缩。"
            f"图11用于每次更新时复核情景位置。图12列出数据链路，要求所有正式结论都能追溯到本地warehouse表、图表或质量门。"
            f"跟踪指标分为五类：需求看收入同比、PMI、订单或销量；供给看产能、库存、资本开支或资产周转；利润看毛利率、净利率、ROE；交易看估值分位、换手、资金；风险看反证信号。"
        )
        data_logic = (
            f"数据获取优先使用本地research_warehouse.db，稳定表包括sw_l1_industry_daily、stock_ohlcv_daily、stock_valuation_daily、stock_moneyflow_daily、financial_report_visible、macro_monthly和v3_industry_signal。"
            f"外部API只作为数据库更新入口，不在报告文件中写入账号、密码、token或cookie。缺失字段进入质量门和缺口清单，不能生成伪结论。"
            f"行业专属高频数据按行业分层接入：资源品接商品价格和库存，消费接批价、渠道库存和客流，制造接订单、排产和产能利用率，金融接利差、资产质量和成交额，医药接医保支付、集采和院内恢复。"
            f"每张图都必须回答一个研究问题：图形趋势是否改善、改善是否同步、是否已经被估值和资金定价、是否存在反证。"
        )
        return {
            "investment_conclusion": self.ai.rewrite("投资结论", evidence, conclusion),
            "business_chain": self.ai.rewrite("商业模式与产业链", evidence, business_chain),
            "demand_analysis": self.ai.rewrite("需求端拆解", evidence, demand),
            "supply_analysis": self.ai.rewrite("供给端拆解", evidence, supply),
            "price_profit": self.ai.rewrite("价格机制与利润弹性", evidence, price_profit),
            "cycle_position": self.ai.rewrite("库存周期与资本周期", evidence, cycle),
            "policy_macro": self.ai.rewrite("政策与产业趋势", evidence, policy_macro),
            "competition": self.ai.rewrite("竞争格局", evidence, competition),
            "financial_validation": self.ai.rewrite("财务验证", evidence, financial),
            "market_pricing": self.ai.rewrite("估值与市场定价", evidence, pricing),
            "scenario_tracking": self.ai.rewrite("情景演绎与跟踪", evidence, scenario),
            "data_logic": data_logic,
        }

    def set_run_font(self, run, size: float = 10.5, bold: bool = False) -> None:
        run.font.name = "Arial"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "楷体")
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = RGBColor(0, 0, 0)

    def set_para(self, paragraph, first_line: bool = True, align=None) -> None:
        fmt = paragraph.paragraph_format
        fmt.line_spacing = 1
        fmt.space_before = Pt(0)
        fmt.space_after = Pt(0)
        if first_line:
            fmt.first_line_indent = Cm(0.74)
        if align is not None:
            paragraph.alignment = align

    def add_p(self, doc: Document, text: str = "", size: float = 10.5, bold: bool = False, first_line: bool = True, align=None):
        p = doc.add_paragraph()
        self.set_para(p, first_line=first_line, align=align)
        r = p.add_run(text)
        self.set_run_font(r, size=size, bold=bold)
        return p

    def add_h(self, doc: Document, text: str, level: int = 1):
        p = doc.add_paragraph()
        self.set_para(p, first_line=False)
        r = p.add_run(text)
        self.set_run_font(r, size=15 if level == 1 else 12, bold=True)

    def add_fig(self, doc: Document, path: str | None, conclusion: str) -> None:
        if path and Path(path).exists():
            p = doc.add_paragraph()
            self.set_para(p, first_line=False, align=WD_ALIGN_PARAGRAPH.CENTER)
            p.add_run().add_picture(path, width=Cm(15.8))
            self.add_p(doc, f"图表结论：{conclusion}", size=10.5)

    def add_table(self, doc: Document, headers: list[str], rows: list[list[Any]]) -> None:
        table = doc.add_table(rows=1, cols=len(headers))
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.style = "Table Grid"
        for i, h in enumerate(headers):
            self.set_cell(table.rows[0].cells[i], h, bold=True)
        for row in rows:
            cells = table.add_row().cells
            for i, value in enumerate(row):
                self.set_cell(cells[i], value)
        for row in table.rows:
            for cell in row.cells:
                tc_pr = cell._tc.get_or_add_tcPr()
                tc_mar = OxmlElement("w:tcMar")
                for m in ["top", "left", "bottom", "right"]:
                    node = OxmlElement(f"w:{m}")
                    node.set(qn("w:w"), "70")
                    node.set(qn("w:type"), "dxa")
                    tc_mar.append(node)
                tc_pr.append(tc_mar)

    def set_cell(self, cell, text: Any, bold: bool = False) -> None:
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        cell.text = ""
        p = cell.paragraphs[0]
        self.set_para(p, first_line=False)
        r = p.add_run(str(text))
        self.set_run_font(r, size=9.5, bold=bold)

    def configure_doc(self, doc: Document) -> None:
        section = doc.sections[0]
        section.top_margin = Cm(2.0)
        section.bottom_margin = Cm(1.8)
        section.left_margin = Cm(2.2)
        section.right_margin = Cm(2.0)
        for style_name in ["Normal", "Body Text"]:
            style = doc.styles[style_name]
            style.font.name = "Arial"
            style._element.rPr.rFonts.set(qn("w:eastAsia"), "楷体")
            style.font.size = Pt(10.5)
            style.font.color.rgb = RGBColor(0, 0, 0)
            style.paragraph_format.line_spacing = 1
            style.paragraph_format.space_after = Pt(0)

    def write_docx(self, payload: dict[str, Any], out_dir: Path) -> Path:
        doc = Document()
        self.configure_doc(doc)
        industry = payload["industry"]
        fw = payload["framework"]
        m = payload["market"]
        f = payload["financial"]
        macro = payload["macro"]
        figs = payload.get("figures", {})
        sections = payload.get("sections", {})

        p = doc.add_paragraph()
        self.set_para(p, first_line=False, align=WD_ALIGN_PARAGRAPH.CENTER)
        r = p.add_run(f"{industry}行业深度研究报告")
        self.set_run_font(r, size=18, bold=True)
        self.add_p(doc, f"报告日期：{pretty_date(payload['as_of'])}    回看区间：{pretty_date(payload['start_date'])} 至 {pretty_date(payload['as_of'])}", first_line=False, align=WD_ALIGN_PARAGRAPH.CENTER)
        self.add_p(doc, "数据来源：本地 research_warehouse.db。外部API和AI网关未写入任何明文凭据。", first_line=False)

        self.add_h(doc, "一 投资结论", 1)
        self.add_p(doc, sections.get("investment_conclusion", ""))
        view = "超配观察" if safe_float(m.get("cum_return"), 0) > safe_float(m.get("benchmark_cum_return"), 0) and safe_float(f.get("median_netprofit_yoy"), 0) > 0 else "中性跟踪"
        self.add_table(doc, ["项目", "当前读数", "研究含义"], [
            ["行业定位", payload["industry_type"], fw["core"]],
            ["行业样本", f"{payload['members']['count']}只成分", "使用申万一级当前成分聚合"],
            ["回看收益", fmt_pct_ratio(m.get("cum_return")), "观察市场定价强度"],
            ["全市场等权", fmt_pct_ratio(m.get("benchmark_cum_return")), "作为宽基参照"],
            ["PE中位数", f"{fmt_num(m.get('median_pe'), 1)}倍", f"回看期分位{fmt_pct_ratio(m.get('median_pe_pctile'))}"],
            ["财务状态", f"ROE中位数{fmt_pct_value(f.get('median_roe'))}", f"净利润同比中位数{fmt_pct_value(f.get('median_netprofit_yoy'))}"],
            ["中期结论", view, "由景气，财务，估值和资金共同决定"],
        ])
        self.add_fig(doc, figs.get("framework"), "报告按照行业边界，需求，供给，利润，财务和定价六步展开。")

        self.add_h(doc, "二 商业模式与产业链", 1)
        self.add_p(doc, f"{industry}属于申万一级行业，本报告只研究行业层面，不进行个股推荐。报告使用行业成分股的行情，估值，资金流和财报做聚合验证。")
        self.add_p(doc, f"该行业在本框架中归为{payload['industry_type']}。核心变量为{fw['core']}。研究边界是未来6到18个月的中期机会，不讨论长期终局和个股估值。")

        self.add_h(doc, "三 商业模式与产业链", 1)
        self.add_p(doc, fw["demand"])
        self.add_p(doc, fw["supply"])
        self.add_p(doc, fw["profit"])
        self.add_p(doc, "产业链研究需要识别收入最大环节，利润率最高环节，现金流最好环节和议价权最强环节。行业机会只有在利润能沉淀时才具备中期配置价值。")

        self.add_h(doc, "四 需求端拆解", 1)
        self.add_p(doc, f"{industry}需求分析需要先拆总量，再拆结构。总量看行业收入和成交活跃度，结构看下游场景和价格带变化。")
        self.add_p(doc, f"本轮数据中，行业回看期等权收益为{fmt_pct_ratio(m.get('cum_return'))}，最近1个月收益为{fmt_pct_ratio(m.get('ret_1m'))}，最近3个月收益为{fmt_pct_ratio(m.get('ret_3m'))}。价格表现说明市场已经对部分需求或景气变化作出反应。")
        self.add_fig(doc, figs.get("returns"), "行业收益曲线用于确认市场是否已经开始定价景气变化。")

        self.add_h(doc, "五 供给端拆解", 1)
        self.add_p(doc, f"{industry}供给端需要看产能，库存，资本开支和进入壁垒。当前数据库不直接存放全部产业库存和产能数据，因此本版使用行业财务中的资产周转率，资产负债率和ROE作为资本周期代理。")
        self.add_p(doc, f"最新报告期行业资产周转率中位数为{fmt_num(f.get('median_assets_turn'), 2)}，资产负债率中位数为{fmt_pct_value(f.get('median_debt_to_assets'))}。如果后续ROE改善伴随资本开支快速上升，需要警惕供给扩张压低中期赔率。")

        self.add_h(doc, "六 价格机制与利润弹性", 1)
        self.add_p(doc, f"{industry}利润弹性需要拆成量，价，成本和费用率。当前行业毛利率中位数为{fmt_pct_value(f.get('median_gross_margin'))}，净利率中位数为{fmt_pct_value(f.get('median_net_margin'))}。")
        self.add_p(doc, "价格机制章节在正式使用时应接入该行业专属高频指标。资源品接入商品价格和库存，消费接入批价和渠道库存，制造接入订单和排产，金融接入利差和成交额。")

        self.add_h(doc, "七 库存周期与资本周期", 1)
        self.add_p(doc, "库存周期决定短期节奏，资本周期决定中期利润中枢。最优状态是需求改善，库存低位，供给扩不出来。风险状态是盈利高位后全行业扩产。")
        self.add_p(doc, sections.get("financial_validation", ""))
        self.add_fig(doc, figs.get("financial"), "财报聚合验证行业逻辑是否已经落到收入，利润和ROE。")

        self.add_h(doc, "八 政策与产业趋势", 1)
        self.add_p(doc, f"{industry}政策分析需要落到收入，成本，价格，供给和估值五个位置。不能只写政策利好。")
        self.add_p(doc, f"宏观背景方面，制造业PMI最新有效值为{fmt_num(macro.get('pmi_manufacturing'), 1)}，PPI同比最新有效值为{fmt_num(macro.get('ppi_yoy'), 1)}，M2同比最新有效值为{fmt_num(macro.get('m2_yoy'), 1)}。这些变量用于校验需求和估值分母。")
        self.add_fig(doc, figs.get("macro"), "宏观图用于解释行业所处的增长，价格和流动性环境。")

        self.add_h(doc, "九 竞争格局", 1)
        self.add_p(doc, "竞争格局判断行业增长能否变成行业利润。需要看集中度，份额变化，价格战，议价权和盈利分化。")
        self.add_p(doc, f"本版报告以{payload['members']['count']}只成分股作为行业样本。后续可在数据库层加入CR3，CR5，龙头份额和细分产品价格，以增强格局分析。")

        self.add_h(doc, "十 行业财务验证", 1)
        self.add_p(doc, sections.get("financial_validation", ""))
        self.add_table(doc, ["财务指标", "最新读数", "研究含义"], [
            ["收入合计", f"{fmt_num(f.get('revenue_sum_yi'), 1)}亿元", "观察行业规模"],
            ["归母净利润合计", f"{fmt_num(f.get('profit_sum_yi'), 1)}亿元", "观察利润池"],
            ["收入同比中位数", fmt_pct_value(f.get("median_tr_yoy")), "观察景气传导"],
            ["净利润同比中位数", fmt_pct_value(f.get("median_netprofit_yoy")), "观察盈利弹性"],
            ["毛利率中位数", fmt_pct_value(f.get("median_gross_margin")), "观察价格成本关系"],
            ["ROE中位数", fmt_pct_value(f.get("median_roe")), "观察资本回报"],
            ["资产负债率中位数", fmt_pct_value(f.get("median_debt_to_assets")), "观察财务承压"],
        ])

        self.add_h(doc, "十一 估值与市场定价", 1)
        self.add_p(doc, sections.get("market_pricing", ""))
        self.add_fig(doc, figs.get("valuation"), "估值图用于判断市场是否已经充分反映中期盈利变化。")
        self.add_fig(doc, figs.get("moneyflow"), "资金面图用于观察交易边际和拥挤度变化。")
        self.add_fig(doc, figs.get("signal"), "内部模型信号用于辅助观察行业中期配置权重变化。")

        self.add_h(doc, "十二 中期情景推演", 1)
        self.add_table(doc, ["情景", "需求假设", "利润假设", "估值假设", "跟踪动作"], [
            ["乐观", "需求继续改善，价格或订单保持强势", "毛利率和ROE同步修复", "估值分位仍可上行", "提高行业结论强度"],
            ["中性", "需求温和修复，价格平稳", "收入改善快于利润率", "估值围绕中枢震荡", "维持跟踪或标配"],
            ["悲观", "需求低于预期，库存或价格承压", "净利润同比回落，现金流转弱", "估值和资金同步收缩", "下调行业判断"],
        ])

        self.add_h(doc, "十三 跟踪指标与反证", 1)
        self.add_p(doc, "后续跟踪应分成需求，价格，供给，利润，交易五类指标。需求看销量，订单，客流，招标，出口。价格看产品价格，服务价格，利差，费率。供给看产能，开工，库存，资本开支。利润看毛利率，ROE，现金流。交易看估值分位，成交额占比，换手率和资金流。")
        self.add_p(doc, "反证信号包括收入同比转弱，净利润同比低于收入，毛利率连续下行，ROE回落，估值高位但资金净流出，行业信号得分下行。出现两项以上时，应下修行业结论。")

        self.add_h(doc, "附录 数据口径", 1)
        self.add_table(doc, ["数据模块", "数据库表", "用途"], [
            ["成分股", "sw_l1_industry_daily", "确定申万一级行业样本"],
            ["行情", "stock_ohlcv_daily", "计算行业等权收益和成交额"],
            ["估值", "stock_valuation_daily", "计算PE，PB，PS，股息率和换手"],
            ["资金流", "stock_moneyflow_daily", "计算行业资金流"],
            ["财报", "financial_report_visible", "计算行业收入，利润率和ROE"],
            ["宏观", "macro_monthly", "校验增长，价格和流动性"],
            ["模型信号", "v3_industry_signal", "辅助中期配置判断"],
            ["研报索引", "broker_report_index", "记录本地研报证据"],
        ])
        self.add_p(doc, "本报告未把任何API密钥，账号，密码，token写入文件。若启用AI，AI只接收结构化摘要，不接收明文凭据。")

        docx_path = out_dir / f"{industry}行业深度研究报告_{payload['as_of']}.docx"
        doc.save(docx_path)
        return docx_path





# === INDUSTRY REPORT V3 OVERRIDES START ===

REPORT_PALETTE_V3 = ["#C71E37", "#7F7F7F", "#ED7D31", "#FFC000", "#A5A5A5", "#F0B183", "#7030A0", "#00B050", "#2F75B5", "#5B9BD5"]
REPORT_RED = REPORT_PALETTE_V3[0]
REPORT_GRAY = REPORT_PALETTE_V3[1]


def _v3_choose_font() -> str:
    try:
        names = {f.name for f in matplotlib.font_manager.fontManager.ttflist}
        for name in ["楷体", "KaiTi", "Microsoft YaHei", "SimHei", "STKaiti"]:
            if name in names:
                return name
    except Exception:
        pass
    return "DejaVu Sans"


REPORT_CN_FONT = _v3_choose_font()
plt.rcParams.update({
    "font.family": [REPORT_CN_FONT, "Arial"],
    "axes.unicode_minus": False,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "savefig.facecolor": "white",
    "axes.edgecolor": "#000000",
    "axes.labelcolor": "#000000",
    "xtick.color": "#000000",
    "ytick.color": "#000000",
    "text.color": "#000000",
    "legend.frameon": False,
    "figure.dpi": 170,
})


def _v3_clean_text(text: str) -> str:
    banned = {
        "数据来源": "",
        "数据源": "",
        "数据支撑": "",
        "质量门": "",
        "本地数据库": "",
        "research_warehouse.db": "",
        "API": "",
        "token": "",
        "提示词": "",
        "假设": "情形",
        "若": "当",
        "如果": "当",
        "可能": "",
        "或许": "",
        "不确定": "",
        "图表结论：": "",
        "图表结论": "",
        "；": "。",
        "、": "，",
        "“": "",
        "”": "",
        "\"": "",
    }
    value = str(text or "")
    for old, new in banned.items():
        value = value.replace(old, new)
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"。{2,}", "。", value)
    return value.strip("。") + ("。" if value.strip("。") else "")


def _v3_scalar(value: Any, default: float = 0.0) -> float:
    x = safe_float(value)
    return default if x is None else float(x)


def _v3_pct(value: Any, digits: int = 1, ratio: bool = False) -> str:
    x = safe_float(value)
    if x is None:
        return "-"
    if ratio:
        x *= 100
    return f"{x:.{digits}f}%"


def _v3_num(value: Any, digits: int = 1) -> str:
    x = safe_float(value)
    return "-" if x is None else f"{x:.{digits}f}"


def _v3_records(payload: dict[str, Any], bucket: str, key: str) -> pd.DataFrame:
    data = (payload.get(bucket) or {}).get(key) or []
    return pd.DataFrame(data)


def _v3_date_col(df: pd.DataFrame, col: str) -> pd.Series:
    return to_datetime_series(df[col]) if col in df else pd.Series([], dtype="datetime64[ns]")


def _v3_base100(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)
    valid = s.dropna()
    if valid.empty:
        return s * np.nan
    base = valid.iloc[0]
    if abs(base) < 1e-12:
        base = 1.0
    return s / base * 100


def _v3_zscore(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)
    if s.dropna().empty:
        return s * np.nan
    std = s.std(skipna=True)
    if not std or np.isnan(std):
        return s * 0
    return (s - s.mean(skipna=True)) / std


def _v3_clip_score(x: float, scale: float = 1.0) -> float:
    try:
        return float(np.tanh(float(x) / scale))
    except Exception:
        return 0.0


def _v3_style_axis(ax, grid: bool = False) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_linewidth(0.8)
    ax.spines["bottom"].set_linewidth(0.8)
    ax.tick_params(axis="both", labelsize=8.5, length=3, width=0.7)
    if grid:
        ax.grid(axis="y", color="#BFBFBF", lw=0.45, alpha=0.55)
    else:
        ax.grid(False)


def _v3_title(ax, title: str, subtitle: str = "") -> None:
    ax.set_title(title, loc="left", fontsize=13.2, fontweight="bold", color="#000000", pad=10)
    if subtitle:
        ax.text(0, 1.018, subtitle, transform=ax.transAxes, ha="left", va="bottom", fontsize=8.8, color="#555555")


def _v3_legend(ax, ncol: int = 2) -> None:
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.26), ncol=ncol, frameon=False, fontsize=8.6)


def _v3_date_axis(ax, df_len: int = 24) -> None:
    interval = max(1, int(df_len / 8))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=interval))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))


def _v3_save(fig: plt.Figure, path: Path) -> Path:
    for ax in fig.axes:
        for label in ax.get_xticklabels():
            label.set_rotation(0)
            label.set_ha("center")
    fig.tight_layout(rect=[0.01, 0.04, 0.99, 0.98])
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def _v3_last_annotate(ax, x, y, text: str, color: str = REPORT_RED) -> None:
    try:
        if pd.isna(y):
            return
        ax.scatter([x], [y], color=color, s=22, zorder=5)
        ax.annotate(text, xy=(x, y), xytext=(7, 0), textcoords="offset points", va="center", fontsize=8.2, color=color)
    except Exception:
        return


def _v3_rewrite(self, section_name: str, evidence: dict[str, Any], fallback: str) -> str:
    fallback = _v3_clean_text(fallback)
    if not self.available():
        return fallback
    system = (
        "你是中国卖方行业研究员。只根据用户给出的结构化证据写正式研报正文。"
        "每次只输出一段，最多两句，直接进入行业判断。"
        "禁止提及数据来源、数据库、质量门、API、AI、提示词。"
        "禁止使用若、假设、可能、或许、不确定、不是而是、引号和顿号。"
        "禁止新增证据之外的数字。语言专业，短句，确认语气。"
    )
    user = {
        "section": section_name,
        "style": "60到130字，一张图对应一段分析，先写结论再写原因。",
        "evidence": evidence,
        "draft": fallback,
    }
    try:
        resp = requests.post(
            f"{self.base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
                ],
                "reasoning_effort": self.reasoning_effort,
                "temperature": 0.05,
            },
            timeout=120,
        )
        resp.raise_for_status()
        text = _v3_clean_text(resp.json()["choices"][0]["message"]["content"].strip())
        banned = ["数据来源", "数据源", "质量门", "本地数据库", "API", "AI", "提示词", "若", "假设", "可能", "或许", "不确定", "不是而是", "、", "“", "”", "?"]
        if not text or len(text) < 35 or len(text) > 180 or any(w in text for w in banned):
            return fallback
        return text
    except Exception:
        return fallback


def _v3_summarize_financial(self, fin: pd.DataFrame) -> dict[str, Any]:
    if fin.empty:
        return {"available": False}
    df = fin.copy()
    cols = ["total_revenue", "n_income_attr_p", "gross_margin", "netprofit_margin", "roe", "roa", "debt_to_assets", "assets_turn", "op_yoy", "tr_yoy", "netprofit_yoy"]
    for c in cols:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    gross_raw = df["gross_margin"]
    revenue = df["total_revenue"].replace(0, np.nan)
    gross_from_amount = gross_raw / revenue * 100
    df["gross_margin_pct"] = np.where(gross_raw.abs() > 200, gross_from_amount, gross_raw)
    df.loc[(df["gross_margin_pct"] < -100) | (df["gross_margin_pct"] > 100), "gross_margin_pct"] = np.nan
    df["equity_multiplier"] = 1 / (1 - pd.to_numeric(df["debt_to_assets"], errors="coerce") / 100)
    df.loc[(df["equity_multiplier"] <= 0) | (df["equity_multiplier"] > 20), "equity_multiplier"] = np.nan
    agg = df.groupby("end_date", as_index=False).agg(
        sample=("ts_code", "nunique"),
        revenue_sum=("total_revenue", "sum"),
        profit_sum=("n_income_attr_p", "sum"),
        median_gross_margin=("gross_margin_pct", "median"),
        median_net_margin=("netprofit_margin", "median"),
        median_roe=("roe", "median"),
        median_roa=("roa", "median"),
        median_debt_to_assets=("debt_to_assets", "median"),
        median_assets_turn=("assets_turn", "median"),
        median_equity_multiplier=("equity_multiplier", "median"),
        median_op_yoy=("op_yoy", "median"),
        median_tr_yoy=("tr_yoy", "median"),
        median_netprofit_yoy=("netprofit_yoy", "median"),
    ).sort_values("end_date")
    latest = agg.tail(1).iloc[0]
    latest_end = str(latest["end_date"])
    latest_company = df[df["end_date"].astype(str) == latest_end].copy()
    keep = ["ts_code", "total_revenue", "n_income_attr_p", "gross_margin_pct", "netprofit_margin", "roe", "roa", "debt_to_assets", "assets_turn", "tr_yoy", "netprofit_yoy", "equity_multiplier"]
    latest_company = latest_company[[c for c in keep if c in latest_company.columns]].replace([np.inf, -np.inf], np.nan)
    annual = agg[agg["end_date"].astype(str).str.endswith("1231")]
    return {
        "available": True,
        "financial_series": agg.to_dict("records"),
        "annual_series": annual.to_dict("records"),
        "latest_company": latest_company.to_dict("records"),
        "latest_end_date": latest_end,
        "latest_sample": int(latest["sample"]),
        "revenue_sum_yi": safe_float(latest["revenue_sum"], 0) / 1e8,
        "profit_sum_yi": safe_float(latest["profit_sum"], 0) / 1e8,
        "median_gross_margin": safe_float(latest["median_gross_margin"]),
        "median_net_margin": safe_float(latest["median_net_margin"]),
        "median_roe": safe_float(latest["median_roe"]),
        "median_roa": safe_float(latest["median_roa"]),
        "median_debt_to_assets": safe_float(latest["median_debt_to_assets"]),
        "median_assets_turn": safe_float(latest["median_assets_turn"]),
        "median_equity_multiplier": safe_float(latest["median_equity_multiplier"]),
        "median_op_yoy": safe_float(latest["median_op_yoy"]),
        "median_tr_yoy": safe_float(latest["median_tr_yoy"]),
        "median_netprofit_yoy": safe_float(latest["median_netprofit_yoy"]),
    }


def _v3_plot_conclusion(self, payload: dict[str, Any], path: Path) -> Path:
    m, f = payload["market"], payload["financial"]
    excess = _v3_scalar(m.get("cum_return")) - _v3_scalar(m.get("benchmark_cum_return"))
    metrics = [
        ("相对收益", _v3_clip_score(excess, 0.35), _v3_pct(excess, ratio=True)),
        ("收入景气", _v3_clip_score(_v3_scalar(f.get("median_tr_yoy")), 25), _v3_pct(f.get("median_tr_yoy"))),
        ("利润景气", _v3_clip_score(_v3_scalar(f.get("median_netprofit_yoy")), 40), _v3_pct(f.get("median_netprofit_yoy"))),
        ("资本回报", _v3_clip_score(_v3_scalar(f.get("median_roe")) - 6, 8), _v3_pct(f.get("median_roe"))),
        ("估值赔率", 1 - 2 * _v3_scalar(m.get("median_pe_pctile"), 0.5), _v3_pct(m.get("median_pe_pctile"), ratio=True)),
        ("资金边际", _v3_clip_score(_v3_scalar(m.get("net_mf_20d_yi")), 15), f"{_v3_num(m.get('net_mf_20d_yi'), 1)}亿元"),
    ]
    labels = [x[0] for x in metrics]
    scores = [x[1] for x in metrics]
    fig, ax = plt.subplots(figsize=(10.8, 4.5))
    colors = [REPORT_RED if x >= 0 else REPORT_GRAY for x in scores]
    ax.barh(labels, scores, color=colors, height=0.58)
    ax.axvline(0, color="#000000", lw=0.8)
    ax.set_xlim(-1.05, 1.05)
    ax.set_xlabel("弱                                                    强", fontsize=9)
    for i, (_, score, label) in enumerate(metrics):
        ax.text(score + (0.035 if score >= 0 else -0.035), i, label, va="center", ha="left" if score >= 0 else "right", fontsize=9.2)
    _v3_title(ax, f"图1 {payload['industry']}中期指标总览", "收益，盈利，估值和资金统一压缩为可比强弱刻度")
    _v3_style_axis(ax, grid=False)
    return _v3_save(fig, path)


def _v3_plot_relative_return(self, payload: dict[str, Any], path: Path) -> Path | None:
    ind = _v3_records(payload, "market", "industry_daily")
    bench = _v3_records(payload, "market", "benchmark_daily")
    if ind.empty or bench.empty:
        return None
    ind["date"] = _v3_date_col(ind, "trade_date")
    bench["date"] = _v3_date_col(bench, "trade_date")
    fig, ax = plt.subplots(figsize=(10.8, 4.6))
    ax.plot(ind["date"], (pd.to_numeric(ind["cum_return"], errors="coerce") + 1) * 100, color=REPORT_RED, lw=1.7, label="行业等权")
    ax.plot(bench["date"], (pd.to_numeric(bench["cum_return"], errors="coerce") + 1) * 100, color="#7F7F7F", lw=1.3, label="全市场等权")
    merged = pd.merge(ind[["trade_date", "date", "cum_return"]], bench[["trade_date", "cum_return"]], on="trade_date", how="inner", suffixes=("_ind", "_bench"))
    if not merged.empty:
        excess = (pd.to_numeric(merged["cum_return_ind"], errors="coerce") - pd.to_numeric(merged["cum_return_bench"], errors="coerce")) * 100
        ax.fill_between(merged["date"], 100, 100 + excess, color="#F0B183", alpha=0.25, label="相对收益")
    _v3_last_annotate(ax, ind["date"].iloc[-1], (pd.to_numeric(ind["cum_return"], errors="coerce").iloc[-1] + 1) * 100, _v3_pct(ind["cum_return"].iloc[-1], ratio=True))
    ax.set_ylabel("期初为100")
    _v3_date_axis(ax, len(ind))
    _v3_legend(ax, 3)
    _v3_title(ax, f"图2 {payload['industry']}区间收益对比", "行业表现先回答市场是否已提前定价")
    _v3_style_axis(ax, grid=True)
    return _v3_save(fig, path)


def _v3_plot_drawdown(self, payload: dict[str, Any], path: Path) -> Path | None:
    ind = _v3_records(payload, "market", "industry_daily")
    bench = _v3_records(payload, "market", "benchmark_daily")
    if ind.empty or bench.empty:
        return None
    ind["date"] = _v3_date_col(ind, "trade_date")
    nav = (1 + pd.to_numeric(ind["cum_return"], errors="coerce")).replace(0, np.nan)
    draw = nav / nav.cummax() - 1
    merged = pd.merge(ind[["trade_date", "date", "ret"]], bench[["trade_date", "ret"]], on="trade_date", how="inner", suffixes=("_ind", "_bench"))
    merged["滚动超额"] = (pd.to_numeric(merged["ret_ind"], errors="coerce") - pd.to_numeric(merged["ret_bench"], errors="coerce")).rolling(60, min_periods=20).sum() * 100
    fig, axes = plt.subplots(2, 1, figsize=(10.8, 5.3), sharex=True, height_ratios=[1, 1])
    axes[0].fill_between(ind["date"], draw * 100, 0, color="#A5A5A5", alpha=0.55)
    axes[0].axhline(0, color="#000000", lw=0.8)
    axes[0].set_ylabel("回撤")
    axes[0].yaxis.set_major_formatter(lambda x, pos: f"{x:.0f}%")
    axes[1].plot(merged["date"], merged["滚动超额"], color=REPORT_RED, lw=1.5)
    axes[1].axhline(0, color="#000000", lw=0.8)
    axes[1].set_ylabel("60日超额")
    axes[1].yaxis.set_major_formatter(lambda x, pos: f"{x:.0f}%")
    _v3_date_axis(axes[1], len(ind))
    _v3_title(axes[0], f"图3 {payload['industry']}回撤与相对动量", "回撤约束赔率，滚动超额刻画边际定价")
    for ax in axes:
        _v3_style_axis(ax, grid=True)
    return _v3_save(fig, path)


def _v3_plot_demand_financial(self, payload: dict[str, Any], path: Path) -> Path | None:
    df = _v3_records(payload, "financial", "financial_series")
    if df.empty:
        return None
    df["date"] = _v3_date_col(df, "end_date")
    fig, ax = plt.subplots(figsize=(10.8, 4.5))
    ax.plot(df["date"], pd.to_numeric(df["median_tr_yoy"], errors="coerce"), color=REPORT_RED, lw=1.6, marker="o", ms=3.2, label="收入同比中位数")
    ax.plot(df["date"], pd.to_numeric(df["median_netprofit_yoy"], errors="coerce"), color="#2F75B5", lw=1.4, marker="o", ms=3.0, label="利润同比中位数")
    ax.axhline(0, color="#000000", lw=0.8)
    ax.set_ylabel("同比")
    ax.yaxis.set_major_formatter(lambda x, pos: f"{x:.0f}%")
    _v3_last_annotate(ax, df["date"].iloc[-1], pd.to_numeric(df["median_tr_yoy"], errors="coerce").iloc[-1], _v3_pct(df["median_tr_yoy"].iloc[-1]))
    _v3_date_axis(ax, len(df))
    _v3_legend(ax, 2)
    _v3_title(ax, f"图4 {payload['industry']}需求进入报表的力度", "收入代表需求，利润代表价格和成本后的兑现")
    _v3_style_axis(ax, grid=True)
    return _v3_save(fig, path)


def _v3_plot_macro_cycle(self, payload: dict[str, Any], path: Path) -> Path | None:
    df = _v3_records(payload, "macro", "series")
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["month"].astype(str) + "01", format="%Y%m%d", errors="coerce")
    cols = [("pmi_manufacturing", "制造业景气", REPORT_RED), ("ppi_yoy", "价格环境", "#ED7D31"), ("m2_yoy", "流动性", "#2F75B5")]
    fig, ax = plt.subplots(figsize=(10.8, 4.5))
    for col, label, color in cols:
        if col in df:
            ax.plot(df["date"], _v3_zscore(df[col]).rolling(3, min_periods=1).mean(), color=color, lw=1.5, label=label)
    ax.axhline(0, color="#000000", lw=0.8)
    ax.set_ylabel("标准化强弱")
    _v3_date_axis(ax, len(df))
    _v3_legend(ax, 3)
    _v3_title(ax, f"图5 {payload['industry']}外部景气坐标", "不同量纲统一为标准化强弱，避免左右轴失真")
    _v3_style_axis(ax, grid=True)
    return _v3_save(fig, path)


def _v3_plot_supply_capital(self, payload: dict[str, Any], path: Path) -> Path | None:
    df = _v3_records(payload, "financial", "financial_series")
    if df.empty:
        return None
    df["date"] = _v3_date_col(df, "end_date")
    x = pd.to_numeric(df["median_assets_turn"], errors="coerce")
    y = pd.to_numeric(df["median_roe"], errors="coerce")
    size = pd.to_numeric(df["median_debt_to_assets"], errors="coerce").clip(lower=20, upper=90)
    fig, ax = plt.subplots(figsize=(9.8, 5.0))
    ax.scatter(x, y, s=size * 5, c=np.arange(len(df)), cmap="Greys", alpha=0.55, edgecolors="#7F7F7F", linewidths=0.5)
    ax.scatter([x.iloc[-1]], [y.iloc[-1]], s=float(size.iloc[-1] * 6 if not pd.isna(size.iloc[-1]) else 260), color=REPORT_RED, edgecolors="#000000", linewidths=0.6, zorder=4)
    ax.axhline(y.median(skipna=True), color="#BFBFBF", lw=0.8)
    ax.axvline(x.median(skipna=True), color="#BFBFBF", lw=0.8)
    _v3_last_annotate(ax, x.iloc[-1], y.iloc[-1], "最新")
    ax.set_xlabel("资产周转率")
    ax.set_ylabel("ROE")
    ax.yaxis.set_major_formatter(lambda v, pos: f"{v:.0f}%")
    _v3_title(ax, f"图6 {payload['industry']}资本周期定位", "气泡越大代表杠杆越高，红点为最新报告期")
    _v3_style_axis(ax, grid=True)
    return _v3_save(fig, path)


def _v3_plot_profit_pool(self, payload: dict[str, Any], path: Path) -> Path | None:
    df = _v3_records(payload, "financial", "financial_series")
    if df.empty:
        return None
    df["date"] = _v3_date_col(df, "end_date")
    fig, axes = plt.subplots(2, 1, figsize=(10.8, 5.6), sharex=True, height_ratios=[1.1, 1])
    axes[0].plot(df["date"], _v3_base100(df["revenue_sum"]), color=REPORT_RED, lw=1.6, marker="o", ms=3, label="收入规模")
    axes[0].plot(df["date"], _v3_base100(df["profit_sum"]), color="#2F75B5", lw=1.5, marker="o", ms=3, label="利润池")
    axes[0].set_ylabel("期初为100")
    axes[1].plot(df["date"], pd.to_numeric(df["median_gross_margin"], errors="coerce"), color="#ED7D31", lw=1.5, label="毛利率")
    axes[1].plot(df["date"], pd.to_numeric(df["median_net_margin"], errors="coerce"), color=REPORT_RED, lw=1.5, label="净利率")
    axes[1].axhline(0, color="#000000", lw=0.8)
    axes[1].set_ylabel("利润率")
    axes[1].yaxis.set_major_formatter(lambda v, pos: f"{v:.0f}%")
    _v3_date_axis(axes[1], len(df))
    _v3_title(axes[0], f"图7 {payload['industry']}利润传导", "上图看利润池，下图看价格和成本后的留存")
    for ax in axes:
        _v3_style_axis(ax, grid=True)
        _v3_legend(ax, 2)
    return _v3_save(fig, path)


def _v3_plot_margin_roe_heatmap(self, payload: dict[str, Any], path: Path) -> Path | None:
    df = _v3_records(payload, "financial", "financial_series")
    if df.empty:
        return None
    df = df.tail(10).copy()
    labels = ["毛利率", "净利率", "ROE", "ROA"]
    cols = ["median_gross_margin", "median_net_margin", "median_roe", "median_roa"]
    mat = df[cols].apply(pd.to_numeric, errors="coerce").T
    fig, ax = plt.subplots(figsize=(10.8, 3.8))
    im = ax.imshow(mat, aspect="auto", cmap="RdYlGn", vmin=np.nanpercentile(mat.values, 10), vmax=np.nanpercentile(mat.values, 90))
    ax.set_yticks(range(len(labels)), labels=labels, fontsize=9)
    ax.set_xticks(range(len(df)), [str(x)[2:6] for x in df["end_date"]], fontsize=8.5)
    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            val = mat.iloc[i, j]
            if pd.notna(val):
                ax.text(j, i, f"{val:.1f}", ha="center", va="center", fontsize=7.8, color="#000000")
    fig.colorbar(im, ax=ax, fraction=0.025, pad=0.02)
    _v3_title(ax, f"图8 {payload['industry']}盈利质量热力图", "颜色越红代表读数越弱，越绿代表读数越强")
    _v3_style_axis(ax, grid=False)
    return _v3_save(fig, path)


def _v3_plot_dupont(self, payload: dict[str, Any], path: Path) -> Path | None:
    df = _v3_records(payload, "financial", "financial_series")
    if df.empty:
        return None
    df["date"] = _v3_date_col(df, "end_date")
    fig, ax = plt.subplots(figsize=(10.8, 4.5))
    series = [
        ("净利率", "median_net_margin", REPORT_RED),
        ("资产周转", "median_assets_turn", "#2F75B5"),
        ("权益乘数", "median_equity_multiplier", "#ED7D31"),
        ("ROE", "median_roe", "#7F7F7F"),
    ]
    for label, col, color in series:
        if col in df:
            ax.plot(df["date"], _v3_base100(df[col]), color=color, lw=1.45, label=label)
    ax.axhline(100, color="#000000", lw=0.8)
    ax.set_ylabel("期初为100")
    _v3_date_axis(ax, len(df))
    _v3_legend(ax, 4)
    _v3_title(ax, f"图9 {payload['industry']}杜邦拆解", "净利率，周转和杠杆共同解释ROE变化")
    _v3_style_axis(ax, grid=True)
    return _v3_save(fig, path)


def _v3_plot_valuation_band(self, payload: dict[str, Any], path: Path) -> Path | None:
    df = _v3_records(payload, "market", "valuation_daily")
    if df.empty:
        return None
    df["date"] = _v3_date_col(df, "trade_date")
    pe = pd.to_numeric(df["median_pe"], errors="coerce").replace([np.inf, -np.inf], np.nan)
    pb = pd.to_numeric(df["median_pb"], errors="coerce").replace([np.inf, -np.inf], np.nan)
    fig, axes = plt.subplots(2, 1, figsize=(10.8, 5.4), sharex=True)
    for ax, s, label, color in [(axes[0], pe, "PE中位数", REPORT_RED), (axes[1], pb, "PB中位数", "#2F75B5")]:
        q25, q50, q75 = s.quantile([0.25, 0.5, 0.75])
        ax.plot(df["date"], s, color=color, lw=1.45, label=label)
        ax.axhspan(q25, q75, color="#D9E2F3", alpha=0.45)
        ax.axhline(q50, color="#7F7F7F", lw=0.9, ls="--", label="历史中位")
        _v3_style_axis(ax, grid=True)
        _v3_legend(ax, 2)
    _v3_date_axis(axes[1], len(df))
    _v3_title(axes[0], f"图10 {payload['industry']}估值分位", "蓝色区间为历史25%至75%分位，末端位置决定赔率")
    return _v3_save(fig, path)


def _v3_plot_moneyflow_crowding(self, payload: dict[str, Any], path: Path) -> Path | None:
    mf = _v3_records(payload, "market", "moneyflow_daily")
    val = _v3_records(payload, "market", "valuation_daily")
    if mf.empty:
        return None
    mf["date"] = _v3_date_col(mf, "trade_date")
    mf["二十日净流入"] = pd.to_numeric(mf["net_mf_20d"], errors="coerce") / 1e8
    fig, axes = plt.subplots(2, 1, figsize=(10.8, 5.4), sharex=True, height_ratios=[1.1, 1])
    colors = np.where(mf["二十日净流入"] >= 0, REPORT_RED, "#7F7F7F")
    axes[0].bar(mf["date"], mf["二十日净流入"], color=colors, width=5)
    axes[0].axhline(0, color="#000000", lw=0.8)
    axes[0].set_ylabel("亿元")
    if not val.empty:
        val["date"] = _v3_date_col(val, "trade_date")
        axes[1].plot(val["date"], _v3_zscore(val["median_turnover"]).rolling(20, min_periods=5).mean(), color="#2F75B5", lw=1.4, label="换手强弱")
        axes[1].axhline(0, color="#000000", lw=0.8)
        axes[1].set_ylabel("标准化")
        _v3_legend(axes[1], 1)
    _v3_date_axis(axes[1], len(mf))
    _v3_title(axes[0], f"图11 {payload['industry']}资金边际与交易拥挤", "资金流看边际，换手强弱看拥挤")
    for ax in axes:
        _v3_style_axis(ax, grid=True)
    return _v3_save(fig, path)


def _v3_plot_financial_dispersion(self, payload: dict[str, Any], path: Path) -> Path | None:
    df = _v3_records(payload, "financial", "latest_company")
    if df.empty:
        fin = payload.get("financial", {})
        metrics = [
            ("收入同比", _v3_scalar(fin.get("median_tr_yoy"))),
            ("利润同比", _v3_scalar(fin.get("median_netprofit_yoy"))),
            ("毛利率", _v3_scalar(fin.get("median_gross_margin"))),
            ("净利率", _v3_scalar(fin.get("median_net_margin"))),
            ("ROE", _v3_scalar(fin.get("median_roe"))),
        ]
        fig, ax = plt.subplots(figsize=(10.5, 4.5))
        labels = [x[0] for x in metrics]
        values = [x[1] for x in metrics]
        colors = [REPORT_RED if v >= 0 else "#7F7F7F" for v in values]
        ax.barh(labels, values, color=colors, height=0.56)
        ax.axvline(0, color="#000000", lw=0.8)
        for i, v in enumerate(values):
            ax.text(v + (1.0 if v >= 0 else -1.0), i, f"{v:.1f}%", va="center", ha="left" if v >= 0 else "right", fontsize=9.2)
        ax.set_xlabel("百分比")
        _v3_title(ax, f"图12 {payload['industry']}最新财务读数", "公司分布不足时，使用行业中位读数确认财务中枢")
        _v3_style_axis(ax, grid=True)
        return _v3_save(fig, path)
    metrics = [
        ("收入同比", "tr_yoy"),
        ("利润同比", "netprofit_yoy"),
        ("毛利率", "gross_margin_pct"),
        ("净利率", "netprofit_margin"),
        ("ROE", "roe"),
    ]
    data, labels = [], []
    for label, col in metrics:
        s = pd.to_numeric(df.get(col), errors="coerce").dropna()
        s = s[(s > -200) & (s < 300)]
        if not s.empty:
            data.append(s.values)
            labels.append(label)
    if not data:
        return None
    fig, ax = plt.subplots(figsize=(10.5, 4.5))
    box = ax.boxplot(data, patch_artist=True, showfliers=False)
    ax.set_xticks(range(1, len(labels) + 1), labels)
    for i, patch in enumerate(box["boxes"]):
        patch.set_facecolor("#F7D9D3" if i < 2 else "#D9E2F3")
        patch.set_edgecolor("#7F7F7F")
    for med in box["medians"]:
        med.set_color(REPORT_RED)
        med.set_linewidth(1.4)
    ax.axhline(0, color="#000000", lw=0.8)
    ax.set_ylabel("百分比")
    ax.yaxis.set_major_formatter(lambda v, pos: f"{v:.0f}%")
    _v3_title(ax, f"图12 {payload['industry']}成分公司财务分化", "箱体越宽代表公司间分化越大，中位线代表行业真实中枢")
    _v3_style_axis(ax, grid=True)
    return _v3_save(fig, path)


def _v3_plot_signal_weight(self, payload: dict[str, Any], path: Path) -> Path | None:
    df = _v3_records(payload, "signal", "series")
    if df.empty:
        return None
    df["date"] = _v3_date_col(df, "rebalance_date")
    fig, ax = plt.subplots(figsize=(10.8, 4.4))
    score = pd.to_numeric(df["score"], errors="coerce")
    ax.plot(df["date"], score, color=REPORT_RED, lw=1.6, label="行业配置信号")
    if "target_weight" in df:
        tw = pd.to_numeric(df["target_weight"], errors="coerce") * 100
        ax.fill_between(df["date"], tw, color="#F0B183", alpha=0.35, label="目标权重")
    ax.axhline(0, color="#000000", lw=0.8)
    _v3_last_annotate(ax, df["date"].iloc[-1], score.iloc[-1], _v3_num(score.iloc[-1], 2))
    _v3_date_axis(ax, len(df))
    _v3_legend(ax, 2)
    _v3_title(ax, f"图13 {payload['industry']}配置信号", "信号用于观察景气，估值和资金合成后的配置方向")
    _v3_style_axis(ax, grid=True)
    return _v3_save(fig, path)


def _v3_plot_risk_tracking(self, payload: dict[str, Any], path: Path) -> Path:
    m, f, sig = payload["market"], payload["financial"], payload.get("signal", {})
    rows = [
        ("需求", _v3_clip_score(_v3_scalar(f.get("median_tr_yoy")), 25), _v3_pct(f.get("median_tr_yoy"))),
        ("利润", _v3_clip_score(_v3_scalar(f.get("median_netprofit_yoy")), 40), _v3_pct(f.get("median_netprofit_yoy"))),
        ("ROE", _v3_clip_score(_v3_scalar(f.get("median_roe")) - 6, 8), _v3_pct(f.get("median_roe"))),
        ("估值", 1 - 2 * _v3_scalar(m.get("median_pe_pctile"), 0.5), _v3_pct(m.get("median_pe_pctile"), ratio=True)),
        ("资金", _v3_clip_score(_v3_scalar(m.get("net_mf_20d_yi")), 15), f"{_v3_num(m.get('net_mf_20d_yi'), 1)}亿元"),
        ("信号", _v3_clip_score(_v3_scalar(sig.get("latest_score")), 1), _v3_num(sig.get("latest_score"), 2)),
    ]
    vals = np.array([[r[1] for r in rows]])
    fig, ax = plt.subplots(figsize=(10.8, 2.8))
    im = ax.imshow(vals, cmap="RdYlGn", vmin=-1, vmax=1, aspect="auto")
    ax.set_yticks([])
    ax.set_xticks(range(len(rows)), [r[0] for r in rows], fontsize=9.5)
    for j, r in enumerate(rows):
        ax.text(j, 0, r[2], ha="center", va="center", fontsize=9.2, color="#000000")
    fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02)
    _v3_title(ax, f"图14 {payload['industry']}跟踪信号", "红色区域代表压力，绿色区域代表支撑")
    _v3_style_axis(ax, grid=False)
    return _v3_save(fig, path)


def _v3_make_figures(self, payload: dict[str, Any], fig_dir: Path) -> dict[str, str]:
    plotters = [
        ("conclusion", self.plot_v3_conclusion, "01_conclusion.png"),
        ("relative_return", self.plot_v3_relative_return, "02_relative_return.png"),
        ("drawdown", self.plot_v3_drawdown, "03_drawdown.png"),
        ("demand_financial", self.plot_v3_demand_financial, "04_demand_financial.png"),
        ("macro_cycle", self.plot_v3_macro_cycle, "05_macro_cycle.png"),
        ("supply_capital", self.plot_v3_supply_capital, "06_supply_capital.png"),
        ("profit_pool", self.plot_v3_profit_pool, "07_profit_pool.png"),
        ("margin_roe", self.plot_v3_margin_roe_heatmap, "08_margin_roe.png"),
        ("dupont", self.plot_v3_dupont, "09_dupont.png"),
        ("valuation_band", self.plot_v3_valuation_band, "10_valuation_band.png"),
        ("moneyflow_crowding", self.plot_v3_moneyflow_crowding, "11_moneyflow_crowding.png"),
        ("financial_dispersion", self.plot_v3_financial_dispersion, "12_financial_dispersion.png"),
        ("signal_weight", self.plot_v3_signal_weight, "13_signal_weight.png"),
        ("risk_tracking", self.plot_v3_risk_tracking, "14_risk_tracking.png"),
    ]
    figs: dict[str, str] = {}
    for key, func, filename in plotters:
        try:
            p = func(payload, fig_dir / filename)
            if p:
                figs[key] = str(p)
        except Exception as exc:
            (fig_dir / f"{key}.error.txt").write_text(str(exc), encoding="utf-8")
    return figs


def _v3_make_sections(self, payload: dict[str, Any]) -> dict[str, str]:
    industry = payload["industry"]
    m, f, sig = payload["market"], payload["financial"], payload.get("signal", {})
    excess = _v3_scalar(m.get("cum_return")) - _v3_scalar(m.get("benchmark_cum_return"))
    profit_state = "利润端仍在承压" if _v3_scalar(f.get("median_netprofit_yoy")) < 0 else "利润端已经修复"
    valuation_state = "估值仍处中低分位" if _v3_scalar(m.get("median_pe_pctile"), 0.5) < 0.55 else "估值已进入偏高分位"
    flow_state = "资金边际为净流入" if _v3_scalar(m.get("net_mf_20d_yi")) > 0 else "资金边际仍为净流出"
    bullets = (
        f"1）{industry}当前的核心矛盾是{profit_state}，收入同比中位数为{_v3_pct(f.get('median_tr_yoy'))}，净利润同比中位数为{_v3_pct(f.get('median_netprofit_yoy'))}。\n"
        f"2）市场定价尚未形成强相对收益，区间超额收益为{_v3_pct(excess, ratio=True)}，{valuation_state}。\n"
        f"3）中期判断以需求修复，利润率修复和资金确认三条线交叉验证，当前{flow_state}。"
    )
    drafts = {
        "investment_conclusion": bullets,
        "conclusion": f"{industry}的中期判断落在盈利修复和估值赔率的交叉点。当前利润读数弱于收入读数，行业仍处在基本面验证阶段。",
        "relative_return": f"行业区间收益为{_v3_pct(m.get('cum_return'), ratio=True)}，全市场等权收益为{_v3_pct(m.get('benchmark_cum_return'), ratio=True)}。相对收益为{_v3_pct(excess, ratio=True)}，市场定价强度仍需由盈利修复继续确认。",
        "drawdown": f"回撤曲线刻画持有体验，滚动超额收益刻画边际定价。当前相对收益尚未形成稳定优势，行业仍处于等待基本面兑现的阶段。",
        "demand_financial": f"收入同比中位数为{_v3_pct(f.get('median_tr_yoy'))}，利润同比中位数为{_v3_pct(f.get('median_netprofit_yoy'))}。需求已经体现在收入端，利润端同步性偏弱。",
        "macro_cycle": f"制造业景气，价格环境和流动性统一为标准化强弱后，外部环境对行业需求形成约束。PPI读数为{_v3_num(payload.get('macro', {}).get('ppi_yoy'), 1)}，价格弹性仍是利润修复的关键。",
        "supply_capital": f"资产周转率中位数为{_v3_num(f.get('median_assets_turn'), 2)}，资产负债率中位数为{_v3_pct(f.get('median_debt_to_assets'))}。ROE尚未明显扩张，供给端没有进入激进扩张状态。",
        "profit_pool": f"行业收入合计为{_v3_num(f.get('revenue_sum_yi'), 1)}亿元，归母净利润合计为{_v3_num(f.get('profit_sum_yi'), 1)}亿元。利润池弱于收入规模，价格和成本传导仍是主线。",
        "margin_roe": f"毛利率中位数为{_v3_pct(f.get('median_gross_margin'))}，净利率中位数为{_v3_pct(f.get('median_net_margin'))}，ROE中位数为{_v3_pct(f.get('median_roe'))}。盈利质量尚未给出强修复信号。",
        "dupont": f"杜邦拆解显示ROE由净利率，资产周转和权益乘数共同决定。当前资产周转率为{_v3_num(f.get('median_assets_turn'), 2)}，ROE修复的主线仍在利润率。",
        "valuation_band": f"PE中位数为{_v3_num(m.get('median_pe'), 1)}倍，PE分位为{_v3_pct(m.get('median_pe_pctile'), ratio=True)}。当前估值没有完全脱离历史区间，赔率取决于利润端能否继续改善。",
        "moneyflow_crowding": f"二十日资金净流入为{_v3_num(m.get('net_mf_20d_yi'), 1)}亿元，换手率中位数为{_v3_num(m.get('median_turnover'), 2)}。资金边际未显示过度拥挤，交易层面对结论形成辅助验证。",
        "financial_dispersion": f"成分公司之间的收入，利润率和ROE分布决定行业结论的可靠性。当前行业中位数表现偏弱，后续修复需要从少数公司扩散到更多样本。",
        "signal_weight": f"配置信号最新得分为{_v3_num(sig.get('latest_score'), 2)}，目标权重为{_v3_pct(sig.get('latest_target_weight'), ratio=True)}。信号用于确认景气，估值和资金合成后的方向。",
        "risk_tracking": f"跟踪信号集中在需求，利润，ROE，估值，资金和配置信号六条线。当前最弱环节仍是利润同比，行业结论需要利润率回升来完成确认。",
        "business_chain": f"{industry}的行业研究从利润留存环节开始。上游约束，中游效率和下游需求共同决定利润分配，报告正文只保留能够解释中期机会的变量。",
        "final_view": f"{industry}当前处在盈利验证阶段。后续结论的上修来自利润同比收敛，ROE回升和资金边际转强的共同确认。"
    }
    evidence = {
        "industry": industry,
        "as_of": pretty_date(payload.get("as_of")),
        "cum_return": _v3_pct(m.get("cum_return"), ratio=True),
        "benchmark_return": _v3_pct(m.get("benchmark_cum_return"), ratio=True),
        "excess_return": _v3_pct(excess, ratio=True),
        "pe": _v3_num(m.get("median_pe"), 1),
        "pe_pctile": _v3_pct(m.get("median_pe_pctile"), ratio=True),
        "tr_yoy": _v3_pct(f.get("median_tr_yoy")),
        "netprofit_yoy": _v3_pct(f.get("median_netprofit_yoy")),
        "roe": _v3_pct(f.get("median_roe")),
        "netflow": _v3_num(m.get("net_mf_20d_yi"), 1),
        "score": _v3_num(sig.get("latest_score"), 2),
    }
    sections: dict[str, str] = {}
    for key, draft in drafts.items():
        if key == "investment_conclusion":
            sections[key] = _v3_clean_text(draft).replace("。2）", "。\n2）").replace("。3）", "。\n3）")
        else:
            sections[key] = self.ai.rewrite(key, evidence, draft)
    return sections


SafeAIWriter.rewrite = _v3_rewrite
IndustryDepthReportAgent.summarize_financial = _v3_summarize_financial
IndustryDepthReportAgent.make_figures = _v3_make_figures
IndustryDepthReportAgent.make_sections = _v3_make_sections
IndustryDepthReportAgent.plot_v3_conclusion = _v3_plot_conclusion
IndustryDepthReportAgent.plot_v3_relative_return = _v3_plot_relative_return
IndustryDepthReportAgent.plot_v3_drawdown = _v3_plot_drawdown
IndustryDepthReportAgent.plot_v3_demand_financial = _v3_plot_demand_financial
IndustryDepthReportAgent.plot_v3_macro_cycle = _v3_plot_macro_cycle
IndustryDepthReportAgent.plot_v3_supply_capital = _v3_plot_supply_capital
IndustryDepthReportAgent.plot_v3_profit_pool = _v3_plot_profit_pool
IndustryDepthReportAgent.plot_v3_margin_roe_heatmap = _v3_plot_margin_roe_heatmap
IndustryDepthReportAgent.plot_v3_dupont = _v3_plot_dupont
IndustryDepthReportAgent.plot_v3_valuation_band = _v3_plot_valuation_band
IndustryDepthReportAgent.plot_v3_moneyflow_crowding = _v3_plot_moneyflow_crowding
IndustryDepthReportAgent.plot_v3_financial_dispersion = _v3_plot_financial_dispersion
IndustryDepthReportAgent.plot_v3_signal_weight = _v3_plot_signal_weight
IndustryDepthReportAgent.plot_v3_risk_tracking = _v3_plot_risk_tracking

# === INDUSTRY REPORT V3 OVERRIDES END ===


# === INDUSTRY REPORT V4 OVERRIDES START ===

def _v4_clean_text(text: str, keep_newline: bool = False) -> str:
    banned = {
        "数据来源": "",
        "数据源": "",
        "数据支撑": "",
        "本地数据库": "",
        "research_warehouse.db": "",
        "质量门": "",
        "API": "",
        "AI": "",
        "提示词": "",
        "图表结论：": "",
        "图表结论": "",
        "行业定义": "",
        "若": "当",
        "如果": "当",
        "假设": "情形",
        "可能": "",
        "或许": "",
        "不确定": "",
        "不是而是": "",
        "；": "。",
        "、": "，",
        "“": "",
        "”": "",
        "\"": "",
        "?": "",
        "？": "。",
    }
    value = str(text or "")
    for old, new in banned.items():
        value = value.replace(old, new)
    if keep_newline:
        value = re.sub(r"[ \t]+", "", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        parts = []
        for line in value.splitlines():
            line = re.sub(r"。{2,}", "。", line).strip("。")
            if line:
                parts.append(line + "。")
        return "\n".join(parts)
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"。{2,}", "。", value)
    return value.strip("。") + ("。" if value.strip("。") else "")


def _v4_bad_text(text: str) -> bool:
    bad = [
        "数据来源", "数据源", "数据支撑", "本地数据库", "质量门", "API", "AI", "提示词",
        "行业定义", "若", "假设", "可能", "或许", "不确定", "不是而是", "、", "“", "”", "?", "？",
    ]
    return any(x in str(text or "") for x in bad)


def _v4_trend_word(value: Any, high: float = 0.0) -> str:
    x = _v3_scalar(value)
    return "改善" if x > high else "承压"


def _v4_change_word(value: float, pos: str = "上行", neg: str = "回落") -> str:
    if value > 0.2:
        return pos
    if value < -0.2:
        return neg
    return "震荡"


def _v4_market_stats(payload: dict[str, Any]) -> dict[str, Any]:
    m = payload.get("market", {})
    f = payload.get("financial", {})
    ind = _v3_records(payload, "market", "industry_daily")
    bench = _v3_records(payload, "market", "benchmark_daily")
    stats: dict[str, Any] = {}
    if not ind.empty:
        nav = 1 + pd.to_numeric(ind.get("cum_return"), errors="coerce")
        draw = nav / nav.cummax() - 1
        stats["drawdown"] = float(draw.iloc[-1]) if len(draw) else None
    if not ind.empty and not bench.empty:
        merged = pd.merge(ind[["trade_date", "ret", "cum_return"]], bench[["trade_date", "ret", "cum_return"]], on="trade_date", how="inner", suffixes=("_ind", "_bench"))
        if not merged.empty:
            rel_nav = (1 + pd.to_numeric(merged["cum_return_ind"], errors="coerce")) / (1 + pd.to_numeric(merged["cum_return_bench"], errors="coerce")) * 100
            stats["relative_nav"] = float(rel_nav.iloc[-1])
            rex = (pd.to_numeric(merged["ret_ind"], errors="coerce") - pd.to_numeric(merged["ret_bench"], errors="coerce")).rolling(60, min_periods=20).sum()
            stats["rolling_excess_60d"] = float(rex.iloc[-1]) if len(rex.dropna()) else None
    stats["excess_return"] = _v3_scalar(m.get("cum_return")) - _v3_scalar(m.get("benchmark_cum_return"))
    stats["profit_gap"] = _v3_scalar(f.get("median_tr_yoy")) - _v3_scalar(f.get("median_netprofit_yoy"))
    stats["margin_gap"] = _v3_scalar(f.get("median_gross_margin")) - _v3_scalar(f.get("median_net_margin"))
    stats["valuation_state"] = "中低分位" if _v3_scalar(m.get("median_pe_pctile"), 0.5) < 0.55 else "偏高分位"
    stats["flow_state"] = "净流入" if _v3_scalar(m.get("net_mf_20d_yi")) > 0 else "净流出"
    return stats


def _v4_rewrite(self, section_name: str, evidence: dict[str, Any], fallback: str) -> str:
    fallback = _v4_clean_text(fallback)
    if not self.available():
        return fallback
    system = (
        "你是中国卖方行业研究员。只根据用户给出的结构化证据写正式行业深度报告正文。"
        "每段必须按结论句，证据句，机制推导句展开。"
        "证据句只能使用证据中的数字，禁止新增事实和新增数字。"
        "禁止写数据来源，数据库，质量门，API，AI，提示词，行业定义。"
        "禁止使用若，假设，可能，或许，不确定，不是而是，引号和顿号。"
        "禁止口语化，禁止空泛形容，禁止把指标逐项报数。"
        "没有产业高频证据时，只能写已验证的行情，财务，估值和资金结论，不能补写产品价格，库存，开工率等外部事实。"
    )
    user = {
        "section": section_name,
        "style": "120到220字，最多三句。一张图对应一段分析，先给判断，再解释图中拐点或背离，最后落到投资含义。",
        "evidence": evidence,
        "draft": fallback,
    }
    try:
        resp = requests.post(
            f"{self.base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
                ],
                "reasoning_effort": self.reasoning_effort,
                "temperature": 0.02,
            },
            timeout=120,
        )
        resp.raise_for_status()
        text = _v4_clean_text(resp.json()["choices"][0]["message"]["content"].strip())
        if not text or len(text) < 80 or len(text) > 260 or _v4_bad_text(text):
            return fallback
        return text
    except Exception:
        return fallback


def _v4_title(ax, title: str, subtitle: str = "") -> None:
    ax.set_title(title, loc="left", fontsize=13.5, fontweight="bold", color="#000000", pad=10)
    if subtitle:
        ax.text(0, 1.018, subtitle, transform=ax.transAxes, ha="left", va="bottom", fontsize=8.7, color="#555555")


def _v4_legend(ax, ncol: int = 2, y: float = -0.24) -> None:
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(loc="lower center", bbox_to_anchor=(0.5, y), ncol=ncol, frameon=False, fontsize=8.4)


def _v4_annotate_last(ax, dates, values, fmt: str = "{:.1f}", suffix: str = "", color: str = REPORT_RED) -> None:
    try:
        s = pd.Series(values).replace([np.inf, -np.inf], np.nan)
        idx = s.last_valid_index()
        if idx is None:
            return
        x = pd.Series(dates).iloc[idx]
        y = float(s.iloc[idx])
        ax.scatter([x], [y], s=22, color=color, zorder=5)
        ax.annotate(fmt.format(y) + suffix, xy=(x, y), xytext=(7, 0), textcoords="offset points", va="center", fontsize=8.2, color=color)
    except Exception:
        return


def _v4_plot_thesis_map(self, payload: dict[str, Any], path: Path) -> Path:
    industry = payload["industry"]
    fw = payload.get("framework", {})
    fig, ax = plt.subplots(figsize=(11.2, 4.2))
    ax.axis("off")
    steps = [
        ("周期位置", "收益与回撤"),
        ("需求边际", fw.get("demand", "终端需求")),
        ("供给约束", fw.get("supply", "产能与库存")),
        ("价格成本", fw.get("profit", "价格减成本")),
        ("利润验证", "收入，利润率，ROE"),
        ("估值资金", "估值分位与资金"),
        ("结论反证", "跟踪指标修正"),
    ]
    xs = np.linspace(0.035, 0.86, len(steps))
    w = 0.112
    y = 0.50
    for i, (name, desc) in enumerate(steps):
        color = REPORT_RED if i in {0, 3, 6} else "#7F7F7F"
        box = FancyBboxPatch((xs[i], y - 0.18), w, 0.34, boxstyle="round,pad=0.01,rounding_size=0.012", facecolor="white", edgecolor=color, linewidth=1.25)
        ax.add_patch(box)
        ax.text(xs[i] + w / 2, y + 0.075, name, ha="center", va="center", fontsize=10.0, fontweight="bold")
        ax.text(xs[i] + w / 2, y - 0.075, "\n".join(textwrap.wrap(desc, width=8)), ha="center", va="center", fontsize=8.0, linespacing=1.22)
        if i < len(steps) - 1:
            ax.annotate("", xy=(xs[i + 1] - 0.01, y), xytext=(xs[i] + w + 0.008, y), arrowprops=dict(arrowstyle="->", lw=1.05, color="#666666"))
    ax.text(0.01, 0.94, f"图1 {industry}中期研究链条", fontsize=13.5, fontweight="bold", ha="left")
    ax.text(0.01, 0.86, "正文只保留能解释中期投资机会的证据，内部模型和数据口径不进入主文。", fontsize=8.8, color="#555555", ha="left")
    return _v3_save(fig, path)


def _v4_plot_relative_return(self, payload: dict[str, Any], path: Path) -> Path | None:
    ind = _v3_records(payload, "market", "industry_daily")
    bench = _v3_records(payload, "market", "benchmark_daily")
    if ind.empty or bench.empty:
        return None
    ind["date"] = _v3_date_col(ind, "trade_date")
    bench["date"] = _v3_date_col(bench, "trade_date")
    merged = pd.merge(ind[["trade_date", "date", "ret", "cum_return"]], bench[["trade_date", "ret", "cum_return"]], on="trade_date", how="inner", suffixes=("_ind", "_bench"))
    if merged.empty:
        return None
    rel_nav = (1 + pd.to_numeric(merged["cum_return_ind"], errors="coerce")) / (1 + pd.to_numeric(merged["cum_return_bench"], errors="coerce")) * 100
    roll_excess = (pd.to_numeric(merged["ret_ind"], errors="coerce") - pd.to_numeric(merged["ret_bench"], errors="coerce")).rolling(60, min_periods=20).sum() * 100
    fig, axes = plt.subplots(2, 1, figsize=(11.0, 5.6), sharex=True, height_ratios=[1.12, 1])
    axes[0].plot(merged["date"], rel_nav, color=REPORT_RED, lw=1.7, label="相对净值")
    axes[0].axhline(100, color="#000000", lw=0.8)
    _v4_annotate_last(axes[0], merged["date"], rel_nav, "{:.1f}", "", REPORT_RED)
    axes[0].set_ylabel("全市场=100")
    axes[1].plot(merged["date"], roll_excess, color="#2F75B5", lw=1.45, label="60日滚动超额")
    axes[1].axhline(0, color="#000000", lw=0.8)
    axes[1].fill_between(merged["date"], roll_excess, 0, where=roll_excess >= 0, color="#F7D9D3", alpha=0.55)
    axes[1].fill_between(merged["date"], roll_excess, 0, where=roll_excess < 0, color="#D9D9D9", alpha=0.55)
    axes[1].set_ylabel("百分点")
    _v3_date_axis(axes[1], len(merged))
    _v4_title(axes[0], f"图2 {payload['industry']}相对净值与边际超额", "相对净值看定价方向，滚动超额看边际变化")
    for ax in axes:
        _v3_style_axis(ax, grid=True)
        _v4_legend(ax, 1)
    return _v3_save(fig, path)


def _v4_plot_drawdown(self, payload: dict[str, Any], path: Path) -> Path | None:
    ind = _v3_records(payload, "market", "industry_daily")
    if ind.empty:
        return None
    ind["date"] = _v3_date_col(ind, "trade_date")
    ret = pd.to_numeric(ind["ret"], errors="coerce")
    nav = (1 + pd.to_numeric(ind["cum_return"], errors="coerce")).replace(0, np.nan)
    draw = (nav / nav.cummax() - 1) * 100
    vol = ret.rolling(60, min_periods=20).std() * np.sqrt(252) * 100
    fig, axes = plt.subplots(2, 1, figsize=(11.0, 5.4), sharex=True, height_ratios=[1.05, 1])
    axes[0].fill_between(ind["date"], draw, 0, color="#A5A5A5", alpha=0.52)
    axes[0].axhline(0, color="#000000", lw=0.8)
    axes[0].set_ylabel("回撤")
    axes[1].plot(ind["date"], vol, color="#ED7D31", lw=1.45, label="60日年化波动")
    axes[1].set_ylabel("波动率")
    _v4_annotate_last(axes[1], ind["date"], vol, "{:.1f}", "%", "#ED7D31")
    _v3_date_axis(axes[1], len(ind))
    _v4_title(axes[0], f"图3 {payload['industry']}回撤与波动约束", "收益弹性需要和持有期回撤一起判断")
    for ax in axes:
        ax.yaxis.set_major_formatter(lambda v, pos: f"{v:.0f}%")
        _v3_style_axis(ax, grid=True)
        _v4_legend(ax, 1)
    return _v3_save(fig, path)


def _v4_plot_demand_financial(self, payload: dict[str, Any], path: Path) -> Path | None:
    df = _v3_records(payload, "financial", "financial_series")
    if df.empty:
        return None
    df["date"] = _v3_date_col(df, "end_date")
    tr = pd.to_numeric(df["median_tr_yoy"], errors="coerce")
    npy = pd.to_numeric(df["median_netprofit_yoy"], errors="coerce")
    gap = tr - npy
    fig, axes = plt.subplots(2, 1, figsize=(11.0, 5.5), sharex=True, height_ratios=[1.15, 0.9])
    axes[0].plot(df["date"], tr, color=REPORT_RED, lw=1.6, marker="o", ms=3.0, label="收入同比")
    axes[0].plot(df["date"], npy, color="#2F75B5", lw=1.45, marker="o", ms=2.8, label="利润同比")
    axes[0].axhline(0, color="#000000", lw=0.8)
    axes[0].set_ylabel("同比")
    axes[1].bar(df["date"], gap, color=np.where(gap >= 0, "#D9D9D9", "#F7D9D3"), width=42, label="收入利润差")
    axes[1].axhline(0, color="#000000", lw=0.8)
    axes[1].set_ylabel("百分点")
    _v4_annotate_last(axes[0], df["date"], tr, "{:.1f}", "%", REPORT_RED)
    _v4_annotate_last(axes[0], df["date"], npy, "{:.1f}", "%", "#2F75B5")
    _v3_date_axis(axes[1], len(df))
    _v4_title(axes[0], f"图4 {payload['industry']}需求进入报表的力度", "收入看需求传导，利润看价格成本和经营杠杆后的兑现")
    for ax in axes:
        ax.yaxis.set_major_formatter(lambda v, pos: f"{v:.0f}%")
        _v3_style_axis(ax, grid=True)
        _v4_legend(ax, 2)
    return _v3_save(fig, path)


def _v4_plot_macro_cycle(self, payload: dict[str, Any], path: Path) -> Path | None:
    df = _v3_records(payload, "macro", "series")
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["month"].astype(str) + "01", format="%Y%m%d", errors="coerce")
    fig, axes = plt.subplots(3, 1, figsize=(11.0, 6.2), sharex=True)
    specs = [
        ("pmi_manufacturing", "制造业PMI", REPORT_RED, 50.0, ""),
        ("ppi_yoy", "PPI同比", "#ED7D31", 0.0, "%"),
        ("m2_yoy", "M2同比", "#2F75B5", None, "%"),
    ]
    for ax, (col, label, color, base, suffix) in zip(axes, specs):
        if col not in df:
            continue
        s = pd.to_numeric(df[col], errors="coerce")
        ax.plot(df["date"], s, color=color, lw=1.45, label=label)
        if base is not None:
            ax.axhline(base, color="#000000", lw=0.8, ls="--")
        _v4_annotate_last(ax, df["date"], s, "{:.1f}", suffix, color)
        ax.set_ylabel(label)
        _v3_style_axis(ax, grid=True)
        _v4_legend(ax, 1, -0.30)
    _v3_date_axis(axes[-1], len(df))
    _v4_title(axes[0], f"图5 {payload['industry']}外部环境坐标", "增长，价格和流动性分面展示，避免量纲压缩造成误读")
    return _v3_save(fig, path)


def _v4_plot_supply_capital(self, payload: dict[str, Any], path: Path) -> Path | None:
    df = _v3_records(payload, "financial", "financial_series")
    if df.empty:
        return None
    df["date"] = _v3_date_col(df, "end_date")
    roe = pd.to_numeric(df["median_roe"], errors="coerce")
    turn = pd.to_numeric(df["median_assets_turn"], errors="coerce")
    debt = pd.to_numeric(df["median_debt_to_assets"], errors="coerce")
    fig, axes = plt.subplots(2, 1, figsize=(11.0, 5.6), sharex=True, height_ratios=[1.12, 0.9])
    axes[0].plot(df["date"], roe, color=REPORT_RED, lw=1.55, marker="o", ms=2.8, label="ROE")
    ax2 = axes[0].twinx()
    ax2.plot(df["date"], turn, color="#2F75B5", lw=1.35, label="资产周转率")
    axes[0].axhline(0, color="#000000", lw=0.8)
    axes[0].set_ylabel("ROE")
    ax2.set_ylabel("周转率")
    axes[1].plot(df["date"], debt, color="#7F7F7F", lw=1.45, label="资产负债率")
    axes[1].set_ylabel("负债率")
    _v4_annotate_last(axes[0], df["date"], roe, "{:.1f}", "%", REPORT_RED)
    _v4_annotate_last(axes[1], df["date"], debt, "{:.1f}", "%", "#7F7F7F")
    _v3_date_axis(axes[1], len(df))
    _v4_title(axes[0], f"图6 {payload['industry']}资本周期与供给纪律", "ROE回升但周转和杠杆未扩张时，供给约束更有利于利润留存")
    for ax in [axes[0], axes[1], ax2]:
        _v3_style_axis(ax, grid=ax is not ax2)
    axes[0].legend(loc="lower left", bbox_to_anchor=(0.22, -0.31), frameon=False, fontsize=8.4)
    ax2.legend(loc="lower left", bbox_to_anchor=(0.47, -0.31), frameon=False, fontsize=8.4)
    _v4_legend(axes[1], 1)
    return _v3_save(fig, path)


def _v4_plot_profit_pool(self, payload: dict[str, Any], path: Path) -> Path | None:
    df = _v3_records(payload, "financial", "financial_series")
    if df.empty:
        return None
    df["date"] = _v3_date_col(df, "end_date")
    revenue = pd.to_numeric(df["revenue_sum"], errors="coerce") / 1e8
    profit = pd.to_numeric(df["profit_sum"], errors="coerce") / 1e8
    fig, axes = plt.subplots(2, 1, figsize=(11.0, 5.7), sharex=True, height_ratios=[1.05, 1])
    axes[0].plot(df["date"], revenue, color=REPORT_RED, lw=1.55, marker="o", ms=2.8, label="收入规模")
    axes[0].set_ylabel("亿元")
    axes[1].bar(df["date"], profit, color=np.where(profit >= 0, "#F7D9D3", "#A5A5A5"), width=42, label="利润池")
    axes[1].plot(df["date"], profit.rolling(4, min_periods=1).mean(), color="#2F75B5", lw=1.3, label="四期均值")
    axes[1].axhline(0, color="#000000", lw=0.8)
    axes[1].set_ylabel("亿元")
    _v4_annotate_last(axes[0], df["date"], revenue, "{:.1f}", "亿元", REPORT_RED)
    _v4_annotate_last(axes[1], df["date"], profit, "{:.1f}", "亿元", "#2F75B5")
    _v3_date_axis(axes[1], len(df))
    _v4_title(axes[0], f"图7 {payload['industry']}收入规模与利润池", "收入代表需求承接，利润池决定行业配置价值能否兑现")
    for ax in axes:
        _v3_style_axis(ax, grid=True)
        _v4_legend(ax, 2)
    return _v3_save(fig, path)


def _v4_plot_margin_roe(self, payload: dict[str, Any], path: Path) -> Path | None:
    df = _v3_records(payload, "financial", "financial_series")
    if df.empty:
        return None
    df["date"] = _v3_date_col(df, "end_date")
    fig, ax = plt.subplots(figsize=(11.0, 4.7))
    series = [
        ("毛利率", "median_gross_margin", "#ED7D31"),
        ("净利率", "median_net_margin", REPORT_RED),
        ("ROE", "median_roe", "#2F75B5"),
    ]
    for label, col, color in series:
        s = pd.to_numeric(df[col], errors="coerce")
        ax.plot(df["date"], s, lw=1.45, color=color, marker="o", ms=2.6, label=label)
        _v4_annotate_last(ax, df["date"], s, "{:.1f}", "%", color)
    ax.axhline(0, color="#000000", lw=0.8)
    ax.set_ylabel("百分比")
    ax.yaxis.set_major_formatter(lambda v, pos: f"{v:.0f}%")
    _v3_date_axis(ax, len(df))
    _v4_legend(ax, 3)
    _v4_title(ax, f"图8 {payload['industry']}利润率到ROE的传导", "毛利率领先观察价格成本，净利率和ROE验证经营杠杆")
    _v3_style_axis(ax, grid=True)
    return _v3_save(fig, path)


def _v4_plot_dupont(self, payload: dict[str, Any], path: Path) -> Path | None:
    df = _v3_records(payload, "financial", "financial_series")
    if df.empty:
        return None
    df["date"] = _v3_date_col(df, "end_date")
    fig, axes = plt.subplots(2, 2, figsize=(11.0, 6.0), sharex=True)
    specs = [
        ("median_net_margin", "净利率", REPORT_RED, "%"),
        ("median_assets_turn", "资产周转率", "#2F75B5", ""),
        ("median_equity_multiplier", "权益乘数", "#ED7D31", "倍"),
        ("median_roe", "ROE", "#7F7F7F", "%"),
    ]
    for ax, (col, label, color, suffix) in zip(axes.ravel(), specs):
        if col not in df:
            continue
        s = pd.to_numeric(df[col], errors="coerce")
        ax.plot(df["date"], s, color=color, lw=1.35, marker="o", ms=2.4, label=label)
        if suffix == "%":
            ax.yaxis.set_major_formatter(lambda v, pos: f"{v:.0f}%")
        _v4_annotate_last(ax, df["date"], s, "{:.1f}", suffix, color)
        ax.set_ylabel(label)
        _v3_style_axis(ax, grid=True)
    _v3_date_axis(axes.ravel()[-1], len(df))
    _v4_title(axes.ravel()[0], f"图9 {payload['industry']}杜邦拆解", "分面显示净利率，周转和杠杆，避免单图折线互相遮蔽")
    return _v3_save(fig, path)


def _v4_plot_valuation_band(self, payload: dict[str, Any], path: Path) -> Path | None:
    df = _v3_records(payload, "market", "valuation_daily")
    if df.empty:
        return None
    df["date"] = _v3_date_col(df, "trade_date")
    pe = pd.to_numeric(df["median_pe"], errors="coerce").replace([np.inf, -np.inf], np.nan)
    pb = pd.to_numeric(df["median_pb"], errors="coerce").replace([np.inf, -np.inf], np.nan)
    fig, axes = plt.subplots(2, 1, figsize=(11.0, 5.6), sharex=True)
    for ax, s, label, color, suffix in [(axes[0], pe, "PE中位数", REPORT_RED, "倍"), (axes[1], pb, "PB中位数", "#2F75B5", "倍")]:
        q20, q50, q80 = s.quantile([0.2, 0.5, 0.8])
        ax.axhspan(q20, q80, color="#D9E2F3", alpha=0.45)
        ax.plot(df["date"], s, color=color, lw=1.45, label=label)
        ax.axhline(q50, color="#7F7F7F", lw=0.9, ls="--", label="历史中位")
        _v4_annotate_last(ax, df["date"], s, "{:.1f}", suffix, color)
        _v3_style_axis(ax, grid=True)
        _v4_legend(ax, 2)
    _v3_date_axis(axes[1], len(df))
    _v4_title(axes[0], f"图10 {payload['industry']}估值位置", "蓝色区间为20%至80%分位，末端位置用于判断赔率")
    return _v3_save(fig, path)


def _v4_plot_moneyflow_crowding(self, payload: dict[str, Any], path: Path) -> Path | None:
    mf = _v3_records(payload, "market", "moneyflow_daily")
    val = _v3_records(payload, "market", "valuation_daily")
    if mf.empty:
        return None
    mf["date"] = _v3_date_col(mf, "trade_date")
    flow = pd.to_numeric(mf["net_mf_20d"], errors="coerce") / 1e8
    fig, axes = plt.subplots(2, 1, figsize=(11.0, 5.5), sharex=True, height_ratios=[1.1, 1])
    axes[0].bar(mf["date"], flow, color=np.where(flow >= 0, REPORT_RED, "#7F7F7F"), width=5, label="二十日资金净流入")
    axes[0].axhline(0, color="#000000", lw=0.8)
    axes[0].set_ylabel("亿元")
    if not val.empty:
        val["date"] = _v3_date_col(val, "trade_date")
        turnover = pd.to_numeric(val["median_turnover"], errors="coerce").rolling(20, min_periods=5).mean()
        axes[1].plot(val["date"], turnover, color="#2F75B5", lw=1.45, label="换手率二十日均值")
        axes[1].set_ylabel("换手率")
        _v4_annotate_last(axes[1], val["date"], turnover, "{:.2f}", "", "#2F75B5")
    _v4_annotate_last(axes[0], mf["date"], flow, "{:.1f}", "亿元", REPORT_RED)
    _v3_date_axis(axes[1], len(mf))
    _v4_title(axes[0], f"图11 {payload['industry']}资金边际与交易热度", "资金流看边际变化，换手率看交易拥挤")
    for ax in axes:
        _v3_style_axis(ax, grid=True)
        _v4_legend(ax, 1)
    return _v3_save(fig, path)


def _v4_plot_financial_dispersion(self, payload: dict[str, Any], path: Path) -> Path | None:
    df = _v3_records(payload, "financial", "latest_company")
    if df.empty:
        return _v3_plot_financial_dispersion(self, payload, path)
    metrics = [
        ("收入同比", "tr_yoy"),
        ("利润同比", "netprofit_yoy"),
        ("毛利率", "gross_margin_pct"),
        ("净利率", "netprofit_margin"),
        ("ROE", "roe"),
    ]
    data, labels = [], []
    for label, col in metrics:
        s = pd.to_numeric(df.get(col), errors="coerce").dropna()
        s = s[(s > -200) & (s < 300)]
        if not s.empty:
            data.append(s.values)
            labels.append(label)
    if not data:
        return None
    fig, ax = plt.subplots(figsize=(11.0, 4.9))
    box = ax.boxplot(data, patch_artist=True, showfliers=False, widths=0.55)
    ax.set_xticks(range(1, len(labels) + 1), labels)
    for i, patch in enumerate(box["boxes"]):
        patch.set_facecolor("#F7D9D3" if i in {0, 1} else "#D9E2F3")
        patch.set_edgecolor("#7F7F7F")
    for med in box["medians"]:
        med.set_color(REPORT_RED)
        med.set_linewidth(1.35)
    ax.axhline(0, color="#000000", lw=0.8)
    ax.set_ylabel("百分比")
    ax.yaxis.set_major_formatter(lambda v, pos: f"{v:.0f}%")
    _v4_title(ax, f"图12 {payload['industry']}成分公司财务分布", "箱体观察分化程度，中位线代表行业真实中枢")
    _v3_style_axis(ax, grid=True)
    return _v3_save(fig, path)


def _v4_plot_tracking_table(self, payload: dict[str, Any], path: Path) -> Path:
    industry = payload["industry"]
    fw = payload.get("framework", {})
    m, f = payload.get("market", {}), payload.get("financial", {})
    rows = [
        ("需求", fw.get("demand", "终端需求"), f"收入同比{_v3_pct(f.get('median_tr_yoy'))}"),
        ("供给", fw.get("supply", "产能与库存"), f"周转率{_v3_num(f.get('median_assets_turn'), 2)}"),
        ("利润", fw.get("profit", "价格成本关系"), f"净利润同比{_v3_pct(f.get('median_netprofit_yoy'))}"),
        ("估值", "PE，PB分位", f"PE分位{_v3_pct(m.get('median_pe_pctile'), ratio=True)}"),
        ("资金", "资金流与换手", f"20日净流入{_v3_num(m.get('net_mf_20d_yi'), 1)}亿元"),
    ]
    fig, ax = plt.subplots(figsize=(11.0, 4.6))
    ax.axis("off")
    ax.text(0.01, 0.95, f"图13 {industry}后续跟踪框架", fontsize=13.5, fontweight="bold", ha="left")
    ax.text(0.01, 0.88, "每次更新只看同一组变量，结论随需求，供给，利润，估值和资金共同修正。", fontsize=8.8, color="#555555", ha="left")
    y0, row_h = 0.74, 0.13
    widths = [0.12, 0.58, 0.26]
    headers = ["层次", "跟踪重点", "当前读数"]
    x0 = 0.02
    for j, htxt in enumerate(headers):
        ax.add_patch(FancyBboxPatch((x0 + sum(widths[:j]), y0 + row_h), widths[j], row_h, boxstyle="square,pad=0.0", facecolor="#F2F4F7", edgecolor="#BFBFBF", linewidth=0.8))
        ax.text(x0 + sum(widths[:j]) + widths[j] / 2, y0 + row_h * 1.5, htxt, ha="center", va="center", fontsize=9.5, fontweight="bold")
    for i, row in enumerate(rows):
        y = y0 - i * row_h
        for j, cell in enumerate(row):
            face = "#FFFFFF" if i % 2 == 0 else "#FAFAFA"
            ax.add_patch(FancyBboxPatch((x0 + sum(widths[:j]), y), widths[j], row_h, boxstyle="square,pad=0.0", facecolor=face, edgecolor="#BFBFBF", linewidth=0.75))
            ax.text(x0 + sum(widths[:j]) + 0.012, y + row_h / 2, "\n".join(textwrap.wrap(cell, width=28 if j == 1 else 14)), ha="left", va="center", fontsize=8.7, linespacing=1.15)
    return _v3_save(fig, path)


def _v4_make_figures(self, payload: dict[str, Any], fig_dir: Path) -> dict[str, str]:
    plotters = [
        ("thesis_map", self.plot_v4_thesis_map, "01_thesis_map.png"),
        ("relative_return", self.plot_v4_relative_return, "02_relative_return.png"),
        ("drawdown", self.plot_v4_drawdown, "03_drawdown.png"),
        ("demand_financial", self.plot_v4_demand_financial, "04_demand_financial.png"),
        ("macro_cycle", self.plot_v4_macro_cycle, "05_macro_cycle.png"),
        ("supply_capital", self.plot_v4_supply_capital, "06_supply_capital.png"),
        ("profit_pool", self.plot_v4_profit_pool, "07_profit_pool.png"),
        ("margin_roe", self.plot_v4_margin_roe, "08_margin_roe.png"),
        ("dupont", self.plot_v4_dupont, "09_dupont.png"),
        ("valuation_band", self.plot_v4_valuation_band, "10_valuation_band.png"),
        ("moneyflow_crowding", self.plot_v4_moneyflow_crowding, "11_moneyflow_crowding.png"),
        ("financial_dispersion", self.plot_v4_financial_dispersion, "12_financial_dispersion.png"),
        ("tracking_table", self.plot_v4_tracking_table, "13_tracking_table.png"),
    ]
    figs: dict[str, str] = {}
    for key, func, filename in plotters:
        try:
            p = func(payload, fig_dir / filename)
            if p:
                figs[key] = str(p)
        except Exception as exc:
            (fig_dir / f"{key}.error.txt").write_text(str(exc), encoding="utf-8")
    return figs


def _v4_make_sections(self, payload: dict[str, Any]) -> dict[str, str]:
    industry = payload["industry"]
    m, f, sig = payload["market"], payload["financial"], payload.get("signal", {})
    fw = payload.get("framework", {})
    stats = _v4_market_stats(payload)
    excess = stats["excess_return"]
    profit_gap = stats["profit_gap"]
    profit_state = "利润端仍在承压" if _v3_scalar(f.get("median_netprofit_yoy")) < 0 else "利润端已经修复"
    revenue_state = "收入端保持正增长" if _v3_scalar(f.get("median_tr_yoy")) > 0 else "收入端尚未恢复"
    valuation_state = stats["valuation_state"]
    flow_state = stats["flow_state"]
    rel_state = "落后全市场" if excess < 0 else "跑赢全市场"
    conclusion = (
        f"1）{industry}中期主线仍在利润验证，{revenue_state}，{profit_state}，收入同比中位数为{_v3_pct(f.get('median_tr_yoy'))}，净利润同比中位数为{_v3_pct(f.get('median_netprofit_yoy'))}。\n"
        f"2）市场定价尚未给出强确认，区间相对收益为{_v3_pct(excess, ratio=True)}，PE分位处在{valuation_state}，资金二十日状态为{flow_state}。\n"
        f"3）报告结论需要按需求边际，供给约束，价格成本，利润兑现，估值资金五条线递进验证，单一行情或单一财务读数不足以提高行业判断。"
    )
    drafts = {
        "investment_conclusion": conclusion,
        "thesis_map": f"{industry}研究主线从周期位置进入，先看市场是否提前定价，再用需求，供给和价格成本解释利润变化，最后用估值和资金判断赔率。该链条避免把财务滞后读数直接写成产业景气，也避免用内部信号替代行业逻辑。",
        "relative_return": f"{industry}相对净值目前{rel_state}，区间相对收益为{_v3_pct(excess, ratio=True)}。相对曲线没有持续上行时，市场尚未把中期景气交易成稳定超额；滚动超额转正并延续，才说明资金开始对基本面改善定价。",
        "drawdown": f"回撤和波动决定行业结论的可持有性，最新回撤为{_v3_pct(stats.get('drawdown'), ratio=True)}，六十日滚动超额为{_v3_pct(stats.get('rolling_excess_60d'), ratio=True)}。收益反弹伴随回撤收敛时，赔率改善更可靠；反弹中波动继续抬升，配置节奏需要压低。",
        "demand_financial": f"需求传导仍未完成闭环，收入同比中位数为{_v3_pct(f.get('median_tr_yoy'))}，净利润同比中位数为{_v3_pct(f.get('median_netprofit_yoy'))}，收入利润差为{_v3_pct(profit_gap)}。收入改善没有同步进入利润时，价格成本或费用端仍在侵蚀景气，行业结论不能只看收入端上行。",
        "macro_cycle": f"外部环境只作为需求和估值分母的坐标，制造业PMI为{_v3_num(payload.get('macro', {}).get('pmi_manufacturing'), 1)}，PPI同比为{_v3_num(payload.get('macro', {}).get('ppi_yoy'), 1)}，M2同比为{_v3_num(payload.get('macro', {}).get('m2_yoy'), 1)}。当价格环境弱于流动性时，估值修复更容易先于利润修复，正文结论仍要回到行业自身利润兑现。",
        "supply_capital": f"供给纪律需要用资本周期验证，ROE中位数为{_v3_pct(f.get('median_roe'))}，资产周转率中位数为{_v3_num(f.get('median_assets_turn'), 2)}，资产负债率中位数为{_v3_pct(f.get('median_debt_to_assets'))}。ROE尚未抬升且周转未改善，行业仍在等待利润和效率的共同确认。",
        "profit_pool": f"利润池是中期配置价值的核心，行业收入合计为{_v3_num(f.get('revenue_sum_yi'), 1)}亿元，归母净利润合计为{_v3_num(f.get('profit_sum_yi'), 1)}亿元。收入规模不能单独支持推荐，利润池回升并持续穿透到净利率时，行情才具备基本面承接。",
        "margin_roe": f"利润率传导仍是关键约束，毛利率中位数为{_v3_pct(f.get('median_gross_margin'))}，净利率中位数为{_v3_pct(f.get('median_net_margin'))}，ROE中位数为{_v3_pct(f.get('median_roe'))}。毛利率和净利率的差距反映费用和成本吸收能力，ROE没有同步改善时，行业仍处盈利验证阶段。",
        "dupont": f"杜邦拆解用于判断ROE来源，当前净利率为{_v3_pct(f.get('median_net_margin'))}，资产周转率为{_v3_num(f.get('median_assets_turn'), 2)}，权益乘数为{_v3_num(f.get('median_equity_multiplier'), 2)}。ROE改善需要利润率，周转效率和杠杆结构配合，单靠杠杆抬升不构成高质量修复。",
        "valuation_band": f"估值处在中期赔率的约束位，PE中位数为{_v3_num(m.get('median_pe'), 1)}倍，PE分位为{_v3_pct(m.get('median_pe_pctile'), ratio=True)}，PB分位为{_v3_pct(m.get('median_pb_pctile'), ratio=True)}。估值在中低分位时给利润修复留出空间，估值先行抬升但盈利没有跟进时，胜率下降。",
        "moneyflow_crowding": f"资金边际尚未形成强确认，二十日资金净流入为{_v3_num(m.get('net_mf_20d_yi'), 1)}亿元，换手率中位数为{_v3_num(m.get('median_turnover'), 2)}。资金净流入配合换手温和上行，代表定价开始扩散；资金流弱且换手抬升，更多反映交易扰动。",
        "financial_dispersion": f"公司分布决定行业结论能否外推，样本数为{payload.get('members', {}).get('count', '-')}只，收入同比中位数为{_v3_pct(f.get('median_tr_yoy'))}，ROE中位数为{_v3_pct(f.get('median_roe'))}。箱体分布越集中，行业层面判断越可靠；分布离散时，需要把结论收束到少数优势公司或优势环节。",
        "tracking_table": f"后续跟踪只保留五类变量：需求看{fw.get('demand', '终端需求')}，供给看{fw.get('supply', '产能与库存')}，利润看{fw.get('profit', '价格成本关系')}，估值看PE和PB分位，资金看净流入和换手率。任一链条背离都需要下修结论强度。",
        "final_view": f"{industry}当前结论维持审慎跟踪，核心依据是利润同比仍弱于收入端，ROE没有形成强修复，资金边际未给出持续确认。后续上修条件是收入改善进入利润率，ROE同步回升，估值仍未显著透支。"
    }
    evidence = {
        "industry": industry,
        "industry_type": payload.get("industry_type"),
        "core_variables": fw.get("core"),
        "demand_chain": fw.get("demand"),
        "supply_chain": fw.get("supply"),
        "profit_formula": fw.get("profit"),
        "as_of": pretty_date(payload.get("as_of")),
        "cum_return": _v3_pct(m.get("cum_return"), ratio=True),
        "benchmark_return": _v3_pct(m.get("benchmark_cum_return"), ratio=True),
        "excess_return": _v3_pct(excess, ratio=True),
        "relative_nav": _v3_num(stats.get("relative_nav"), 1),
        "drawdown": _v3_pct(stats.get("drawdown"), ratio=True),
        "rolling_excess_60d": _v3_pct(stats.get("rolling_excess_60d"), ratio=True),
        "pe": _v3_num(m.get("median_pe"), 1),
        "pe_pctile": _v3_pct(m.get("median_pe_pctile"), ratio=True),
        "pb_pctile": _v3_pct(m.get("median_pb_pctile"), ratio=True),
        "tr_yoy": _v3_pct(f.get("median_tr_yoy")),
        "netprofit_yoy": _v3_pct(f.get("median_netprofit_yoy")),
        "profit_gap": _v3_pct(profit_gap),
        "gross_margin": _v3_pct(f.get("median_gross_margin")),
        "net_margin": _v3_pct(f.get("median_net_margin")),
        "roe": _v3_pct(f.get("median_roe")),
        "assets_turn": _v3_num(f.get("median_assets_turn"), 2),
        "debt_to_assets": _v3_pct(f.get("median_debt_to_assets")),
        "equity_multiplier": _v3_num(f.get("median_equity_multiplier"), 2),
        "netflow_20d": _v3_num(m.get("net_mf_20d_yi"), 1),
        "turnover": _v3_num(m.get("median_turnover"), 2),
        "score": _v3_num(sig.get("latest_score"), 2),
    }
    sections: dict[str, str] = {}
    for key, draft in drafts.items():
        if key == "investment_conclusion":
            sections[key] = _v4_clean_text(draft, keep_newline=True)
        else:
            sections[key] = self.ai.rewrite(key, evidence, draft)
    return sections


SafeAIWriter.rewrite = _v4_rewrite
IndustryDepthReportAgent.make_figures = _v4_make_figures
IndustryDepthReportAgent.make_sections = _v4_make_sections
IndustryDepthReportAgent.plot_v4_thesis_map = _v4_plot_thesis_map
IndustryDepthReportAgent.plot_v4_relative_return = _v4_plot_relative_return
IndustryDepthReportAgent.plot_v4_drawdown = _v4_plot_drawdown
IndustryDepthReportAgent.plot_v4_demand_financial = _v4_plot_demand_financial
IndustryDepthReportAgent.plot_v4_macro_cycle = _v4_plot_macro_cycle
IndustryDepthReportAgent.plot_v4_supply_capital = _v4_plot_supply_capital
IndustryDepthReportAgent.plot_v4_profit_pool = _v4_plot_profit_pool
IndustryDepthReportAgent.plot_v4_margin_roe = _v4_plot_margin_roe
IndustryDepthReportAgent.plot_v4_dupont = _v4_plot_dupont
IndustryDepthReportAgent.plot_v4_valuation_band = _v4_plot_valuation_band
IndustryDepthReportAgent.plot_v4_moneyflow_crowding = _v4_plot_moneyflow_crowding
IndustryDepthReportAgent.plot_v4_financial_dispersion = _v4_plot_financial_dispersion
IndustryDepthReportAgent.plot_v4_tracking_table = _v4_plot_tracking_table


def _v4_strip_framework_prefix(value: Any) -> str:
    text = str(value or "")
    prefixes = [
        "需求需要拆成",
        "供给需要看",
        "行业利润取决于",
        "利润取决于",
        "需求看",
        "供给看",
    ]
    for prefix in prefixes:
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text.strip(" 。.，,")


def _v4_make_sections_refined(self, payload: dict[str, Any]) -> dict[str, str]:
    sections = _v4_make_sections(self, payload)
    fw = payload.get("framework", {})
    industry = payload["industry"]
    m, f = payload["market"], payload["financial"]
    demand = _v4_strip_framework_prefix(fw.get("demand", "终端需求"))
    supply = _v4_strip_framework_prefix(fw.get("supply", "产能与库存"))
    profit = _v4_strip_framework_prefix(fw.get("profit", "价格成本关系"))
    sections["tracking_table"] = _v4_clean_text(
        f"后续跟踪压缩为五条线：需求看{demand}，供给看{supply}，利润看{profit}，估值看PE和PB分位，资金看净流入和换手率。"
        f"目前净利润同比中位数为{_v3_pct(f.get('median_netprofit_yoy'))}，ROE中位数为{_v3_pct(f.get('median_roe'))}，二十日资金净流入为{_v3_num(m.get('net_mf_20d_yi'), 1)}亿元，结论仍以盈利兑现为主。"
    )
    sections["profit_pool"] = _v4_clean_text(
        f"利润池是中期配置价值的核心，最新可见期收入合计为{_v3_num(f.get('revenue_sum_yi'), 1)}亿元，归母净利润合计为{_v3_num(f.get('profit_sum_yi'), 1)}亿元。"
        f"图中进一步用滚动四期观察收入和利润池，目的在于剔除季节性累计口径干扰；利润池回升并穿透到净利率时，行情才具备基本面承接。"
    )
    sections["final_view"] = _v4_clean_text(
        f"{industry}维持审慎跟踪，核心约束来自利润同比弱于收入端，ROE尚未形成强修复，资金边际也未给出连续确认。"
        f"结论上修需要看到收入改善进入利润率，ROE同步回升，估值仍未明显透支；三条线缺一条，行业判断都不宜提前抬升。"
    )
    return sections


IndustryDepthReportAgent.make_sections = _v4_make_sections_refined




def _v4_fin_axis(ax) -> None:
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))


def _v4_ytd_to_ttm(df: pd.DataFrame, value_col: str) -> pd.Series:
    work = df[["date", value_col]].copy()
    work[value_col] = pd.to_numeric(work[value_col], errors="coerce")
    work["year"] = work["date"].dt.year
    work["quarter"] = work["date"].dt.quarter
    quarter_values = []
    prev_by_year: dict[int, float] = {}
    for _, row in work.iterrows():
        year = int(row["year"]) if pd.notna(row["year"]) else -1
        val = row[value_col]
        if pd.isna(val):
            quarter_values.append(np.nan)
            continue
        prev = prev_by_year.get(year)
        if int(row["quarter"]) == 1 or prev is None:
            qv = val
        else:
            qv = val - prev
        prev_by_year[year] = float(val)
        quarter_values.append(qv)
    q = pd.Series(quarter_values, index=df.index, dtype="float64")
    return q.rolling(4, min_periods=2).sum()


def _v4_plot_demand_financial_refined(self, payload: dict[str, Any], path: Path) -> Path | None:
    df = _v3_records(payload, "financial", "financial_series")
    if df.empty:
        return None
    df["date"] = _v3_date_col(df, "end_date")
    tr = pd.to_numeric(df["median_tr_yoy"], errors="coerce")
    npy = pd.to_numeric(df["median_netprofit_yoy"], errors="coerce")
    gap = tr - npy
    fig, axes = plt.subplots(2, 1, figsize=(11.0, 5.5), sharex=True, height_ratios=[1.15, 0.9])
    axes[0].plot(df["date"], tr, color=REPORT_RED, lw=1.6, marker="o", ms=3.0, label="收入同比")
    axes[0].plot(df["date"], npy, color="#2F75B5", lw=1.45, marker="o", ms=2.8, label="利润同比")
    axes[0].axhline(0, color="#000000", lw=0.8)
    axes[0].set_ylabel("同比")
    axes[1].bar(df["date"], gap, color=np.where(gap >= 0, "#D9D9D9", "#F7D9D3"), width=55, label="收入利润差")
    axes[1].axhline(0, color="#000000", lw=0.8)
    axes[1].set_ylabel("百分点")
    _v4_annotate_last(axes[0], df["date"], tr, "{:.1f}", "%", REPORT_RED)
    _v4_annotate_last(axes[0], df["date"], npy, "{:.1f}", "%", "#2F75B5")
    _v4_fin_axis(axes[1])
    _v4_title(axes[0], f"图4 {payload['industry']}需求进入报表的力度", "收入看需求传导，利润看价格成本和经营杠杆后的兑现")
    for ax in axes:
        ax.yaxis.set_major_formatter(lambda v, pos: f"{v:.0f}%")
        _v3_style_axis(ax, grid=True)
        _v4_legend(ax, 2)
    return _v3_save(fig, path)


def _v4_plot_supply_capital_refined(self, payload: dict[str, Any], path: Path) -> Path | None:
    df = _v3_records(payload, "financial", "financial_series")
    if df.empty:
        return None
    df["date"] = _v3_date_col(df, "end_date")
    roe = pd.to_numeric(df["median_roe"], errors="coerce")
    turn = pd.to_numeric(df["median_assets_turn"], errors="coerce")
    debt = pd.to_numeric(df["median_debt_to_assets"], errors="coerce")
    fig, axes = plt.subplots(2, 1, figsize=(11.0, 5.6), sharex=True, height_ratios=[1.12, 0.9])
    axes[0].plot(df["date"], roe, color=REPORT_RED, lw=1.55, marker="o", ms=2.8, label="ROE")
    ax2 = axes[0].twinx()
    ax2.plot(df["date"], turn, color="#2F75B5", lw=1.35, label="资产周转率")
    axes[0].axhline(0, color="#000000", lw=0.8)
    axes[0].set_ylabel("ROE")
    ax2.set_ylabel("周转率")
    axes[1].plot(df["date"], debt, color="#7F7F7F", lw=1.45, label="资产负债率")
    axes[1].set_ylabel("负债率")
    _v4_annotate_last(axes[0], df["date"], roe, "{:.1f}", "%", REPORT_RED)
    _v4_annotate_last(axes[1], df["date"], debt, "{:.1f}", "%", "#7F7F7F")
    _v4_fin_axis(axes[1])
    _v4_title(axes[0], f"图6 {payload['industry']}资本周期与供给纪律", "ROE回升但周转和杠杆未扩张时，供给约束更有利于利润留存")
    for ax in [axes[0], axes[1], ax2]:
        _v3_style_axis(ax, grid=ax is not ax2)
    axes[0].legend(loc="lower left", bbox_to_anchor=(0.20, -0.31), frameon=False, fontsize=8.4)
    ax2.legend(loc="lower left", bbox_to_anchor=(0.46, -0.31), frameon=False, fontsize=8.4)
    _v4_legend(axes[1], 1)
    return _v3_save(fig, path)


def _v4_plot_profit_pool_refined(self, payload: dict[str, Any], path: Path) -> Path | None:
    df = _v3_records(payload, "financial", "financial_series")
    if df.empty:
        return None
    df["date"] = _v3_date_col(df, "end_date")
    revenue_ttm = _v4_ytd_to_ttm(df, "revenue_sum") / 1e8
    profit_ttm = _v4_ytd_to_ttm(df, "profit_sum") / 1e8
    fig, axes = plt.subplots(2, 1, figsize=(11.0, 5.7), sharex=True, height_ratios=[1.05, 1])
    axes[0].plot(df["date"], revenue_ttm, color=REPORT_RED, lw=1.55, marker="o", ms=2.8, label="收入滚动四期")
    axes[0].set_ylabel("亿元")
    axes[1].bar(df["date"], profit_ttm, color=np.where(profit_ttm >= 0, "#F7D9D3", "#A5A5A5"), width=55, label="利润滚动四期")
    axes[1].plot(df["date"], profit_ttm.rolling(4, min_periods=1).mean(), color="#2F75B5", lw=1.3, label="四期均值")
    axes[1].axhline(0, color="#000000", lw=0.8)
    axes[1].set_ylabel("亿元")
    _v4_annotate_last(axes[0], df["date"], revenue_ttm, "{:.1f}", "亿元", REPORT_RED)
    _v4_annotate_last(axes[1], df["date"], profit_ttm, "{:.1f}", "亿元", "#2F75B5")
    _v4_fin_axis(axes[1])
    _v4_title(axes[0], f"图7 {payload['industry']}收入规模与利润池", "累计值还原为单季后滚动四期，避免季节性锯齿干扰判断")
    for ax in axes:
        _v3_style_axis(ax, grid=True)
        _v4_legend(ax, 2)
    return _v3_save(fig, path)


def _v4_plot_margin_roe_refined(self, payload: dict[str, Any], path: Path) -> Path | None:
    df = _v3_records(payload, "financial", "financial_series")
    if df.empty:
        return None
    df["date"] = _v3_date_col(df, "end_date")
    fig, ax = plt.subplots(figsize=(11.0, 4.7))
    series = [
        ("毛利率", "median_gross_margin", "#ED7D31"),
        ("净利率", "median_net_margin", REPORT_RED),
        ("ROE", "median_roe", "#2F75B5"),
    ]
    for label, col, color in series:
        s = pd.to_numeric(df[col], errors="coerce")
        ax.plot(df["date"], s, lw=1.45, color=color, marker="o", ms=2.6, label=label)
        _v4_annotate_last(ax, df["date"], s, "{:.1f}", "%", color)
    ax.axhline(0, color="#000000", lw=0.8)
    ax.set_ylabel("百分比")
    ax.yaxis.set_major_formatter(lambda v, pos: f"{v:.0f}%")
    _v4_fin_axis(ax)
    _v4_legend(ax, 3)
    _v4_title(ax, f"图8 {payload['industry']}利润率到ROE的传导", "毛利率领先观察价格成本，净利率和ROE验证经营杠杆")
    _v3_style_axis(ax, grid=True)
    return _v3_save(fig, path)


def _v4_plot_dupont_refined(self, payload: dict[str, Any], path: Path) -> Path | None:
    df = _v3_records(payload, "financial", "financial_series")
    if df.empty:
        return None
    df["date"] = _v3_date_col(df, "end_date")
    fig, axes = plt.subplots(2, 2, figsize=(11.0, 6.0), sharex=True)
    specs = [
        ("median_net_margin", "净利率", REPORT_RED, "%"),
        ("median_assets_turn", "资产周转率", "#2F75B5", ""),
        ("median_equity_multiplier", "权益乘数", "#ED7D31", "倍"),
        ("median_roe", "ROE", "#7F7F7F", "%"),
    ]
    for ax, (col, label, color, suffix) in zip(axes.ravel(), specs):
        if col not in df:
            continue
        s = pd.to_numeric(df[col], errors="coerce")
        ax.plot(df["date"], s, color=color, lw=1.35, marker="o", ms=2.4, label=label)
        if suffix == "%":
            ax.yaxis.set_major_formatter(lambda v, pos: f"{v:.0f}%")
        _v4_annotate_last(ax, df["date"], s, "{:.1f}", suffix, color)
        ax.set_ylabel(label)
        _v4_fin_axis(ax)
        _v3_style_axis(ax, grid=True)
    _v4_title(axes.ravel()[0], f"图9 {payload['industry']}杜邦拆解", "分面显示净利率，周转和杠杆，避免单图折线互相遮蔽")
    return _v3_save(fig, path)


IndustryDepthReportAgent.plot_v4_demand_financial = _v4_plot_demand_financial_refined
IndustryDepthReportAgent.plot_v4_supply_capital = _v4_plot_supply_capital_refined
IndustryDepthReportAgent.plot_v4_profit_pool = _v4_plot_profit_pool_refined
IndustryDepthReportAgent.plot_v4_margin_roe = _v4_plot_margin_roe_refined
IndustryDepthReportAgent.plot_v4_dupont = _v4_plot_dupont_refined

# === INDUSTRY REPORT V4 OVERRIDES END ===


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--industry", required=True)
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--lookback-years", type=int, default=3)
    parser.add_argument("--db", default=str(DB_DEFAULT))
    parser.add_argument("--out", default=str(OUT_DEFAULT))
    parser.add_argument("--use-ai", action="store_true")
    args = parser.parse_args()
    agent = IndustryDepthReportAgent(args.db, args.out, use_ai=args.use_ai)
    try:
        result = agent.generate(args.industry, args.as_of, args.lookback_years)
        print(json.dumps({
            "status": result.status,
            "job_id": result.job_id,
            "docx": str(result.docx_path),
            "payload": str(result.payload_path),
            "figures": [str(p) for p in result.figures],
        }, ensure_ascii=False, indent=2))
    except DataQualityError as exc:
        print(json.dumps({"status": "blocked", "message": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
