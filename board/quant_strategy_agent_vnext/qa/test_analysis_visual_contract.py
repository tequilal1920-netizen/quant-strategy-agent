from __future__ import annotations

from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]


def test_analysis_component_is_direct_chinese_and_table_free() -> None:
    script = (APP_DIR / "static" / "js" / "research_five_panel.js").read_text(
        encoding="utf-8"
    )
    template = (
        APP_DIR / "templates" / "index_rotation_factor_lab.html"
    ).read_text(encoding="utf-8")

    assert "research_five_panel.js" in template
    assert "research_analysis.js" not in template
    assert "research_evidence_dense.js" not in template
    assert "research_evidence.js" not in template
    assert "<table" not in script
    assert "<p>" not in script
    assert "模型与数据证据层" not in script
    assert "高密度研究工作台" not in script
    assert "公开PDF方法来源" not in script
    assert "模型对象" not in script
    assert "当前值" not in script
    assert "版本" not in script
    assert "模型参数" in script
    assert "原理与传导" in script
    assert "数据与截面" in script
    assert "历史与实时" in script
    assert "模型与预测" in script
    assert "策略与归因" in script
    assert "研究原页隐藏" in script
    assert 'type: "sankey"' in script
    assert "中文值" in script
    assert "中文化可见文本" in script
    login = (APP_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    assert "<footer>版本" not in login
    app_script = (APP_DIR / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "poTableHTML('模型状态'" not in app_script
    assert "poHeading('模型状态')" not in app_script
    assert "<span>模型引擎</span>" not in app_script
    assert "poHeading('重点分析')" in app_script


def test_analysis_component_keeps_global_plot_view() -> None:
    script = (APP_DIR / "static" / "js" / "research_five_panel.js").read_text(
        encoding="utf-8"
    )
    css = (APP_DIR / "static" / "css" / "research_five_panel.css").read_text(
        encoding="utf-8"
    )

    assert "rangeslider: { visible: false }" in script
    assert "dragmode: false" in script
    assert "scrollZoom: false" in script
    assert "displayModeBar: false" in script
    assert "responsive: true" in script
    assert "overflow: auto" not in css
    assert "overflow-x: auto" not in css
    assert "min-width: 760px" not in css
    assert "统一参数区" in css
    assert "五图综合画布" in css
    assert "条件矩阵" in css
    assert "微趋势" in css
    assert "研究原页隐藏" in css


def test_analysis_component_prevents_shell_moves_and_visual_overlap_sources() -> None:
    script = (APP_DIR / "static" / "js" / "research_five_panel.js").read_text(
        encoding="utf-8"
    )
    css = (APP_DIR / "static" / "css" / "research_five_panel.css").read_text(
        encoding="utf-8"
    )

    assert "禁止壳层" in script
    assert ".fl2-shell,.fl2-section,.fl2-toolbar,.fl2-param-group" in script
    assert "控件数 !== 1 || 非字段 || 含业务" in script
    assert "const 字段列 = []" in script
    assert "压缩横轴" in script
    assert 'tickmode = "array"' in script
    assert 'slice(0, 3)' in script
    assert "container-type: inline-size" in css
    assert "font-family: Arial, KaiTi, STKaiti, sans-serif" in css
    assert "max-height: 64px" in css
    assert "box-shadow: none" in css
    assert "background: var(--研蓝)" not in css
    assert "background: #2f75b5" not in css
    assert "background-color: #2f75b5" not in css


def test_navigation_titles_and_order_are_unchanged() -> None:
    template = (
        APP_DIR / "templates" / "index_rotation_factor_lab.html"
    ).read_text(encoding="utf-8")
    titles = [
        "主页",
        "数据看板",
        "宏观",
        "全球市场",
        "行业",
        "大宗商品",
        "个股",
        "新闻事件",
        "AI监控",
        "资产配置",
        "周期跟踪",
        "配置策略",
        "资金面跟踪",
        "散户",
        "公募",
        "私募",
        "外资",
        "ETF",
        "一级市场",
        "融资资金",
        "行业景气度",
        "风格轮动",
        "因子实验室",
        "因子看板",
        "因子挖掘",
        "技术分析",
        "K线学习",
        "组合优化",
        "优化求解",
    ]
    positions = [template.index(title) for title in titles]
    assert positions == sorted(positions)
