"""Graph-first visual adapter for v6.4 daily-excess governed asset allocation."""

from __future__ import annotations

import math
from html import escape
from typing import Any, Mapping, Sequence

from asset_allocation_visual_v61 import COLORS, MODEL_ORDER, _metric, _nav_trace, _num, _trace
from asset_allocation_visual_v63 import build as build_v63

ASSET_CN = {"equity": "股票", "bond": "债券", "gold": "黄金", "commodity": "商品"}
STAGE_CN = {
    "recovery": "复苏期",
    "overheat": "过热期",
    "stagflation": "滞胀期",
    "recession": "衰退期",
    "I_credit_repair": "阶段I\n复苏期",
    "II_profit_expansion": "阶段II\n繁荣期",
    "III_prosperity": "阶段III\n过热期",
    "IV_credit_pressure": "阶段IV\n滞涨期",
    "V_profit_downturn": "阶段V\n衰退前期",
    "V_stagflation_profit_downturn": "阶段V\n衰退前期",
    "VI_recession_repair": "阶段VI\n衰退后期",
}
MERRILL_STAGE_ORDER = ["recovery", "overheat", "stagflation", "recession"]
PRING_STAGE_ORDER = [
    "I_credit_repair",
    "II_profit_expansion",
    "III_prosperity",
    "IV_credit_pressure",
    "V_profit_downturn",
    "VI_recession_repair",
]


def _assets(data: Mapping[str, Any]) -> list[str]:
    order = [str(x) for x in data.get("asset_order") or []]
    return order if order == ["equity", "bond", "gold", "commodity"] else ["equity", "bond", "gold", "commodity"]


def _asset_names(data: Mapping[str, Any]) -> list[str]:
    labels = data.get("asset_labels") or {}
    out = []
    for asset in _assets(data):
        value = str(labels.get(asset) or ASSET_CN[asset])
        out.append(value if "�" not in value else ASSET_CN[asset])
    return out


def _weights(data: Mapping[str, Any], model: Mapping[str, Any]) -> list[float]:
    current = model.get("current_weights") or {}
    return [_num(current.get(asset)) for asset in _assets(data)]


def _history(data: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return list((data.get("cycle_tracking") or {}).get("history") or [])


def _html_chart(html: str, *, height: int = 540) -> dict[str, Any]:
    return {"title": "", "height": height, "html": html}


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _month_label(value: Any) -> str:
    text = str(value or "")
    if len(text) == 6 and text.isdigit():
        return f"{text[:4]}-{text[4:]}"
    return text


def _smooth(values: Sequence[Any], window: int = 5) -> list[float | None]:
    numeric = [float(v) if _finite(v) else None for v in values]
    out: list[float | None] = []
    for idx, val in enumerate(numeric):
        if val is None:
            out.append(None)
            continue
        left = max(0, idx - window + 1)
        bucket = [x for x in numeric[left : idx + 1] if x is not None]
        out.append(sum(bucket) / len(bucket) if bucket else None)
    return out


def _direction_from_row(row: Mapping[str, Any], axis_key: str, continuous_key: str) -> int:
    keys = (
        f"{axis_key}_direction",
        f"{axis_key}_signal",
        f"{axis_key}_direction_signal",
        continuous_key.replace("continuous", "direction"),
        continuous_key.replace("score", "direction"),
        "direction",
        "signal",
    )
    for key in keys:
        if key in row and _finite(row.get(key)):
            return 1 if float(row.get(key)) >= 0 else -1
    value = row.get(continuous_key)
    return 1 if (_finite(value) and float(value) >= 0) else -1


def _asset_mapping_html(data: Mapping[str, Any]) -> dict[str, Any]:
    meta = data.get("asset_metadata") or data.get("asset_mapping") or {}
    commodity = meta.get("commodity") if isinstance(meta, Mapping) else None
    commodity_text = "非黄金商品期货自融资篮子（A/AL/C/CF/CU/J/L/M/P/RB/RU/SR/TA/V/Y/ZN；剔除AU/AG）"
    if isinstance(commodity, Mapping):
        roots = commodity.get("roots") or commodity.get("underlyings")
        if roots:
            commodity_text = "非黄金商品期货自融资篮子（" + "/".join(map(str, roots)) + "；剔除AU/AG）"
    rows = [
        ("股票", "沪深300全收益 / 510300.SH 执行映射"),
        ("债券", "国债收益指数口径 / 511260.SH 执行映射"),
        ("黄金", "上海金 Au99.99 / 518880.SH 执行映射"),
        ("商品", commodity_text),
    ]
    body = "".join(f"<tr><td>{escape(asset)}</td><td>{escape(desc)}</td></tr>" for asset, desc in rows)
    html = f"""
    <div class="资产配置资产表" style="font-family:KaiTi,SimKai,STKaiti,Arial,sans-serif;color:#111;padding:18px 26px;">
      <table style="border-collapse:collapse;width:100%;font-size:23px;line-height:1.9;">
        <thead><tr>
          <th style="text-align:left;border-top:3px solid #7f1d1d;border-bottom:3px solid #7f1d1d;padding:8px 20px;width:24%;">资产类别</th>
          <th style="text-align:left;border-top:3px solid #7f1d1d;border-bottom:3px solid #7f1d1d;padding:8px 20px;">代表资产 / 可交易映射</th>
        </tr></thead>
        <tbody>{body}</tbody>
      </table>
      <div style="border-top:3px solid #7f1d1d;margin-top:4px;padding-top:10px;font-size:18px;">资料来源：Wind / RQData / 本地冻结面板；商品为非黄金期货篮子，非单一ETF替代。</div>
    </div>
    """
    return _html_chart(html, height=360)


def _nav_rows(series: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    for key in ("daily_nav", "nav_daily", "daily"):
        rows = series.get(key)
        if isinstance(rows, list) and rows:
            return [row for row in rows if isinstance(row, Mapping)]
    rows = series.get("nav")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, Mapping)]
    return []


def _row_x(row: Mapping[str, Any]) -> str:
    return str(row.get("date") or _month_label(row.get("month")))


def _row_nav(row: Mapping[str, Any]) -> float | None:
    value = row.get("nav")
    if value is None:
        value = row.get("level")
    return _num(value)


def _aligned_benchmark(model_rows: Sequence[Mapping[str, Any]], benchmark_rows: Sequence[Mapping[str, Any]]) -> list[float | None]:
    lookup = {_row_x(row): _row_nav(row) for row in benchmark_rows}
    aligned = [lookup.get(_row_x(row)) for row in model_rows]
    if any(value is not None for value in aligned):
        return aligned
    raw = [_row_nav(row) for row in benchmark_rows]
    return raw[-len(model_rows) :] if model_rows else []


def _is_daily_rows(rows: Sequence[Mapping[str, Any]]) -> bool:
    return bool(rows and rows[0].get("date"))


def _merrill_clock_html() -> dict[str, Any]:
    html = r"""
    <div class="资产配置画布 资产配置美林矩形图" style="position:relative;height:640px;background:#fff;font-family:KaiTi,SimKai,STKaiti,Arial,sans-serif;color:#111;overflow:hidden;">
      <style>
        .资产配置美林矩形图 .美林外框{position:absolute;left:17.2%;top:10.8%;width:65.6%;height:73.5%;border:14px solid #c00000;box-sizing:border-box;background:#fff;}
        .资产配置美林矩形图 .美林轴{position:absolute;font-size:30px;letter-spacing:5px;white-space:nowrap;color:#111;font-weight:500;}
        .资产配置美林矩形图 .美林象限{position:absolute;width:25.8%;height:23.8%;background:#eeeeee;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;box-sizing:border-box;}
        .资产配置美林矩形图 .美林象限 h3{font-size:34px;margin:0 0 22px 0;font-weight:700;letter-spacing:5px;line-height:1.05;}
        .资产配置美林矩形图 .美林象限 p{font-size:22px;margin:0 0 22px 0;letter-spacing:3px;line-height:1.35;}
        .资产配置美林矩形图 .美林象限 b{font-size:27px;line-height:1.45;font-weight:500;letter-spacing:4px;}
        .资产配置美林矩形图 .美林十字横{position:absolute;left:28%;top:49.2%;width:44%;height:1px;background:transparent;}
        .资产配置美林矩形图 .美林十字竖{position:absolute;left:50%;top:23%;width:1px;height:40%;background:transparent;}
      </style>
      <div class="美林外框"></div>
      <div class="美林轴" style="left:50%;top:6%;transform:translateX(-50%);">通胀上行</div>
      <div class="美林轴" style="left:50%;bottom:1.5%;transform:translateX(-50%);">通胀下行</div>
      <div class="美林轴" style="left:4.8%;top:47.2%;">经济上行</div>
      <div class="美林轴" style="right:4.8%;top:47.2%;">经济下行</div>
      <div class="美林象限" style="left:24.5%;top:21.4%;"><h3>复苏期</h3><p>增长上行 / 通胀下行</p><b>股票优先<br>商品跟随</b></div>
      <div class="美林象限" style="left:51.2%;top:21.4%;"><h3>过热期</h3><p>增长上行 / 通胀上行</p><b>商品优先<br>股票跟随</b></div>
      <div class="美林象限" style="left:24.5%;top:58.0%;"><h3>衰退期</h3><p>增长下行 / 通胀下行</p><b>债券优先<br>黄金跟随</b></div>
      <div class="美林象限" style="left:51.2%;top:58.0%;"><h3>滞胀期</h3><p>增长下行 / 通胀上行</p><b>黄金优先<br>商品跟随</b></div>
    </div>
    """
    return _html_chart(html, height=660)

def _pringer_html() -> dict[str, Any]:
    phases = [
        ("阶段 I<br>复苏期", "宽货币 ↑<br>宽信用 ↑<br>增长下行 ↓", "货币底"),
        ("阶段 II<br>繁荣期", "宽货币 ↑<br>宽信用 ↑<br>增长上行 ↑", "信用底"),
        ("阶段 III<br>过热期", "紧货币 ↓<br>宽信用 ↑<br>增长上行 ↑", "货币顶"),
        ("阶段 IV<br>滞涨期", "紧货币 ↓<br>紧信用 ↓<br>增长上行 ↑", "信用顶"),
        ("阶段 V<br>衰退前期", "紧货币 ↓<br>紧信用 ↓<br>增长下行 ↓", "经济顶"),
        ("阶段 VI<br>衰退后期", "宽货币 ↑<br>紧信用 ↓<br>增长下行 ↓", "经济底"),
    ]
    cols = []
    for idx, (title, body, foot) in enumerate(phases):
        cols.append(
            f'<div class="阶段列" style="--亮度:{0.46 - idx * 0.035:.3f}"><h3>{title}</h3><p>{body}</p><div class="阶段底">{foot}</div></div>'
        )
    style = """
      <style>
        .资产配置普林格图{height:500px;background:#fff;font-family:KaiTi,SimKai,STKaiti,Arial,sans-serif;color:#111;display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:0;padding:26px 28px;box-sizing:border-box;overflow:hidden;}
        .资产配置普林格图 .阶段列{min-width:0;display:flex;flex-direction:column;align-items:center;justify-content:space-between;text-align:center;padding:24px 10px 18px;box-sizing:border-box;background:rgba(192,0,0,var(--亮度));border-left:2px solid #fff;color:#fff;}
        .资产配置普林格图 .阶段列:first-child{border-left:0;}
        .资产配置普林格图 .阶段列 h3{margin:0;font-size:27px;line-height:1.28;font-weight:500;letter-spacing:1px;}
        .资产配置普林格图 .阶段列 p{margin:18px 0 14px;font-size:19px;line-height:1.65;font-weight:400;letter-spacing:0;}
        .资产配置普林格图 .阶段底{font-size:20px;line-height:1.35;border-top:1px solid rgba(255,255,255,.7);padding-top:12px;width:100%;}
        @media (max-width:900px){.资产配置普林格图{grid-template-columns:repeat(3,minmax(0,1fr));height:auto;}.资产配置普林格图 .阶段列{min-height:230px;}.资产配置普林格图 .阶段列 h3{font-size:23px}.资产配置普林格图 .阶段列 p{font-size:17px}}
      </style>
    """
    return _html_chart('<div class="资产配置画布 资产配置普林格图">' + style + ''.join(cols) + '</div>', height=520)


def _axis_factor_chart(data: Mapping[str, Any], axis_key: str, title: str, continuous_key: str) -> dict[str, Any]:
    rows = _history(data)
    x = [_month_label(row.get("month")) for row in rows]
    cont = [_num(row.get(continuous_key)) for row in rows]
    direction = [_direction_from_row(row, axis_key, continuous_key) for row in rows]
    smooth = _smooth(cont, window=5)
    return {
        "title": title,
        "height": 360,
        "y_range": [-1.15, 1.15],
        "y_tickvals": [-1, 0, 1],
        "y_ticktext": ["下行", "0", "上行"],
        "y2_title": "连续平滑指标",
        "traces": [
            {"name": "方向背景（+1/-1）", "x": x, "y": direction, "type": "bar", "color": "#f4b183", "opacity": 0.58, "show_text": False},
            {"name": title.split("：", 1)[0], "x": x, "y": smooth, "type": "scatter", "mode": "lines+markers", "axis": "y2", "color": "#c00000", "line_shape": "spline", "line_width": 2.8, "marker_size": 4, "connectgaps": True},
        ],
        "legend": {"orientation": "h", "y": -0.22, "x": 0.30},
    }


def _stage_step_chart(data: Mapping[str, Any], key: str, title: str, order: Sequence[str]) -> dict[str, Any]:
    rows = _history(data)
    x = [_month_label(row.get("month")) for row in rows]
    index = {name: i + 1 for i, name in enumerate(order)}
    y = [index.get(str(row.get(key)), None) for row in rows]
    labels = [STAGE_CN.get(name, name).replace("\n", " ") for name in order]
    return {
        "title": title,
        "height": 380,
        "y_range": [0.5, len(order) + 0.5],
        "y_tickvals": list(range(1, len(order) + 1)),
        "y_ticktext": labels,
        "traces": [{"name": "周期划分", "x": x, "y": y, "type": "scatter", "mode": "lines+markers", "color": "#c00000", "line_shape": "hv", "line_width": 2.0, "marker_size": 5}],
    }


def _relative_strength_chart(model_key: str, model: Mapping[str, Any], benchmark: Mapping[str, Any]) -> dict[str, Any]:
    rows = _nav_rows(model)
    bench_rows = _nav_rows(benchmark)
    x = [_row_x(row) for row in rows]
    nav = [_row_nav(row) for row in rows]
    bench = _aligned_benchmark(rows, bench_rows)
    rel = [n / b if (n is not None and b not in (None, 0)) else None for n, b in zip(nav, bench)]
    name = str(model.get("name") or model_key)
    freq = "日度" if _is_daily_rows(rows) else "月度"
    return {
        "title": f"{name}：{freq}净值与相对强度（右轴）",
        "height": 420,
        "y2_title": "相对强度",
        "traces": [
            {"name": "四资产等权", "x": x, "y": bench, "type": "scatter", "mode": "lines", "color": "#ffc000", "line_width": 2.2},
            {"name": name, "x": x, "y": nav, "type": "scatter", "mode": "lines", "color": "#bfbfbf", "line_width": 2.2},
            {"name": "相对强度（右轴）", "x": x, "y": rel, "type": "scatter", "mode": "lines", "axis": "y2", "color": "#c00000", "line_width": 2.8},
        ],
        "legend": {"orientation": "h", "y": -0.20, "x": 0.22},
    }


def _recent_excess_chart(data: Mapping[str, Any]) -> dict[str, Any]:
    rows = list(((data.get("recommended") or {}).get("recent_relative_diagnostics") or []))
    years: list[str] = []
    for row in rows:
        year = str(row.get("year") or "")
        if year and year not in years:
            years.append(year)
    models = data.get("allocation_models") or {}
    traces: list[dict[str, Any]] = []
    for key in MODEL_ORDER:
        lookup = {
            str(row.get("year")): _num(row.get("annual_excess_return")) * 100.0
            for row in rows
            if row.get("model") == key
        }
        model = models.get(key) or {}
        traces.append(
            {
                "name": str(model.get("name") or key),
                "x": years,
                "y": [lookup.get(year) for year in years],
                "type": "bar",
                "color": COLORS.get(key),
                "show_text": True,
            }
        )
    return {
        "title": "近三年相对四资产等权年化超额（报告期诊断，不参与选模）",
        "height": 390,
        "barmode": "group",
        "y_title": "年化超额（%）",
        "traces": traces,
        "legend": {"orientation": "h", "y": -0.22, "x": 0.18},
    }


def _formula_html(kind: str) -> dict[str, Any]:
    common_style = """
      <style>
        .资产配置公式页{padding:16px 28px 20px 28px;box-sizing:border-box;background:#fff;}
        .资产配置公式页 h3{font-size:25px;margin:4px 0 14px;font-weight:700;letter-spacing:1px;}
        .资产配置公式页 ol{font-size:16px;line-height:1.48;margin:0 0 0 24px;padding:0;}
        .资产配置公式页 li{margin:7px 0;}
        .资产配置公式页 .资产配置公式{display:block;font-family:'Cambria Math','Times New Roman',Arial,serif;font-size:20px;text-align:center;border:1px solid #d7d7d7;border-radius:0;background:#fff;margin:6px 0 7px;padding:8px 10px;letter-spacing:0;line-height:1.45;}
        .资产配置公式页 .资产配置公式小{font-size:17px;}
        .资产配置公式页 .资产配置小注{font-size:14px;color:#555;margin-top:8px;line-height:1.55;}
        .资产配置公式页 .红{color:#c00000;font-weight:700;}
      </style>
    """
    if kind == "bl":
        html = f"""
        <div class="资产配置画布 资产配置公式页" style="font-family:KaiTi,SimKai,STKaiti,Arial,sans-serif;color:#111;">{common_style}<h3>Black-Litterman：周期观点联动权重求解</h3><ol>
        <li><b>资产收益与协方差：</b><span class="资产配置公式">R<sub>t</sub>=(R<sub>E,t</sub>,R<sub>B,t</sub>,R<sub>G,t</sub>,R<sub>C,t</sub>)′，&nbsp;Σ<sub>t</sub>=Shrink[Cov(R<sub>t-L:t</sub>)]</span>股票、债券、黄金、非黄金商品统一进入同一月度/日度风险口径。</li>
        <li><b>均衡先验：</b><span class="资产配置公式">π<sub>t</sub>=δ<sub>t</sub>Σ<sub>t</sub>w<sub>b</sub>，&nbsp;w<sub>b</sub>=(25%,25%,25%,25%)′</span>等权基准只作为先验锚和超额比较，不作为事后调参工具。</li>
        <li><b>周期排序转主观观点：</b><span class="资产配置公式">z<sub>t</sub>=ω<sub>M</sub>·Rank<sub>Merrill,t</sub>+ω<sub>P</sub>·Rank<sub>Pring,t</sub>，&nbsp;P<sub>t</sub>E[R<sub>t+1</sub>]=Q<sub>t</sub>+ε<sub>t</sub></span>美林四阶段与普林格六阶段先给出四资产排序，再映射为相对观点矩阵。</li>
        <li><b>观点收益与置信度：</b><span class="资产配置公式">Q<sub>t</sub>=κ<sub>t</sub>P<sub>t</sub>z<sub>t</sub>，&nbsp;ε<sub>t</sub>~N(0,Ω<sub>t</sub>)，&nbsp;Ω<sub>t</sub>=diag(σ²<sub>view,t</sub>)/(conf<sub>t</sub>+ε)</span><span class="资产配置公式 资产配置公式小">conf<sub>t</sub>=g(因子覆盖率, PIT可用率, 训练期命中率, 验证期稳定性, 两周期一致性)</span></li>
        <li><b>贝叶斯后验：</b><span class="资产配置公式">μ<sub>BL,t</sub>=[(τΣ<sub>t</sub>)<sup>-1</sup>+P′<sub>t</sub>Ω<sup>-1</sup><sub>t</sub>P<sub>t</sub>]<sup>-1</sup>[(τΣ<sub>t</sub>)<sup>-1</sup>π<sub>t</sub>+P′<sub>t</sub>Ω<sup>-1</sup><sub>t</sub>Q<sub>t</sub>]</span><span class="资产配置公式 资产配置公式小">U<sub>BL,t</sub>=[(τΣ<sub>t</sub>)<sup>-1</sup>+P′<sub>t</sub>Ω<sup>-1</sup><sub>t</sub>P<sub>t</sub>]<sup>-1</sup></span></li>
        <li><b>含成本优化：</b><span class="资产配置公式">max<sub>w</sub>&nbsp; μ′<sub>BL,t</sub>w − γ/2·w′Σ<sub>t</sub>w − η√(w′U<sub>BL,t</sub>w) − c′|w-w<sup>-</sup>| − 1/2(w-w<sup>-</sup>)′K(w-w<sup>-</sup>)</span><span class="资产配置公式 资产配置公式小">s.t.&nbsp;1′w=1，L≤w≤U，Turnover≤cap，TE≤cap，KKT≤10<sup>-7</sup></span></li>
        </ol><div class="资产配置小注"><span class="红">治理边界：</span>P/Q/Ω/参数网格只允许训练与验证信息进入；报告期只展示，不允许回头优化。</div></div>
        """
    elif kind == "rp":
        html = f"""
        <div class="资产配置画布 资产配置公式页" style="font-family:KaiTi,SimKai,STKaiti,Arial,sans-serif;color:#111;">{common_style}<h3>风险平价：风险贡献均衡</h3><ol>
        <li><b>稳健协方差：</b><span class="资产配置公式">Σ<sub>t</sub>=Shrink[EWMA(R<sub>t-36:t</sub>)]，&nbsp;λ<sub>min</sub>(Σ<sub>t</sub>)≥0，&nbsp;Cond(Σ<sub>t</sub>)≤阈值</span></li>
        <li><b>组合波动率：</b><span class="资产配置公式">σ<sub>p,t</sub>=√(w′Σ<sub>t</sub>w)</span></li>
        <li><b>边际风险贡献：</b><span class="资产配置公式">MRC<sub>i,t</sub>=∂σ<sub>p,t</sub>/∂w<sub>i</sub>=(Σ<sub>t</sub>w)<sub>i</sub>/σ<sub>p,t</sub></span></li>
        <li><b>总风险贡献：</b><span class="资产配置公式">RC<sub>i,t</sub>=w<sub>i</sub>·MRC<sub>i,t</sub>，&nbsp;Σ<sub>i=1</sub><sup>4</sup>RC<sub>i,t</sub>=σ<sub>p,t</sub></span></li>
        <li><b>等风险约束：</b><span class="资产配置公式">RC<sub>1,t</sub>=RC<sub>2,t</sub>=RC<sub>3,t</sub>=RC<sub>4,t</sub>=σ<sub>p,t</sub>/4</span></li>
        <li><b>风险预算扩展：</b><span class="资产配置公式">min<sub>w</sub>Σ<sub>i</sub>(RC<sub>i,t</sub>/σ<sub>p,t</sub>-β<sub>i,t</sub>)<sup>2</sup>+λ<sub>c</sub>|w-w<sup>-</sup>|</span><span class="资产配置公式 资产配置公式小">β<sub>t</sub>=Proj(β<sub>0</sub>+A·Cycle<sub>t</sub>+B·Macro<sub>t</sub>)，&nbsp;Σβ<sub>i,t</sub>=1，β<sub>i,t</sub>≥0</span></li>
        </ol><div class="资产配置小注">页面同时保留纯ERC权重、预算目标、实际风险贡献和最终交易权重，避免把软锚误写成最终风险平价。</div></div>
        """
    else:
        html = f"""
        <div class="资产配置画布 资产配置公式页" style="font-family:KaiTi,SimKai,STKaiti,Arial,sans-serif;color:#111;">{common_style}<h3>宏观因子调整：六维因子筛选与组合调控</h3><ol>
        <li><b>六维因子池：</b>增长、通胀、利率、信用、汇率、流动性；每个小因子保存 provider、query hash、release/available time、vintage/revision。</li>
        <li><b>变换与去噪：</b><span class="资产配置公式">x<sub>j,t</sub>=RobustZ(Δ<sub>m</sub>F<sub>j,t</sub>)，&nbsp;ĉ<sub>j,t</sub>=HP(x<sub>j,t</sub>)+FFT<sub>band</sub>(x<sub>j,t</sub>)</span>方向背景只取 sign(ĉ)，连续线保留真实平滑指标。</li>
        <li><b>单因子检验：</b><span class="资产配置公式">r<sub>i,t+1</sub>=α<sub>i,j</sub>+β<sub>i,j</sub>x<sub>j,t</sub>+ε<sub>i,t+1</sub>，&nbsp;IC<sub>j</sub>=Corr(x<sub>j,t</sub>,r<sub>i,t+1</sub>)</span><span class="资产配置公式 资产配置公式小">Score<sub>j</sub>=0.35|t(β)|+0.25|IC|+0.20IR+0.10WinRate−0.10TurnoverPenalty</span></li>
        <li><b>大类聚合：</b><span class="资产配置公式">S<sub>k,t</sub>=Σ<sub>j∈k</sub>ω<sub>j</sub>x<sub>j,t</sub>，&nbsp;ω<sub>j</sub>=Score<sub>j</sub>/Σ<sub>j∈k</sub>Score<sub>j</sub></span></li>
        <li><b>收益映射：</b><span class="资产配置公式">E[R<sub>t+1</sub>|S<sub>t</sub>]=α+B·S<sub>t</sub>，&nbsp;μ<sub>macro,t</sub>=μ<sub>base,t</sub>+B·S<sub>t</sub></span></li>
        <li><b>权重调控：</b><span class="资产配置公式">β<sub>macro,t</sub>=Proj(β<sub>0</sub>+A·S<sub>t</sub>)，&nbsp;w<sub>t</sub>=Opt(μ<sub>macro,t</sub>,Σ<sub>t</sub>,β<sub>macro,t</sub>,Cost)</span></li>
        </ol><div class="资产配置小注"><span class="红">真实性边界：</span>D3/PIT 未闭环的小因子只做研究展示；生产权重只接收通过可得时点与修订谱系校验的因子。</div></div>
        """
    return _html_chart(html, height=760)

def _strategy_rows(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    models = data.get("allocation_models") or {}
    for key in MODEL_ORDER:
        model = models.get(key) or {}
        metrics = model.get("metrics") or {}
        row = {
            "model": model.get("name") or key,
            "annual_return": _metric(model, "full", "annual_return"),
            "annual_excess": _metric(model, "full", "annual_excess_return"),
            "sharpe": _metric(model, "full", "sharpe"),
            "ir": _metric(model, "full", "information_ratio"),
            "max_drawdown": _metric(model, "full", "max_drawdown"),
            "governance": model.get("governance") or "research-only",
        }
        rows.append(row)
    return rows


def _table(columns: Sequence[tuple[str, str, str]], rows: Sequence[Mapping[str, Any]], *, limit_rows: int | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"columns": [{"key": k, "label": l, "format": f} for k, l, f in columns], "rows": [dict(r) for r in rows]}
    if limit_rows is not None:
        out["limit_rows"] = limit_rows
    return out


def build(data: dict[str, Any], metrics: list[dict[str, Any]] | None = None, page: str = "strategy") -> dict[str, Any]:
    visuals = build_v63(data, metrics=metrics, page=page)
    cycle = data.get("cycle_tracking") or {}
    models = data.get("allocation_models") or {}
    benchmark = (data.get("benchmarks") or {}).get("equal_weight_4_assets") or {}
    assets = _assets(data)
    asset_names = _asset_names(data)

    note = (
        "v6.4口径：美林=增长/通胀两轴，普林格=货币/信用/增长三轴；"
        "四资产等权25%为基准；BL、风险平价、宏观因子三模型独立展示；D3/PIT仍保持研究服务边界。"
    )

    factor_rows = list(cycle.get("factor_rows") or [])
    descriptive = visuals.get("descriptive") or {}
    descriptive.update({
        "title": "周期理论框架：美林时钟 + 普林格周期",
        "display": "charts_only",
        "note": note,
        "table": _table([
            ("cycle", "模型", "text"), ("pillar", "因子大类", "text"), ("factor", "因子", "text"),
            ("processing", "处理方法", "text"), ("enters_current_weight", "是否入权重", "status"),
        ], factor_rows),
        "chart": _merrill_clock_html(),
        "secondary_charts": [_asset_mapping_html(data), _pringer_html(), _stage_step_chart(data, "merrill_stage", "美林时钟历史阶段总图", MERRILL_STAGE_ORDER), _stage_step_chart(data, "pring_stage", "普林格六阶段历史阶段总图", PRING_STAGE_ORDER)],
    })

    history = visuals.get("history") or {}
    history.update({
        "title": "连续因子检验与周期划分",
        "display": "charts_only",
        "note": note,
        "chart": _axis_factor_chart(data, "growth", "增长因子：连续指标 + ±1方向背景", "merrill_growth"),
        "secondary_charts": [
            _axis_factor_chart(data, "inflation", "通胀因子：连续指标 + ±1方向背景", "merrill_inflation"),
            _axis_factor_chart(data, "money", "货币因子：连续指标 + ±1方向背景", "pring_money"),
            _axis_factor_chart(data, "credit", "信用因子：连续指标 + ±1方向背景", "pring_credit"),
            _axis_factor_chart(data, "pring_growth", "普林格增长因子：连续指标 + ±1方向背景", "pring_growth"),
        ],
    })

    diagnostics = visuals.get("diagnostics") or {}
    diagnostics.update({
        "title": "资产配置模型原理：BL / 风险平价 / 宏观因子",
        "display": "charts_only",
        "note": note,
        "table": _table([
            ("model", "模型", "text"), ("annual_return", "年化收益", "percent"), ("annual_excess", "超额收益", "percent"),
            ("sharpe", "Sharpe", "number"), ("ir", "IR", "number"), ("max_drawdown", "最大回撤", "percent"),
        ], _strategy_rows(data)),
        "chart": _formula_html("bl"),
        "secondary_charts": [_formula_html("rp"), _formula_html("macro")],
    })

    nav_charts = [_relative_strength_chart(key, models.get(key) or {}, benchmark) for key in MODEL_ORDER]
    recommended_key = str((data.get("recommended") or {}).get("primary_model") or "macro_factor")
    recommended = models.get(recommended_key) or {}
    strategy = visuals.get("strategy") or {}
    strategy.update({
        "title": "策略收益：三模型 vs 四资产等权基准",
        "display": "charts_only",
        "note": note + "；策略图优先读取 daily_nav；当前快照未提供 daily_nav 时按真实月度快照展示，禁止伪造日度。",
        "table": _table([
            ("model", "模型", "text"), ("annual_return", "年化收益", "percent"), ("annual_excess", "超额收益", "percent"),
            ("sharpe", "Sharpe", "number"), ("ir", "IR", "number"), ("max_drawdown", "最大回撤", "percent"),
        ], _strategy_rows(data)),
        "chart": nav_charts[0],
        "secondary_charts": nav_charts[1:] + [
            _recent_excess_chart(data),
            {"title": "当前四资产权重", "height": 420, "barmode": "group", "traces": [_trace(str((models.get(k) or {}).get("name") or k), asset_names, _weights(data, models.get(k) or {}), color=COLORS.get(k), kind="bar") for k in MODEL_ORDER] + [_trace("四资产等权25%", asset_names, [0.25, 0.25, 0.25, 0.25], color="#ffc000", kind="bar")]},
            {"title": "当前两周期综合资产排序", "height": 420, "traces": [_trace("综合得分", asset_names, [_num((cycle.get("combined_scores") or {}).get(a)) for a in assets], color="#c00000", kind="bar")]},
        ],
    })

    strategy["title"] = f"最终推荐：{recommended.get('name') or recommended_key}（训练/验证门禁决定，报告期只展示）"
    return visuals
