# -*- coding: utf-8 -*-
"""申万一级行业深度报告 Agent Web 服务。"""
from __future__ import annotations

import base64
import html as html_lib
import json
import mimetypes
import os
import sys
import threading
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request, send_file

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parents[2]
MODEL_DIR = PROJECT_ROOT / "model" / "data_dashboard" / "industry_depth_report"
if str(MODEL_DIR) not in sys.path:
    sys.path.insert(0, str(MODEL_DIR))

from industry_depth_agent import IndustryDepthReportAgent  # noqa: E402

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False

APP_VERSION = "IndustryDepthReport/0.3"
JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()

SW_LEVEL1 = [
    "农林牧渔", "基础化工", "钢铁", "有色金属", "电子", "家用电器", "食品饮料", "纺织服饰", "轻工制造",
    "医药生物", "公用事业", "交通运输", "房地产", "商贸零售", "社会服务", "综合", "建筑材料", "建筑装饰",
    "电力设备", "国防军工", "计算机", "传媒", "通信", "银行", "非银金融", "汽车", "机械设备", "煤炭",
    "石油石化", "环保", "美容护理",
]


def _auth_configured() -> bool:
    return bool(os.environ.get("INDUSTRY_REPORT_USER") and os.environ.get("INDUSTRY_REPORT_PASSWORD"))


def _auth_ok() -> bool:
    if not _auth_configured():
        return True
    auth = request.authorization
    return bool(
        auth
        and auth.username == os.environ.get("INDUSTRY_REPORT_USER")
        and auth.password == os.environ.get("INDUSTRY_REPORT_PASSWORD")
    )


@app.before_request
def require_auth() -> Response | None:
    public_paths = {"/healthz"}
    protected = request.path not in public_paths
    if protected and not _auth_ok():
        return Response("Auth required", 401, {"WWW-Authenticate": 'Basic realm="IndustryDepthReport"'})
    return None


def secure_path(raw_path: str) -> Path:
    path = Path(raw_path).resolve()
    root = PROJECT_ROOT.resolve()
    if path != root and root not in path.parents:
        raise ValueError("文件路径不在报告项目目录内")
    if not path.exists():
        raise FileNotFoundError(str(path))
    return path


def normalize_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"0", "false", "off", "no", "n", "关闭", "否"}:
        return False
    if text in {"1", "true", "on", "yes", "y", "开启", "是"}:
        return True
    return default


def h(value: Any) -> str:
    return html_lib.escape("" if value is None else str(value), quote=True)


def fmt_num(value: Any, digits: int = 2) -> str:
    try:
        if value is None or value == "":
            return "--"
        return f"{float(value):.{digits}f}"
    except Exception:
        return "--"


def table_html(headers: list[str], rows: list[list[Any]], class_name: str = "") -> str:
    if not rows:
        return ""
    th = "".join(f"<th>{h(x)}</th>" for x in headers)
    body = []
    for row in rows:
        body.append("<tr>" + "".join(f"<td>{h(x)}</td>" for x in row) + "</tr>")
    return f'<table class="{h(class_name)}"><thead><tr>{th}</tr></thead><tbody>{"".join(body)}</tbody></table>'


def paragraph(text: Any) -> str:
    text = str(text or "").strip()
    if not text:
        return ""
    return f"<p>{h(text)}</p>"


def bullets(items: list[Any]) -> str:
    clean = [str(x).strip() for x in items if str(x or "").strip()]
    if not clean:
        return ""
    return '<ul class="clean-list">' + "".join(f"<li>{h(x)}</li>" for x in clean) + "</ul>"


def figure_html(figures: dict[str, str], key: str, caption: str) -> str:
    raw_path = figures.get(key)
    if not raw_path:
        return ""
    path = Path(raw_path)
    if not path.exists():
        return ""
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return (
        '<figure class="report-figure">'
        f'<img src="data:{mime};base64,{data}" alt="{h(caption)}" />'
        f'<figcaption>{h(caption)}</figcaption>'
        '</figure>'
    )


def extract_company_rows(payload: dict[str, Any]) -> list[list[Any]]:
    rows = []
    for item in payload.get("top_companies", [])[:8]:
        rows.append([
            item.get("rank", ""),
            item.get("name", ""),
            fmt_num(item.get("score"), 2),
            fmt_num(item.get("market_cap"), 2),
            fmt_num(item.get("roe_ttm"), 2),
            fmt_num(item.get("revenue_growth_yoy"), 2),
        ])
    return rows


def build_preview_html(payload_path: Path, output_dir: Path) -> Path:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    industry = payload.get("industry") or "申万一级行业"
    as_of = payload.get("as_of") or payload.get("report_date") or ""
    start_date = payload.get("start_date") or ""
    framework = payload.get("framework") or {}
    market = payload.get("market") or {}
    financial = payload.get("financial") or {}
    macro = payload.get("macro") or {}
    signal = payload.get("signal") or {}
    figures = payload.get("figures") or {}
    sections = payload.get("sections") or {}
    quality = payload.get("quality_gate") or {}
    members = payload.get("members") or {}

    def date_label(value: Any) -> str:
        s = str(value or "").replace("-", "")
        if len(s) >= 8:
            return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
        return str(value or "")

    def pct_ratio(v: Any, digits: int = 1) -> str:
        try:
            if v is None or v == "":
                return "-"
            return f"{float(v) * 100:.{digits}f}%"
        except Exception:
            return "-"

    def pct_value(v: Any, digits: int = 1) -> str:
        try:
            if v is None or v == "":
                return "-"
            return f"{float(v):.{digits}f}%"
        except Exception:
            return "-"

    view = "超配观察" if (float(market.get("cum_return") or 0) > float(market.get("benchmark_cum_return") or 0) and float(financial.get("median_netprofit_yoy") or 0) > 0) else "中性跟踪"
    warnings = quality.get("warnings") or []

    conclusion_rows = [
        ["质量门", "通过" if quality.get("passed", True) else "未通过", f"警告{len(warnings)}项"],
        ["行业样本", f"{members.get('count', '-')}只成分股", "申万一级当前成分聚合"],
        ["行业属性", payload.get("industry_type", "-"), framework.get("core", "-")],
        ["区间收益", pct_ratio(market.get("cum_return")), "观察市场定价强度"],
        ["宽基收益", pct_ratio(market.get("benchmark_cum_return")), "作为全市场等权参照"],
        ["PE/PB", f"{fmt_num(market.get('median_pe'), 1)}倍 / {fmt_num(market.get('median_pb'), 2)}倍", f"PE分位{pct_ratio(market.get('median_pe_pctile'))}"],
        ["财务状态", f"收入同比{pct_value(financial.get('median_tr_yoy'))}", f"净利润同比{pct_value(financial.get('median_netprofit_yoy'))}，ROE{pct_value(financial.get('median_roe'))}"],
        ["中期结论", view, "由需求、供给、利润、估值、资金共同决定"],
    ]
    demand_rows = [
        ["总量需求", "financial_report_visible", "收入同比、利润同比、利润池变化"],
        ["宏观需求", "macro_monthly", "PMI、PPI、M1、M2"],
        ["市场验证", "stock_ohlcv_daily", "行业收益、成交额、相对收益"],
        ["行业高频", "行业专属接口", "库存、批价、订单、排产、利差等"],
    ]
    financial_rows = [
        ["收入合计", f"{fmt_num(financial.get('revenue_sum_yi'), 1)}亿元", "行业规模"],
        ["归母净利润合计", f"{fmt_num(financial.get('profit_sum_yi'), 1)}亿元", "利润池"],
        ["收入同比中位数", pct_value(financial.get("median_tr_yoy")), "景气传导"],
        ["净利润同比中位数", pct_value(financial.get("median_netprofit_yoy")), "盈利弹性"],
        ["毛利率中位数", pct_value(financial.get("median_gross_margin")), "价格成本关系"],
        ["净利率中位数", pct_value(financial.get("median_net_margin")), "费用和经营杠杆"],
        ["ROE中位数", pct_value(financial.get("median_roe")), "资本回报"],
        ["资产负债率中位数", pct_value(financial.get("median_debt_to_assets")), "财务承压"],
    ]
    scenario_rows = [
        ["乐观", "需求继续改善，价格或订单保持强势", "毛利率和ROE同步修复", "估值分位仍可上行", "提高行业结论强度"],
        ["中性", "需求温和修复，价格平稳", "收入改善快于利润率", "估值围绕中枢震荡", "维持跟踪或标配"],
        ["悲观", "需求低于预期，库存或价格承压", "净利润同比回落，现金流转弱", "估值和资金同步收缩", "下调行业判断"],
    ]
    data_rows = [
        ["成分股", "sw_l1_industry_daily", "确定申万一级行业样本"],
        ["行情", "stock_ohlcv_daily", "计算行业等权收益、波动和成交额"],
        ["估值", "stock_valuation_daily", "计算PE、PB、PS、股息率和换手"],
        ["资金流", "stock_moneyflow_daily", "计算行业资金流和交易边际"],
        ["财报", "financial_report_visible", "计算收入、利润、利润率、ROE和资产周转"],
        ["宏观", "macro_monthly", "校验增长、价格和流动性"],
        ["模型信号", "v3_industry_signal", "辅助中期配置判断"],
        ["研报索引", "broker_report_index", "记录本地研报证据"],
    ]

    pages: list[str] = []
    pages.append("".join([
        f"<h1>{h(industry)}行业深度研究报告</h1>",
        f'<div class="subtitle">报告日期：{h(date_label(as_of))}　回看区间：{h(date_label(start_date))} 至 {h(date_label(as_of))}</div>',
        '<div class="section-title">一 投资结论</div>',
        paragraph(sections.get("investment_conclusion", "")),
        table_html(["项目", "当前读数", "研究含义"], conclusion_rows, "compact"),
        figure_html(figures, "framework", "图表结论：研究链路从需求、供给、利润传导到财务和市场定价，所有结论必须有数据或图表证据。"),
    ]))
    pages.append("".join([
        '<div class="section-title">二 商业模式与产业链</div>',
        paragraph(sections.get("business_chain", "")),
        table_html(["层次", "核心问题", "当前数据抓手"], [
            ["上游", "资源、能源、技术、牌照、数据和流量由谁控制", framework.get("supply", "-")],
            ["中游", "生产、制造、服务、平台或渠道如何组织", "收入、利润率、资产周转率、ROE"],
            ["下游", "客户预算来自居民、企业、政府、出口还是资本开支", framework.get("demand", "-")],
            ["利润池", "利润留在价格端、成本端、渠道端还是技术端", framework.get("profit", "-")],
        ], "compact"),
        '<div class="section-title">三 中期需求拆解</div>',
        paragraph(sections.get("demand_analysis", "")),
        table_html(["指标组", "稳定数据源", "分析方式"], demand_rows, "compact"),
        figure_html(figures, "returns", "图表结论：收益曲线确认市场是否已经开始定价景气变化。"),
        figure_html(figures, "demand", "图表结论：需求链同时观察收入增速、宏观景气和成交活跃，单一股价上涨不能替代需求改善。"),
    ]))
    pages.append("".join([
        '<div class="section-title">四 中期供给拆解</div>',
        paragraph(sections.get("supply_analysis", "")),
        figure_html(figures, "supply", "图表结论：供给端先用资本周期代理指标判断供给纪律，后续用行业专属产能、库存和开工率补强。"),
        '<div class="section-title">五 价格机制与利润弹性</div>',
        paragraph(sections.get("price_profit", "")),
        table_html(["利润变量", "当前读数", "解释"], financial_rows[:7], "compact"),
        figure_html(figures, "profit", "图表结论：利润池和利润率共同验证景气是否进入报表。"),
    ]))
    pages.append("".join([
        '<div class="section-title">六 库存周期与资本周期</div>',
        paragraph(sections.get("cycle_position", "")),
        figure_html(figures, "valuation", "图表结论：估值分位用于判断市场是否已经充分反映中期盈利变化。"),
        figure_html(figures, "moneyflow", "图表结论：资金面用于观察交易边际和拥挤度变化。"),
        '<div class="section-title">七 政策与产业趋势</div>',
        paragraph(sections.get("policy_macro", "")),
        figure_html(figures, "macro", "图表结论：宏观图解释需求和分母端环境，不能替代行业自身数据。"),
    ]))
    pages.append("".join([
        '<div class="section-title">八 行业竞争格局</div>',
        paragraph(sections.get("competition", "")),
        paragraph("本版不在缺少份额数据时强行写集中度结论。后续数据层应补充CR3、CR5、龙头份额、细分产品价格和产能份额。"),
        '<div class="section-title">九 行业财务验证</div>',
        paragraph(sections.get("financial_validation", "")),
        table_html(["财务指标", "最新读数", "研究含义"], financial_rows, "compact"),
        figure_html(figures, "financial", "图表结论：财务聚合验证行业逻辑是否已经落到收入、利润和ROE。"),
    ]))
    pages.append("".join([
        '<div class="section-title">十 行业估值与市场定价</div>',
        paragraph(sections.get("market_pricing", "")),
        figure_html(figures, "signal", "图表结论：内部模型信号用于辅助观察中期配置权重和景气方向。"),
        '<div class="section-title">十一 中期情景推演</div>',
        paragraph(sections.get("scenario_tracking", "")),
        table_html(["情景", "需求假设", "利润假设", "估值假设", "跟踪动作"], scenario_rows, "compact"),
        figure_html(figures, "scenario", "图表结论：情景矩阵用于把需求、利润、估值和操作动作放到同一张表里复核。"),
    ]))
    pages.append("".join([
        '<div class="section-title">十二 跟踪指标、数据源与风险提示</div>',
        paragraph(sections.get("data_logic", "")),
        table_html(["数据模块", "数据库表", "用途"], data_rows, "compact"),
        figure_html(figures, "data_map", "图表结论：数据链路保证每个正式结论都能追溯到数据库、质量门或图表。"),
    ]))

    page_html = "".join(f'<section class="report-page">{page}</section>' for page in pages)
    preview = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{h(industry)}行业深度研究报告预览</title>
<style>
  :root {{ --ink:#000; --muted:#555; --line:#cfd5df; --accent:#b83222; --paper:#fff; --bg:#eef1f5; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink); font-family: Arial, KaiTi, STKaiti, SimKai, serif; }}
  .preview-shell {{ padding: 26px 0 42px; }}
  .report-page {{ width: 794px; min-height: 1123px; margin: 0 auto 20px; padding: 70px 68px 66px; background: var(--paper); box-shadow: 0 2px 18px rgba(20,28,38,.18); overflow: hidden; }}
  h1 {{ margin: 0 0 14px; text-align: center; font-size: 25px; line-height: 1.35; font-weight: 700; letter-spacing: 0; }}
  .subtitle {{ text-align:center; color:var(--muted); font-size: 13px; margin-bottom: 28px; }}
  .section-title {{ margin: 20px 0 9px; padding-left: 10px; border-left: 4px solid var(--accent); font-size: 17px; line-height: 1.45; font-weight: 700; }}
  p {{ margin: 0 0 8px; font-size: 14px; line-height: 1.72; text-indent: 2em; text-align: justify; }}
  table {{ width: 100%; border-collapse: collapse; margin: 10px 0 14px; table-layout: fixed; font-size: 12px; }}
  th {{ background: #f2f4f7; border: 1px solid var(--line); padding: 7px 6px; text-align: center; font-weight: 700; }}
  td {{ border: 1px solid var(--line); padding: 7px 6px; line-height: 1.5; vertical-align: top; word-break: break-word; }}
  .report-figure {{ margin: 12px 0 16px; page-break-inside: avoid; }}
  .report-figure img {{ display: block; width: 100%; max-height: 372px; object-fit: contain; border: 1px solid #d7dce5; background: #fff; }}
  figcaption {{ margin-top: 6px; text-align: left; color: var(--ink); font-size: 13px; line-height: 1.55; text-indent: 2em; }}
  @media (max-width: 860px) {{ .preview-shell {{ padding: 0; }} .report-page {{ width: 100%; min-height: 0; margin: 0 0 12px; padding: 42px 26px; box-shadow: none; }} }}
</style>
</head>
<body><main class="preview-shell">{page_html}</main></body>
</html>"""
    preview_path = output_dir / "report_preview.html"
    preview_path.write_text(preview, encoding="utf-8")
    return preview_path





# === INDUSTRY REPORT V3 PREVIEW START ===
def build_preview_html(payload_path: Path, output_dir: Path) -> Path:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    industry = payload.get("industry") or "行业"
    as_of = payload.get("as_of") or ""
    start_date = payload.get("start_date") or ""
    figures = payload.get("figures") or {}
    sections = payload.get("sections") or {}

    def img(key: str) -> str:
        raw = figures.get(key)
        if not raw:
            return ""
        path = Path(raw)
        if not path.exists():
            return ""
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return f'<figure class="report-figure"><img src="data:image/png;base64,{data}" alt="{h(key)}" /></figure>'

    def para(text: str, strong: bool = False) -> str:
        text = str(text or "").strip()
        if not text:
            return ""
        text = text.replace("数据来源", "").replace("数据源", "").replace("图表结论：", "").replace("图表结论", "")
        cls = "analysis strong" if strong else "analysis"
        return f'<p class="{cls}">{h(text)}</p>'

    def bullets(text: str) -> str:
        parts = [x.strip() for x in str(text or "").splitlines() if x.strip()]
        return "".join(para(x, True) for x in parts)

    def block(title: str, pairs: list[tuple[str, str]]) -> str:
        body = [f'<div class="section-title">{h(title)}</div>']
        for key, comment in pairs:
            body.append(img(key))
            body.append(para(comment, True))
        return "\n".join(body)

    pages = []
    pages.append(
        '<section class="report-page cover">'
        f'<h1>{h(industry)}行业深度研究报告</h1>'
        f'<div class="subtitle">报告日期：{h(pretty_date(as_of) if "pretty_date" in globals() else as_of)}　回看区间：{h(start_date)} 至 {h(as_of)}</div>'
        '<div class="section-title">一 投资结论</div>'
        + bullets(sections.get("investment_conclusion", ""))
        + img("conclusion")
        + para(sections.get("conclusion", ""), True)
        + '</section>'
    )
    pages.append('<section class="report-page">' + block("二 市场定价与周期位置", [
        ("relative_return", sections.get("relative_return", "")),
        ("drawdown", sections.get("drawdown", "")),
        ("valuation_band", sections.get("valuation_band", "")),
    ]) + '</section>')
    pages.append('<section class="report-page">' + block("三 中期需求拆解", [
        ("demand_financial", sections.get("demand_financial", "")),
        ("macro_cycle", sections.get("macro_cycle", "")),
    ]) + '</section>')
    pages.append('<section class="report-page">' + block("四 供给约束与资本周期", [
        ("supply_capital", sections.get("supply_capital", "")),
        ("dupont", sections.get("dupont", "")),
    ]) + '</section>')
    pages.append('<section class="report-page">' + block("五 价格机制与利润弹性", [
        ("profit_pool", sections.get("profit_pool", "")),
        ("margin_roe", sections.get("margin_roe", "")),
    ]) + '</section>')
    pages.append('<section class="report-page">' + block("六 竞争格局与财务分化", [
        ("financial_dispersion", sections.get("financial_dispersion", "")),
        ("moneyflow_crowding", sections.get("moneyflow_crowding", "")),
    ]) + '</section>')
    pages.append('<section class="report-page">' + block("七 资金边际与配置线索", [
        ("signal_weight", sections.get("signal_weight", "")),
        ("risk_tracking", sections.get("risk_tracking", "")),
    ]) + para(sections.get("final_view", ""), True) + '</section>')

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{h(industry)}行业深度研究报告预览</title>
<style>
  :root {{ --ink:#000; --muted:#555; --line:#d6dbe3; --accent:#c71e37; --paper:#fff; --bg:#eef1f5; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink); font-family:Arial, KaiTi, STKaiti, SimKai, serif; }}
  .preview-shell {{ padding:26px 0 42px; }}
  .report-page {{ width:794px; min-height:1123px; margin:0 auto 20px; padding:62px 62px 60px; background:var(--paper); box-shadow:0 2px 18px rgba(20,28,38,.16); overflow:hidden; }}
  h1 {{ margin:0 0 14px; text-align:center; font-size:25px; line-height:1.35; font-weight:700; letter-spacing:0; }}
  .subtitle {{ text-align:center; color:var(--muted); font-size:13px; margin-bottom:26px; }}
  .section-title {{ margin:16px 0 10px; padding-left:10px; border-left:4px solid var(--accent); font-size:17px; line-height:1.45; font-weight:700; }}
  .analysis {{ margin:8px 0 12px; font-size:13.5px; line-height:1.62; text-indent:2em; }}
  .analysis.strong {{ font-weight:700; }}
  .report-figure {{ margin:10px 0 8px; page-break-inside:avoid; }}
  .report-figure img {{ display:block; width:100%; max-height:382px; object-fit:contain; background:#fff; }}
  .cover .report-figure img {{ max-height:410px; }}
  @media (max-width:860px) {{ .preview-shell {{ padding:10px; }} .report-page {{ width:100%; min-height:auto; padding:34px 24px; }} }}
</style>
</head>
<body><main class="preview-shell">{''.join(pages)}</main></body></html>"""
    out = output_dir / "report_preview.html"
    out.write_text(html, encoding="utf-8")
    return out
# === INDUSTRY REPORT V3 PREVIEW END ===

INDEX_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>申万一级行业深度报告 Agent</title>
<style>
  :root { --bg:#f3f5f8; --panel:#fff; --line:#d7dce5; --ink:#111; --muted:#606975; --red:#b83222; --red-dark:#9c281c; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--ink); font-family: Arial, KaiTi, STKaiti, SimKai, serif; }
  .page { max-width: 1500px; margin: 36px auto; padding: 0 28px; }
  header { display:flex; align-items:flex-end; justify-content:space-between; gap:18px; margin-bottom:24px; }
  h1 { margin:0; font-size:32px; line-height:1.25; letter-spacing:0; }
  .subtitle { margin-top:8px; color:var(--muted); font-size:15px; }
  .badge { padding:5px 12px; background:#fff; border-radius:999px; font-size:14px; color:#222; box-shadow:0 1px 6px rgba(0,0,0,.04); white-space:nowrap; }
  .panel { background:var(--panel); border:1px solid var(--line); border-radius:8px; box-shadow:0 1px 4px rgba(18,24,32,.04); }
  form.panel { display:grid; grid-template-columns: 1.25fr 1.05fr .8fr .85fr auto; gap:16px; align-items:end; padding:24px; margin-bottom:22px; }
  label { display:block; font-weight:700; font-size:15px; margin-bottom:9px; }
  select, input { width:100%; height:48px; border:1px solid #bfc7d2; border-radius:5px; background:#fff; padding:0 14px; font-size:16px; font-family:Arial, KaiTi, STKaiti, SimKai, serif; color:#111; }
  button, .download-btn { height:48px; border:0; border-radius:5px; background:var(--red); color:#fff; padding:0 26px; font-size:16px; font-weight:700; font-family:Arial, KaiTi, STKaiti, SimKai, serif; cursor:pointer; text-decoration:none; display:inline-flex; align-items:center; justify-content:center; }
  button:hover, .download-btn:hover { background:var(--red-dark); }
  button:disabled { opacity:.58; cursor:not-allowed; }
  .result { padding:22px; min-height:170px; }
  .meta { display:flex; flex-wrap:wrap; gap:10px 18px; align-items:center; font-size:16px; margin-bottom:16px; }
  .meta strong { font-family:Arial, sans-serif; }
  .message { color:var(--muted); line-height:1.6; font-size:15px; }
  .error { color:#b83222; white-space:pre-wrap; }
  .preview-wrap { display:none; margin-top:14px; border:1px solid var(--line); border-radius:8px; overflow:hidden; background:#dfe4eb; }
  .preview-wrap.active { display:block; }
  iframe.report-preview { display:block; width:100%; height:820px; border:0; background:#eef1f5; }
  .actions { display:none; margin-top:16px; justify-content:flex-end; gap:12px; }
  .actions.active { display:flex; }
  footer { margin:18px 0 0; color:var(--muted); font-size:13px; }
  @media (max-width: 980px) { form.panel { grid-template-columns:1fr; } header { align-items:flex-start; flex-direction:column; } iframe.report-preview { height:680px; } }
</style>
</head>
<body>
<div class="page">
  <header>
    <div>
      <h1>申万一级行业深度报告 Agent</h1>
      <div class="subtitle">按研报链条生成正式 Word。每张图对应一段机制分析，完成后展示同版式预览并提供浏览器下载。</div>
    </div>
    <div class="badge">__APP_VERSION__</div>
  </header>

  <form id="jobForm" class="panel">
    <div>
      <label for="industry">申万一级行业</label>
      <select id="industry">__OPTIONS__</select>
    </div>
    <div>
      <label for="reportDate">报告日期</label>
      <input id="reportDate" placeholder="例如 2026-06-30，可留空取库内最新日" />
    </div>
    <div>
      <label for="years">回看年限</label>
      <input id="years" type="number" min="1" max="10" value="5" />
    </div>
    <div>
      <label for="useAi">AI段落</label>
      <select id="useAi">
        <option value="1" selected>开启</option>
        <option value="0">关闭</option>
      </select>
    </div>
    <button id="submitBtn" type="submit">生成报告</button>
  </form>

  <section class="panel result">
    <div id="meta" class="meta"><span>状态：等待生成</span></div>
    <div id="message" class="message">选择行业和日期后生成报告。AI段落默认开启，正文按结论，证据，机制推导生成。</div>
    <div id="previewWrap" class="preview-wrap"></div>
    <div id="actions" class="actions"></div>
  </section>

  <footer>公网部署使用 Basic Auth。所有密钥仅从服务器环境变量读取。</footer>
</div>
<script>
const form = document.getElementById('jobForm');
const submitBtn = document.getElementById('submitBtn');
const meta = document.getElementById('meta');
const message = document.getElementById('message');
const previewWrap = document.getElementById('previewWrap');
const actions = document.getElementById('actions');
let timer = null;

function esc(s) {
  return String(s ?? '').replace(/[&<>"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[ch]));
}

function clearPreview() {
  previewWrap.classList.remove('active');
  previewWrap.innerHTML = '';
  actions.classList.remove('active');
  actions.innerHTML = '';
}

function renderJob(job) {
  const status = job.status || 'unknown';
  meta.innerHTML = `<span>状态：<strong>${esc(status)}</strong></span>` + (job.job_id ? `<span>任务：${esc(job.job_id)}</span>` : '');
  message.className = status === 'error' ? 'message error' : 'message';
  message.textContent = job.message || '';
  clearPreview();
  if (status === 'done' && job.preview && job.docx) {
    const previewUrl = '/preview?path=' + encodeURIComponent(job.preview);
    const downloadUrl = '/download?path=' + encodeURIComponent(job.docx);
    previewWrap.innerHTML = `<iframe class="report-preview" title="报告预览" src="${previewUrl}"></iframe>`;
    previewWrap.classList.add('active');
    actions.innerHTML = `<a class="download-btn" href="${downloadUrl}" download>下载 Word 报告</a>`;
    actions.classList.add('active');
  }
  if (status === 'done' || status === 'error') {
    submitBtn.disabled = false;
    submitBtn.textContent = '生成报告';
    if (timer) window.clearInterval(timer);
  }
}

async function poll(jobId) {
  const resp = await fetch('/api/jobs/' + encodeURIComponent(jobId));
  const data = await resp.json();
  renderJob(data);
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  clearPreview();
  submitBtn.disabled = true;
  submitBtn.textContent = '生成中';
  const payload = {
    industry: document.getElementById('industry').value,
    report_date: document.getElementById('reportDate').value.trim() || null,
    years: Number(document.getElementById('years').value || 5),
    use_ai: document.getElementById('useAi').value === '1'
  };
  const resp = await fetch('/api/jobs', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  const data = await resp.json();
  renderJob(data);
  if (!resp.ok) return;
  if (timer) window.clearInterval(timer);
  timer = window.setInterval(() => poll(data.job_id).catch(err => {
    message.className = 'message error';
    message.textContent = String(err);
  }), 2500);
});
</script>
</body>
</html>
"""


def index_html() -> str:
    options = "".join(f'<option value="{h(x)}">{h(x)}</option>' for x in SW_LEVEL1 if x != "综合")
    return INDEX_HTML.replace("__OPTIONS__", options).replace("__APP_VERSION__", APP_VERSION)


@app.get("/")
def index() -> str:
    return index_html()


@app.get("/healthz")
def healthz() -> Response:
    return jsonify({"ok": True, "version": APP_VERSION, "project_root": str(PROJECT_ROOT)})


@app.get("/api/state")
def state() -> Response:
    return jsonify({
        "version": APP_VERSION,
        "project_root": str(PROJECT_ROOT),
        "model_dir": str(MODEL_DIR),
        "db_path": os.environ.get("INDUSTRY_REPORT_DB", ""),
        "jobs": list(JOBS.values())[-20:],
        "auth": _auth_configured(),
    })




# === INDUSTRY REPORT V4 WEB PREVIEW START ===
def build_preview_html(payload_path: Path, output_dir: Path) -> Path:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    industry = payload.get("industry") or "行业"
    as_of = payload.get("as_of") or ""
    start_date = payload.get("start_date") or ""
    figures = payload.get("figures") or {}
    sections = payload.get("sections") or {}

    def date_label(value: Any) -> str:
        s = str(value or "").replace("-", "")
        if len(s) >= 8:
            return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
        return str(value or "")

    def img(key: str) -> str:
        raw = figures.get(key)
        if not raw:
            return ""
        path = Path(raw)
        if not path.exists():
            return ""
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return f'<figure class="report-figure"><img src="data:image/png;base64,{data}" alt="{h(key)}" /></figure>'

    def clean_body(text: Any) -> str:
        value = str(text or "").strip()
        repl = {
            "数据来源": "",
            "数据源": "",
            "图表结论：": "",
            "图表结论": "",
            "本地数据库": "",
            "质量门": "",
            "API": "",
            "AI": "",
            "提示词": "",
            "行业定义": "",
            "、": "，",
            "“": "",
            "”": "",
            "?": "",
            "？": "。",
        }
        for old, new in repl.items():
            value = value.replace(old, new)
        return value.strip()

    def para(text: Any, strong: bool = False) -> str:
        value = clean_body(text)
        if not value:
            return ""
        cls = "analysis strong" if strong else "analysis"
        return "".join(f'<p class="{cls}">{h(line.strip())}</p>' for line in value.splitlines() if line.strip())

    def block(title: str, pairs: list[tuple[str, str]]) -> str:
        body = [f'<div class="section-title">{h(title)}</div>']
        for key, comment in pairs:
            body.append(img(key))
            body.append(para(comment))
        return "\n".join(body)

    pages = []
    pages.append(
        '<section class="report-page cover">'
        f'<h1>{h(industry)}行业深度研究报告</h1>'
        f'<div class="subtitle">报告日期：{h(date_label(as_of))}　回看区间：{h(date_label(start_date))} 至 {h(date_label(as_of))}</div>'
        '<div class="section-title">一 投资结论</div>'
        + para(sections.get("investment_conclusion", ""), True)
        + img("thesis_map")
        + para(sections.get("thesis_map", ""))
        + '</section>'
    )
    pages.append('<section class="report-page">' + block("二 周期位置与市场定价", [
        ("relative_return", sections.get("relative_return", "")),
        ("drawdown", sections.get("drawdown", "")),
        ("valuation_band", sections.get("valuation_band", "")),
    ]) + '</section>')
    pages.append('<section class="report-page">' + block("三 需求边际与外部环境", [
        ("demand_financial", sections.get("demand_financial", "")),
        ("macro_cycle", sections.get("macro_cycle", "")),
    ]) + '</section>')
    pages.append('<section class="report-page">' + block("四 供给约束与资本周期", [
        ("supply_capital", sections.get("supply_capital", "")),
        ("dupont", sections.get("dupont", "")),
    ]) + '</section>')
    pages.append('<section class="report-page">' + block("五 价格机制与利润弹性", [
        ("profit_pool", sections.get("profit_pool", "")),
        ("margin_roe", sections.get("margin_roe", "")),
    ]) + '</section>')
    pages.append('<section class="report-page">' + block("六 竞争格局与财务分化", [
        ("financial_dispersion", sections.get("financial_dispersion", "")),
        ("moneyflow_crowding", sections.get("moneyflow_crowding", "")),
    ]) + '</section>')
    pages.append('<section class="report-page">' + block("七 跟踪指标与配置结论", [
        ("tracking_table", sections.get("tracking_table", "")),
    ]) + para(sections.get("final_view", ""), True) + '</section>')

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{h(industry)}行业深度研究报告预览</title>
<style>
  :root {{ --ink:#000; --muted:#555; --line:#d6dbe3; --accent:#c71e37; --paper:#fff; --bg:#eef1f5; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink); font-family:Arial, KaiTi, STKaiti, SimKai, serif; }}
  .preview-shell {{ padding:26px 0 42px; }}
  .report-page {{ width:794px; min-height:1123px; margin:0 auto 20px; padding:60px 62px 58px; background:var(--paper); box-shadow:0 2px 18px rgba(20,28,38,.16); overflow:hidden; }}
  h1 {{ margin:0 0 14px; text-align:center; font-size:25px; line-height:1.35; font-weight:700; letter-spacing:0; }}
  .subtitle {{ text-align:center; color:var(--muted); font-size:13px; margin-bottom:24px; }}
  .section-title {{ margin:14px 0 10px; padding-left:10px; border-left:4px solid var(--accent); font-size:17px; line-height:1.45; font-weight:700; }}
  .analysis {{ margin:8px 0 12px; font-size:13.5px; line-height:1.62; text-indent:2em; text-align:justify; }}
  .analysis.strong {{ font-weight:700; }}
  .report-figure {{ margin:10px 0 8px; page-break-inside:avoid; }}
  .report-figure img {{ display:block; width:100%; max-height:392px; object-fit:contain; background:#fff; }}
  .cover .report-figure img {{ max-height:420px; }}
  @media (max-width:860px) {{ .preview-shell {{ padding:10px; }} .report-page {{ width:100%; min-height:auto; padding:34px 24px; }} }}
</style>
</head>
<body><main class="preview-shell">{''.join(pages)}</main></body></html>"""
    out = output_dir / "report_preview.html"
    out.write_text(html, encoding="utf-8")
    return out
# === INDUSTRY REPORT V4 WEB PREVIEW END ===

@app.post("/api/jobs")
def create_job() -> Response:
    data = request.get_json(force=True, silent=True) or {}
    industry = str(data.get("industry") or "").strip()
    if industry not in SW_LEVEL1 or industry == "综合":
        return jsonify({"status": "error", "message": "请选择有效的申万一级行业"}), 400
    report_date = data.get("report_date") or None
    years = int(data.get("years") or 5)
    use_ai = normalize_bool(data.get("use_ai"), default=True)
    job_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
    job = {
        "job_id": job_id,
        "status": "queued",
        "message": "任务已提交",
        "industry": industry,
        "report_date": report_date,
        "years": years,
        "use_ai": use_ai,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    with JOBS_LOCK:
        JOBS[job_id] = job
    thread = threading.Thread(target=run_job, args=(job_id,), daemon=True)
    thread.start()
    return jsonify(job)


def run_job(job_id: str) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        job["status"] = "running"
        job["message"] = "正在计算指标、绘制图表并生成研报段落"
    try:
        agent_kwargs = {"use_ai": bool(job.get("use_ai", True))}
        db_path = os.environ.get("INDUSTRY_REPORT_DB")
        if db_path:
            agent_kwargs["db_path"] = db_path
        agent = IndustryDepthReportAgent(**agent_kwargs)
        result = agent.generate(
            industry=job["industry"],
            as_of=job.get("report_date"),
            lookback_years=int(job.get("years") or 5),
            job_id=job_id,
        )
        preview_path = build_preview_html(result.payload_path, result.output_dir)
        with JOBS_LOCK:
            job.update({
                "status": "done",
                "message": "正式报告已生成，预览与Word采用同一章节链条",
                "docx": str(result.docx_path),
                "preview": str(preview_path),
                "payload": str(result.payload_path),
                "output_dir": str(result.output_dir),
                "figures": {Path(v).stem: str(v) for v in result.figures},
                "completed_at": datetime.now().isoformat(timespec="seconds"),
            })
    except Exception as exc:  # noqa: BLE001
        with JOBS_LOCK:
            job.update({
                "status": "error",
                "message": f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=8)}",
                "completed_at": datetime.now().isoformat(timespec="seconds"),
            })


@app.get("/api/jobs/<job_id>")
def get_job(job_id: str) -> Response:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"status": "error", "message": "任务不存在"}), 404
        return jsonify(job)


@app.get("/preview")
def preview() -> Response:
    raw_path = request.args.get("path") or ""
    try:
        path = secure_path(raw_path)
        return send_file(path, mimetype="text/html; charset=utf-8", as_attachment=False)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"status": "error", "message": str(exc)}), 400


@app.get("/download")
def download() -> Response:
    raw_path = request.args.get("path") or ""
    try:
        path = secure_path(raw_path)
        return send_file(path, as_attachment=True, download_name=path.name)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"status": "error", "message": str(exc)}), 400


if __name__ == "__main__":
    host = os.environ.get("INDUSTRY_REPORT_HOST", "127.0.0.1")
    port = int(os.environ.get("INDUSTRY_REPORT_PORT", "8892"))
    app.run(host=host, port=port, threaded=True)
