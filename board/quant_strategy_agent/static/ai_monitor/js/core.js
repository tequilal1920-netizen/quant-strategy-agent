(() => {
  "use strict";

  const config = window.AI_MONITOR_CONFIG || { basePath: "", version: "dev" };
  let basePath = (config.basePath || "").replace(/\/$/, "");
  let domRoot = document;
  const colors = {
    red: "#c00000", yellow: "#ffc000", blue: "#2f75b5", gray: "#808080",
    green: "#00b050", orange: "#ed7d31", purple: "#7030a0", lightBlue: "#5b9bd5",
    ink: "#1f2933", muted: "#667085", faint: "#98a2b3", border: "#d9e1e8", grid: "#e7ebef",
  };
  const palette = [colors.red, colors.yellow, colors.blue, colors.gray, colors.orange, colors.purple, colors.green, colors.lightBlue];
  const state = {
    snapshot: null,
    selectedLevel1: null,
    selectedIndustry: "__L1__",
    groupPayload: null,
    level1Payloads: new Map(),
    selectedStock: null,
    window: "all",
    smooth: "5",
    metric: "diffusion_score_smooth5",
    reliableOnly: true,
    stockQuery: "",
  };
  const cache = new Map();
  const $ = (selector) => domRoot.querySelector(selector);
  const $$ = (selector) => Array.from(domRoot.querySelectorAll(selector));
  const finite = (value) => Number.isFinite(Number(value));

  function esc(value) {
    return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  }

  function endpoint(path) { return `${basePath}${path}`; }
  async function fetchJSON(path) {
    if (cache.has(path)) return cache.get(path);
    const response = await fetch(endpoint(path), { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    cache.set(path, payload);
    return payload;
  }

  function showToast(message) {
    const toast = $("#toast");
    toast.textContent = message;
    toast.classList.add("is-visible");
    clearTimeout(showToast.timer);
    showToast.timer = setTimeout(() => toast.classList.remove("is-visible"), 2800);
  }

  function fmt(value, digits = 1) { return finite(value) ? Number(value).toFixed(digits) : "-"; }
  function fmtSigned(value, digits = 1, suffix = "") {
    if (!finite(value)) return "-";
    const number = Number(value);
    return `${number > 0 ? "+" : ""}${number.toFixed(digits)}${suffix}`;
  }
  function fmtRatio(value, digits = 0) { return finite(value) ? `${(Number(value) * 100).toFixed(digits)}%` : "-"; }
  function toneClass(value) { return !finite(value) || Number(value) === 0 ? "num-neutral" : Number(value) > 0 ? "num-pos" : "num-neg"; }
  function shapeLabel(shape) {
    return ({ leader: "趋势龙头", broad_follower: "扩散跟随", isolated_contributor: "孤立贡献", isolated_leader: "缩圈龙头", drag: "拖累", watch: "观察" })[shape] || "观察";
  }
  function regimeLabel(regime) {
    return ({ expansion: "扩散加速", broad: "偏扩散", balanced: "均衡", narrow: "偏缩圈", contraction: "缩圈", low_sample: "低样本" })[regime] || "均衡";
  }
  function metricInfo(key) {
    return (state.snapshot?.metric_catalog || []).find((item) => item.key === key) || { key, label: key, unit: "score" };
  }
  function formatUnit(value, unit) {
    if (!finite(value)) return "-";
    if (unit === "%") return fmtRatio(value, 1);
    if (unit === "%raw") return `${fmt(value, 2)}%`;
    if (unit === "count") return fmt(value, 0);
    if (unit === "count1") return fmt(value, 1);
    return fmt(value, 1);
  }
  function unitValue(value, unit) {
    if (!finite(value)) return null;
    return unit === "%" ? Number(value) * 100 : Number(value);
  }

  function plotLayout(extra = {}) {
    return {
      margin: { l: 52, r: 20, t: 28, b: 42 },
      paper_bgcolor: "#ffffff", plot_bgcolor: "#ffffff",
      font: { family: 'Arial, KaiTi, "楷体", sans-serif', size: 11, color: colors.muted },
      hoverlabel: { bgcolor: colors.ink, bordercolor: colors.ink, font: { family: 'Arial, KaiTi, "楷体", sans-serif', color: "#fff", size: 11 } },
      xaxis: { gridcolor: colors.grid, zeroline: false, tickfont: { size: 11 }, automargin: true },
      yaxis: { gridcolor: colors.grid, zerolinecolor: colors.border, tickfont: { size: 11 }, automargin: true },
      legend: { orientation: "h", y: 1.08, x: 0, font: { size: 11 } },
      showlegend: true,
      ...extra,
    };
  }
  const plotConfig = { displayModeBar: false, responsive: true, scrollZoom: false };
  function drawPlot(target, traces, layout = {}) {
    const element = typeof target === "string" ? $(target) : target;
    if (!element || !window.Plotly) return;
    Plotly.react(element, traces, plotLayout(layout), plotConfig);
  }

  function windowed(rows) {
    if (!Array.isArray(rows) || state.window === "all") return rows || [];
    return rows.slice(-Number(state.window));
  }
  function ema(values, span) {
    const alpha = 2 / (span + 1);
    let previous = null;
    return values.map((value) => {
      if (!finite(value)) return null;
      previous = previous == null ? Number(value) : alpha * Number(value) + (1 - alpha) * previous;
      return previous;
    });
  }
  function smoothed(values) {
    const clean = values.map((value) => finite(value) ? Number(value) : null);
    return state.smooth === "raw" ? clean : ema(clean, Number(state.smooth));
  }
  function latest(rows) { return rows?.length ? rows[rows.length - 1] : {}; }
  function industryNode(level1 = state.selectedLevel1) {
    return (state.snapshot?.industry_tree || []).find((item) => item.name === level1) || { name: level1, children: [], stats: {} };
  }
  function flatIndustries(level1 = state.selectedLevel1) { return industryNode(level1).children.flatMap((item) => item.children || []); }
  function selectedLatest() {
    if (state.selectedIndustry === "__L1__") return (state.snapshot.level1_latest || []).find((row) => row.industry_name === state.selectedLevel1) || {};
    return (state.snapshot.industry_latest || []).find((row) => row.industry_code === state.selectedIndustry) || {};
  }

  function populateControls() {
    const level1 = state.snapshot.industry_tree || [];
    if (!state.selectedLevel1) state.selectedLevel1 = level1[0]?.name || "电子";
    $("#level1-select").innerHTML = level1.map((item) => `<option value="${esc(item.name)}">${esc(item.name)}</option>`).join("");
    $("#level1-select").value = state.selectedLevel1;
    populateIndustrySelect();
    $("#metric-select").innerHTML = (state.snapshot.metric_catalog || []).map((item) => `<option value="${esc(item.key)}">${esc(item.label)}</option>`).join("");
    if (!(state.snapshot.metric_catalog || []).some((item) => item.key === state.metric)) state.metric = "diffusion_score_smooth5";
    $("#metric-select").value = state.metric;
  }
  function populateIndustrySelect() {
    const items = flatIndustries();
    const options = [`<option value="__L1__">${esc(state.selectedLevel1)} · 一级汇总</option>`];
    items.forEach((item) => options.push(`<option value="${esc(item.code)}">${esc(item.name)}${item.sample_grade === "C" ? " · C级" : ""}</option>`));
    $("#industry-select").innerHTML = options.join("");
    if (state.selectedIndustry !== "__L1__" && !items.some((item) => item.code === state.selectedIndustry)) state.selectedIndustry = "__L1__";
    $("#industry-select").value = state.selectedIndustry;
  }

  async function loadLevel1Series() {
    const names = (state.snapshot.industry_tree || []).map((item) => item.name);
    const payloads = await Promise.all(names.map((name) => fetchJSON(`/api/level1/${encodeURIComponent(name)}`)));
    names.forEach((name, index) => state.level1Payloads.set(name, payloads[index]));
  }

  function scoreColor(value) {
    if (!finite(value)) return colors.faint;
    if (Number(value) >= 55) return colors.red;
    if (Number(value) <= 45) return colors.green;
    return colors.gray;
  }

  function renderMarketCharts() {
    const rows = windowed(state.snapshot.market_series || []);
    const x = rows.map((row) => row.trade_date);
    const values = smoothed(rows.map((row) => row.avg_diffusion_score));
    const lastValue = [...values].reverse().find(finite);
    $("#market-last").textContent = finite(lastValue) ? `${fmt(lastValue, 1)} 分` : "-";
    drawPlot("#market-chart", [
      { x, y: values, type: "scatter", mode: "lines", name: state.smooth === "raw" ? "综合扩散" : `${state.smooth}日平滑`, line: { color: colors.red, width: 2.2 } },
      { x, y: rows.map((row) => row.avg_diffusion_score_smooth20), type: "scatter", mode: "lines", name: "20日基准", line: { color: colors.blue, width: 1.5 } },
      { x, y: rows.map((row) => finite(row.tech_positive_ratio) ? Number(row.tech_positive_ratio) * 100 : null), type: "scatter", mode: "lines", name: "当日上涨家数", line: { color: colors.gray, width: 1 }, yaxis: "y2", opacity: .75 },
    ], {
      yaxis: { range: [0, 100], title: "扩散分", gridcolor: colors.grid },
      yaxis2: { range: [0, 100], title: "%", overlaying: "y", side: "right", showgrid: false },
      shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 50, y1: 50, line: { color: colors.border, width: 1, dash: "dot" } }],
    });

    const traces = [];
    Array.from(state.level1Payloads.entries()).forEach(([name, payload], index) => {
      const data = windowed(payload.series || []);
      traces.push({ x: data.map((row) => row.trade_date), y: smoothed(data.map((row) => row.diffusion_score)), type: "scatter", mode: "lines", name, line: { color: palette[index % palette.length], width: 1.8 } });
    });
    drawPlot("#level1-chart", traces, { yaxis: { range: [0, 100], title: "扩散分", gridcolor: colors.grid } });
    renderRankings();
  }

  function rankTrace(rows) {
    const sorted = [...rows].filter((row) => finite(row.diffusion_score_smooth5)).sort((a, b) => Number(a.diffusion_score_smooth5) - Number(b.diffusion_score_smooth5));
    return [{
      x: sorted.map((row) => row.diffusion_score_smooth5), y: sorted.map((row) => row.industry_name),
      type: "bar", orientation: "h", marker: { color: sorted.map((row) => scoreColor(row.diffusion_score_smooth5)) },
      text: sorted.map((row) => fmt(row.diffusion_score_smooth5, 1)), textposition: "auto",
      customdata: sorted.map((row) => [row.industry_code, row.diffusion_score_chg_20d, row.sample_grade]),
      hovertemplate: "%{y}<br>扩散 %{x:.1f}<br>20日 %{customdata[1]:+.1f}<br>样本 %{customdata[2]}<extra></extra>",
    }];
  }
  function renderRankings() {
    const all = (state.snapshot.industry_latest || []).filter((row) => !state.reliableOnly || row.is_reliable);
    drawPlot("#all-rank-chart", rankTrace(all), {
      showlegend: false, margin: { l: 116, r: 16, t: 10, b: 36 },
      xaxis: { range: [0, 100], gridcolor: colors.grid }, yaxis: { automargin: true, gridcolor: "#fff", tickfont: { size: 11 } },
    });
    const selectedCodes = new Set(flatIndustries().map((item) => item.code));
    const selected = all.filter((row) => selectedCodes.has(row.industry_code));
    $("#level1-rank-title").textContent = `${state.selectedLevel1} · 三级行业排名`;
    drawPlot("#level1-rank-chart", rankTrace(selected), {
      showlegend: false, margin: { l: 112, r: 16, t: 10, b: 36 },
      xaxis: { range: [0, 100], gridcolor: colors.grid }, yaxis: { automargin: true, gridcolor: "#fff", tickfont: { size: 11 } },
    });
  }

  function renderIndustryMap() {
    const nodes = state.snapshot.industry_tree || [];
    $("#level1-tabs").innerHTML = nodes.map((node) => `<button type="button" class="${node.name === state.selectedLevel1 ? "is-active" : ""}" data-level1="${esc(node.name)}">${esc(node.name)}</button>`).join("");
    $("#level1-tabs").querySelectorAll("button").forEach((button) => button.addEventListener("click", async () => {
      state.selectedLevel1 = button.dataset.level1;
      state.selectedIndustry = "__L1__";
      $("#level1-select").value = state.selectedLevel1;
      populateIndustrySelect();
      renderIndustryMap(); renderRankings();
      await loadSelectedGroup();
    }));
    const node = industryNode();
    const flat = flatIndustries().filter((item) => !state.reliableOnly || item.sample_grade !== "C");
    $("#map-summary").innerHTML = `<span class="summary-badge">${flat.length} 个三级行业</span><span class="summary-badge" data-tone="red">${flat.filter((item) => Number(item.score) >= 55).length} 个偏扩散</span>`;
    $("#industry-board").innerHTML = node.children.map((level2) => {
      const items = (level2.children || []).filter((item) => !state.reliableOnly || item.sample_grade !== "C");
      return `<article class="level2-group"><header><strong>${esc(level2.name)}</strong><span>${items.length}</span></header><div class="industry-card-grid">${items.map(industryCard).join("")}</div></article>`;
    }).join("");
    $("#industry-board").querySelectorAll(".industry-card[data-code]").forEach((card) => card.addEventListener("click", async () => {
      state.selectedIndustry = card.dataset.code;
      $("#industry-select").value = state.selectedIndustry;
      renderIndustryMap();
      await loadSelectedGroup();
      $("#industry-series").scrollIntoView({ behavior: "smooth", block: "start" });
    }));
  }
  function industryCard(item) {
    const dims = [item.return_breadth, item.contribution_diffusion, item.expectation, item.research, item.funding];
    const dimColors = [colors.red, colors.yellow, colors.blue, colors.gray, colors.orange];
    const chips = (item.stock_chips || []).slice(0, 2).map((stock) => `<span class="stock-chip"><b>${esc(stock.stock_name)}</b><span class="${toneClass(stock.contribution_pp)}">${fmtSigned(stock.contribution_pp, 2)}</span></span>`).join("");
    return `<button type="button" class="industry-card ${item.code === state.selectedIndustry ? "is-selected" : ""}" data-code="${esc(item.code)}">
      <div class="industry-card-head"><strong>${esc(item.name)}</strong><span class="industry-score">${fmt(item.score, 1)}</span></div>
      <div class="industry-card-meta"><span>20日 ${fmtSigned(item.change20, 1)}</span><span>${esc(regimeLabel(item.regime))}</span><span>${item.valid}/${item.members}</span></div>
      <div class="mini-dimensions">${dims.map((value, index) => `<i title="${fmt(value, 1)}" style="--value:${Math.max(0, Math.min(100, Number(value) || 0))}%;--series:${dimColors[index]}"></i>`).join("")}</div>
      <div class="stock-chips">${chips}</div></button>`;
  }

  function dimensionCell(label, value, note, tone) {
    return `<div class="dimension-cell" style="--tone:${tone}"><span>${esc(label)}</span><strong>${fmt(value, 1)}</strong><small>${esc(note || "")}</small></div>`;
  }
  function renderIndustryHeader(row) {
    const name = state.selectedIndustry === "__L1__" ? state.selectedLevel1 : row.industry_name;
    $("#industry-title").textContent = `${name} · 综合扩散指数`;
    $("#industry-badges").innerHTML = `<span class="summary-badge" data-tone="red">${esc(regimeLabel(row.regime))}</span><span class="summary-badge">样本 ${fmt(row.valid_count, 0)}/${fmt(row.member_count, 0)}</span><span class="summary-badge">覆盖 ${fmt((Number(row.dimension_coverage) || 0) * 100, 0)}%</span>`;
    $("#dimension-strip").innerHTML = [
      dimensionCell("综合扩散", row.diffusion_score_smooth5, `20日 ${fmtSigned(row.diffusion_score_chg_20d, 1)}`, colors.red),
      dimensionCell("收益广度", row.return_breadth_score, "权重 25%", colors.yellow),
      dimensionCell("贡献扩散", row.contribution_diffusion_score, "权重 20%", colors.blue),
      dimensionCell("预期修正", row.expectation_revision_score, `上调 ${fmtRatio(row.expectation_up_ratio, 0)}`, colors.gray),
      dimensionCell("调研热度", row.research_heat_score, `60日 ${fmt(row.research_events_60d, 0)} 次`, colors.orange),
      dimensionCell("资金扩散", row.funding_crowding_score, `覆盖 ${fmtRatio(row.flow_coverage, 0)}`, colors.purple),
    ].join("");
  }

  function renderIndustryCharts() {
    const rows = windowed(state.groupPayload?.series || []);
    const x = rows.map((row) => row.trade_date);
    const scoreKey = state.smooth === "raw" ? "diffusion_score" : state.smooth === "20" ? "diffusion_score_smooth20" : "diffusion_score_smooth5";
    drawPlot("#industry-score-chart", [
      { x, y: rows.map((row) => row[scoreKey]), type: "scatter", mode: "lines", name: state.smooth === "raw" ? "原值" : `${state.smooth}日平滑`, line: { color: colors.red, width: 2.2 } },
      { x, y: rows.map((row) => row.diffusion_percentile_252d), type: "scatter", mode: "lines", name: "252日分位", line: { color: colors.blue, width: 1.3 }, yaxis: "y2" },
    ], {
      yaxis: { range: [0, 100], title: "扩散分", gridcolor: colors.grid },
      yaxis2: { range: [0, 100], title: "分位", overlaying: "y", side: "right", showgrid: false },
      shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 50, y1: 50, line: { color: colors.border, dash: "dot", width: 1 } }],
    });
    const metric = metricInfo(state.metric);
    $("#metric-chart-title").textContent = metric.label;
    drawPlot("#industry-metric-chart", [{
      x, y: smoothed(rows.map((row) => unitValue(row[state.metric], metric.unit))), type: "scatter", mode: "lines",
      name: metric.label, line: { color: colors.red, width: 2 }, fill: "tozeroy", fillcolor: "rgba(192,0,0,.07)",
      hovertemplate: `%{x}<br>${esc(metric.label)} %{y:.2f}<extra></extra>`,
    }], { showlegend: false, yaxis: { title: metric.unit.includes("%") ? "%" : metric.unit === "count" || metric.unit === "count1" ? "次数" : "分", gridcolor: colors.grid } });

    drawPlot("#size-area-chart", [
      areaTrace(x, rows, "large_up_share", "大市值", colors.red),
      areaTrace(x, rows, "mid_up_share", "中市值", colors.blue),
      areaTrace(x, rows, "small_up_share", "小市值", colors.green),
    ], { yaxis: { range: [0, 100], ticksuffix: "%", gridcolor: colors.grid }, legend: { orientation: "h", y: 1.08, x: 0 } });
    drawPlot("#driver-area-chart", [
      areaTrace(x, rows, "driver_return_breadth_share", "收益广度", colors.red, false),
      areaTrace(x, rows, "driver_contribution_diffusion_share", "贡献扩散", colors.yellow, false),
      areaTrace(x, rows, "driver_expectation_revision_share", "预期修正", colors.blue, false),
      areaTrace(x, rows, "driver_research_heat_share", "调研热度", colors.gray, false),
      areaTrace(x, rows, "driver_funding_crowding_share", "资金扩散", colors.orange, false),
    ], { yaxis: { range: [0, 100], ticksuffix: "%", gridcolor: colors.grid }, legend: { orientation: "h", y: 1.12, x: 0 } });
  }
  function areaTrace(x, rows, key, name, color, ratio = true) {
    return { x, y: rows.map((row) => finite(row[key]) ? Number(row[key]) * (ratio ? 100 : 1) : null), type: "scatter", mode: "lines", name, stackgroup: "one", groupnorm: "percent", line: { color, width: .8 }, hovertemplate: `%{x}<br>${name} %{y:.1f}%<extra></extra>` };
  }

  function attributionFields() {
    const level1 = state.selectedIndustry === "__L1__";
    return level1 ? { contribution: "level1_contribution_pp", relative: "level1_relative_strength_score", contributionScore: "level1_contribution_score", stockScore: "level1_stock_score", coverage: "level1_stock_score_coverage" }
      : { contribution: "contribution_pp", relative: "relative_strength_score", contributionScore: "contribution_score", stockScore: "stock_score", coverage: "stock_score_coverage" };
  }
  function filteredStocks() {
    const q = state.stockQuery.trim().toLowerCase();
    return (state.groupPayload?.stocks || []).filter((row) => !q || String(row.stock_name).toLowerCase().includes(q) || String(row.stock_code).toLowerCase().includes(q));
  }
  function renderStockAttribution() {
    const rows = filteredStocks();
    const fields = attributionFields();
    const positive = rows.filter((row) => Number(row[fields.contribution]) > 0).sort((a, b) => Number(b[fields.contribution]) - Number(a[fields.contribution])).slice(0, 12);
    const negative = rows.filter((row) => Number(row[fields.contribution]) < 0).sort((a, b) => Number(a[fields.contribution]) - Number(b[fields.contribution])).slice(0, 12);
    const bars = [...negative.reverse(), ...positive.reverse()];
    drawPlot("#contribution-chart", [{
      x: bars.map((row) => row[fields.contribution]), y: bars.map((row) => row.stock_name), type: "bar", orientation: "h",
      marker: { color: bars.map((row) => Number(row[fields.contribution]) >= 0 ? colors.red : colors.green) },
      text: bars.map((row) => fmtSigned(row[fields.contribution], 3)), textposition: "auto",
      customdata: bars.map((row) => row.stock_code), hovertemplate: "%{y} %{customdata}<br>贡献 %{x:.3f}pp<extra></extra>",
    }], { showlegend: false, margin: { l: 86, r: 14, t: 10, b: 38 }, xaxis: { title: "百分点", zeroline: true, zerolinecolor: colors.ink, gridcolor: colors.grid }, yaxis: { gridcolor: "#fff", automargin: true } });

    const scatterRows = rows.filter((row) => finite(row[fields.relative]) && finite(row[fields.stockScore]));
    const maxContribution = Math.max(...scatterRows.map((row) => Math.abs(Number(row[fields.contribution]) || 0)), .001);
    drawPlot("#stock-map-chart", [{
      x: scatterRows.map((row) => row[fields.relative]), y: scatterRows.map((row) => row[fields.stockScore]),
      text: scatterRows.map((row) => row.stock_name), customdata: scatterRows.map((row) => [row.stock_code, row[fields.contribution], row.shape_tag]),
      type: "scatter", mode: "markers+text", textposition: "top center",
      textfont: { size: 9, color: colors.muted },
      marker: { size: scatterRows.map((row) => 7 + 24 * Math.sqrt(Math.abs(Number(row[fields.contribution]) || 0) / maxContribution)), color: scatterRows.map((row) => Number(row[fields.contribution]) >= 0 ? colors.red : colors.green), opacity: .68, line: { color: "#fff", width: 1 } },
      hovertemplate: "%{text} %{customdata[0]}<br>相对强弱 %{x:.1f}<br>综合 %{y:.1f}<br>贡献 %{customdata[1]:+.3f}pp<extra></extra>",
    }], { showlegend: false, xaxis: { range: [0, 100], title: "相对强弱", gridcolor: colors.grid }, yaxis: { range: [0, 100], title: "综合得分", gridcolor: colors.grid }, shapes: [
      { type: "line", x0: 50, x1: 50, y0: 0, y1: 100, line: { color: colors.border, dash: "dot" } },
      { type: "line", x0: 0, x1: 100, y0: 50, y1: 50, line: { color: colors.border, dash: "dot" } },
    ] });
    const tableRows = [...rows].sort((a, b) => Number(b[fields.stockScore] || -1) - Number(a[fields.stockScore] || -1));
    $("#stock-table-body").innerHTML = tableRows.map((row) => `<tr data-code="${esc(row.stock_code)}">
      <td class="stock-name-cell"><strong>${esc(row.stock_name)}</strong><small>${esc(row.stock_code)}</small></td>
      <td><span class="shape-badge" data-shape="${esc(row.shape_tag)}">${esc(shapeLabel(row.shape_tag))}</span></td>
      <td class="${toneClass(row.pct_change)}">${fmtSigned(row.pct_change, 2, "%")}</td><td class="${toneClass(row.ret_20d)}">${fmtSigned(row.ret_20d, 1, "%")}</td>
      <td class="${toneClass(row[fields.contribution])}">${fmtSigned(row[fields.contribution], 3)}</td><td>${fmt(row.trend_score, 1)}</td><td>${fmt(row[fields.relative], 1)}</td>
      <td>${fmt(row.consensus_revision_score, 1)}</td><td>${fmt(row.research_score, 1)}</td><td>${fmt(row.funding_score, 1)}</td><td><strong>${fmt(row[fields.stockScore], 1)}</strong></td>
    </tr>`).join("");
    $("#stock-table-body").querySelectorAll("tr[data-code]").forEach((row) => row.addEventListener("click", () => loadStock(row.dataset.code)));
  }

  async function loadStock(code) {
    try {
      state.selectedStock = await fetchJSON(`/api/stock/${encodeURIComponent(code)}`);
      renderStockDetail();
      $("#stock-detail").hidden = false;
      $("#stock-detail").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) { showToast(`个股数据读取失败：${error.message}`); }
  }
  function renderStockDetail() {
    const payload = state.selectedStock;
    if (!payload) return;
    const meta = payload.meta || {};
    const fields = attributionFields();
    const rows = windowed(payload.series || []);
    const x = rows.map((row) => row.trade_date);
    $("#stock-title").textContent = `${meta.stock_name || payload.stock_code} · ${payload.stock_code}`;
    $("#stock-badges").innerHTML = `<span class="summary-badge" data-tone="red">${esc(shapeLabel(meta.shape_tag))}</span><span class="summary-badge" data-tone="blue">${esc(meta.industry_name || "-")}</span><span class="summary-badge" data-tone="${Number(meta.pct_change) >= 0 ? "red" : "green"}">${fmtSigned(meta.pct_change, 2, "%")}</span>`;
    $("#stock-score-strip").innerHTML = [
      dimensionCell("综合", meta[fields.stockScore], `覆盖 ${fmt((Number(meta[fields.coverage]) || 0) * 100, 0)}%`, colors.red),
      dimensionCell("趋势", meta.trend_score, `20日 ${fmtSigned(meta.ret_20d, 1, "%")}`, colors.yellow),
      dimensionCell("相对强弱", meta[fields.relative], "横截面分位", colors.blue),
      dimensionCell("预期修正", meta.consensus_revision_score, `方向 ${fmtSigned(meta.consensus_revision_direction, 2)}`, colors.gray),
      dimensionCell("调研热度", meta.research_score, `60日 ${fmt(meta.research_events_60d, 0)} 次`, colors.orange),
      dimensionCell("资金扩散", meta.funding_score, `净流入率 ${fmtSigned(meta.net_inflow_rate, 2, "%")}`, colors.purple),
    ].join("");
    drawPlot("#stock-price-chart", [
      { x, y: rows.map((row) => row.return_index), type: "scatter", mode: "lines", name: "复权净值", line: { color: colors.ink, width: 2 } },
      { x, y: rows.map((row) => row.ma20), type: "scatter", mode: "lines", name: "MA20", line: { color: colors.red, width: 1.3 } },
      { x, y: rows.map((row) => row.ma60), type: "scatter", mode: "lines", name: "MA60", line: { color: colors.blue, width: 1.3 } },
    ], { yaxis: { title: "基期=100", gridcolor: colors.grid } });
    drawPlot("#stock-score-chart", [
      { x, y: smoothed(rows.map((row) => row.trend_score)), type: "scatter", mode: "lines", name: "趋势", line: { color: colors.red, width: 1.5 } },
      { x, y: smoothed(rows.map((row) => row.consensus_revision_score)), type: "scatter", mode: "lines", name: "预期", line: { color: colors.blue, width: 1.4 } },
      { x, y: smoothed(rows.map((row) => row.research_score)), type: "scatter", mode: "lines", name: "调研", line: { color: colors.gray, width: 1.2 } },
      { x, y: smoothed(rows.map((row) => row.funding_score)), type: "scatter", mode: "lines", name: "资金", line: { color: colors.orange, width: 1.2 } },
      { x, y: smoothed(rows.map((row) => row[fields.stockScore])), type: "scatter", mode: "lines", name: "综合", line: { color: colors.ink, width: 2 } },
    ], { yaxis: { range: [0, 100], gridcolor: colors.grid } });
    drawPlot("#stock-event-chart", [
      { x, y: rows.map((row) => row[fields.contribution]), type: "bar", name: "贡献", marker: { color: rows.map((row) => Number(row[fields.contribution]) >= 0 ? "rgba(192,0,0,.6)" : "rgba(0,176,80,.6)") }, yaxis: "y" },
      { x, y: smoothed(rows.map((row) => row.net_inflow_rate)), type: "scatter", mode: "lines", name: "净流入率", line: { color: colors.blue, width: 1.4 }, yaxis: "y2" },
      { x, y: smoothed(rows.map((row) => row.large_order_inflow_rate)), type: "scatter", mode: "lines", name: "大单净流入率", line: { color: colors.yellow, width: 1.2 }, yaxis: "y2" },
    ], { yaxis: { title: "贡献pp", gridcolor: colors.grid, zeroline: true, zerolinecolor: colors.border }, yaxis2: { title: "%", overlaying: "y", side: "right", showgrid: false }, margin: { l: 52, r: 48, t: 28, b: 40 } });
  }

  async function loadSelectedGroup() {
    try {
      const path = state.selectedIndustry === "__L1__" ? `/api/level1/${encodeURIComponent(state.selectedLevel1)}` : `/api/industry/${encodeURIComponent(state.selectedIndustry)}`;
      state.groupPayload = await fetchJSON(path);
      const row = latest(state.groupPayload.series || []);
      renderIndustryHeader(row); renderIndustryCharts(); renderStockAttribution(); renderIndustryMap(); renderRankings();
    } catch (error) { showToast(`行业数据读取失败：${error.message}`); }
  }

  function bindControls() {
    $("#level1-select").addEventListener("change", async (event) => {
      state.selectedLevel1 = event.target.value; state.selectedIndustry = "__L1__";
      populateIndustrySelect(); renderIndustryMap(); renderRankings(); await loadSelectedGroup();
    });
    $("#industry-select").addEventListener("change", async (event) => { state.selectedIndustry = event.target.value; renderIndustryMap(); await loadSelectedGroup(); });
    $("#metric-select").addEventListener("change", (event) => { state.metric = event.target.value; renderIndustryCharts(); });
    $("#reliable-only").addEventListener("change", (event) => { state.reliableOnly = event.target.checked; renderIndustryMap(); renderRankings(); });
    $("#stock-search").addEventListener("input", (event) => { state.stockQuery = event.target.value; renderStockAttribution(); });
    $$("#window-control button").forEach((button) => button.addEventListener("click", () => {
      state.window = button.dataset.window; $$("#window-control button").forEach((item) => item.classList.toggle("is-active", item === button));
      renderMarketCharts(); renderIndustryCharts(); if (state.selectedStock) renderStockDetail();
    }));
    $$("#smooth-control button").forEach((button) => button.addEventListener("click", () => {
      state.smooth = button.dataset.smooth; $$("#smooth-control button").forEach((item) => item.classList.toggle("is-active", item === button));
      renderMarketCharts(); renderIndustryCharts(); if (state.selectedStock) renderStockDetail();
    }));
    $$(".nav-item[data-scroll]").forEach((button) => button.addEventListener("click", () => {
      $$(".nav-item[data-scroll]").forEach((item) => item.classList.toggle("is-active", item === button));
      $(`#${button.dataset.scroll}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
    }));
  }

  async function init() {
    try {
      state.snapshot = await fetchJSON("/api/snapshot");
      state.selectedLevel1 = state.snapshot.industry_tree?.[0]?.name || "电子";
      $("#latest-date").textContent = state.snapshot.meta?.latest_trade_dt || "-";
      populateControls(); bindControls();
      await loadLevel1Series();
      renderMarketCharts(); renderIndustryMap();
      await loadSelectedGroup();
    } catch (error) {
      showToast(`看板加载失败：${error.message}`);
      throw error;
    }
  }

  async function mount(root, options = {}) {
    domRoot = root;
    if (options.basePath) basePath = String(options.basePath).replace(/\/$/, "");
    state.snapshot = null;
    state.groupPayload = null;
    state.level1Payloads = new Map();
    state.selectedStock = null;
    await init();
    return state;
  }

  function invalidate() {
    cache.clear();
    state.snapshot = null;
    state.groupPayload = null;
    state.level1Payloads = new Map();
    state.selectedStock = null;
  }

  window.AIMonitorCore = { mount, invalidate, state };
})();
