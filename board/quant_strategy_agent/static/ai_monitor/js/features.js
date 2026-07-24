(() => {
  "use strict";

  const config = window.AI_MONITOR_CONFIG || { basePath: "" };
  let basePath = String(config.basePath || "").replace(/\/$/, "");
  let domRoot = document;
  const metricLabels = new Map([
    ["diffusion_score_smooth5", "扩散分数"],
    ["return_breadth_score", "收益广度"],
    ["contribution_diffusion_score", "贡献扩散"],
    ["expectation_revision_score", "预期修正"],
    ["research_heat_score", "调研热度"],
    ["funding_crowding_score", "资金扩散"],
  ]);
  const metricOrder = Array.from(metricLabels.keys());
  const cache = new Map();
  let snapshot = null;
  let overlayPending = false;

  const $ = (selector) => domRoot.querySelector(selector);
  const finite = (value) => Number.isFinite(Number(value));
  const escapeHTML = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  async function fetchJSON(path) {
    if (cache.has(path)) return cache.get(path);
    const response = await fetch(`${basePath}${path}`, { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    cache.set(path, payload);
    return payload;
  }

  function scoreKey() {
    const active = $("#smooth-control button.is-active")?.dataset.smooth || "5";
    if (active === "raw") return "diffusion_score";
    if (active === "20") return "diffusion_score_smooth20";
    return "diffusion_score_smooth5";
  }

  function scoreClass(value) {
    if (!finite(value)) return "score-neutral";
    if (Number(value) >= 55) return "score-strong";
    if (Number(value) <= 45) return "score-weak";
    return "score-neutral";
  }

  function oneDecimal(value) {
    return finite(value) ? Number(value).toFixed(1) : "-";
  }

  function ensureScoreTable() {
    if ($("#industry-dimension-table")) return;
    const grid = $("#overview .overview-grid");
    if (!grid) return;
    const panel = document.createElement("article");
    panel.id = "industry-dimension-table";
    panel.className = "table-panel industry-score-table-panel";
    panel.innerHTML = `
      <header class="panel-header">
        <h3 id="industry-score-table-title">三级行业多维得分</h3>
        <strong id="industry-score-table-count"></strong>
      </header>
      <div class="table-scroll">
        <table class="data-table industry-score-table">
          <thead>
            <tr>
              <th>排名</th>
              <th>三级行业名称</th>
              <th>综合扩散</th>
              <th>收益广度</th>
              <th>贡献扩散</th>
              <th>预期修正</th>
              <th>调研热度</th>
              <th>资金扩散</th>
            </tr>
          </thead>
          <tbody id="industry-score-table-body"></tbody>
        </table>
      </div>`;
    grid.insertAdjacentElement("afterend", panel);
    panel.addEventListener("click", (event) => {
      const row = event.target.closest("tr[data-industry-code]");
      if (!row) return;
      const select = $("#industry-select");
      if (!select || !Array.from(select.options).some((option) => option.value === row.dataset.industryCode)) return;
      select.value = row.dataset.industryCode;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      $("#industry-series")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  function renderScoreTable() {
    if (!snapshot) return;
    ensureScoreTable();
    const level1 = $("#level1-select")?.value;
    if (!level1) return;
    const key = scoreKey();
    const rows = (snapshot.industry_latest || [])
      .filter((row) => row.level1_name === level1)
      .sort((left, right) => {
        const a = finite(left[key]) ? Number(left[key]) : -Infinity;
        const b = finite(right[key]) ? Number(right[key]) : -Infinity;
        return b - a;
      });
    $("#industry-score-table-title").textContent = `${level1} · 三级行业多维得分`;
    $("#industry-score-table-count").textContent = `${rows.length} 个三级行业`;
    $("#industry-score-table-body").innerHTML = rows.map((row, index) => {
      const fields = [
        row[key],
        row.return_breadth_score,
        row.contribution_diffusion_score,
        row.expectation_revision_score,
        row.research_heat_score,
        row.funding_crowding_score,
      ];
      return `<tr data-industry-code="${escapeHTML(row.industry_code)}">
        <td class="rank-cell">${index + 1}</td>
        <td class="industry-name-cell">${escapeHTML(row.industry_name)}</td>
        ${fields.map((value) => `<td class="${scoreClass(value)}">${oneDecimal(value)}</td>`).join("")}
      </tr>`;
    }).join("");
  }

  function syncMetricOptions() {
    const select = $("#metric-select");
    if (!select || !select.options.length) return false;
    const selected = select.value;
    const existing = new Map(Array.from(select.options).map((option) => [option.value, option]));
    const fragment = document.createDocumentFragment();
    metricOrder.forEach((key) => {
      const option = existing.get(key) || new Option(metricLabels.get(key), key);
      option.textContent = metricLabels.get(key);
      fragment.appendChild(option);
      existing.delete(key);
    });
    existing.forEach((option) => fragment.appendChild(option));
    select.replaceChildren(fragment);
    select.value = selected;
    return true;
  }

  function syncMetricTitle() {
    const key = $("#metric-select")?.value;
    if (metricLabels.has(key) && $("#metric-chart-title")) {
      $("#metric-chart-title").textContent = metricLabels.get(key);
    }
  }

  function selectedGroupPath() {
    const industry = $("#industry-select")?.value;
    const level1 = $("#level1-select")?.value;
    if (!level1) return null;
    return industry && industry !== "__L1__"
      ? `/api/industry/${encodeURIComponent(industry)}`
      : `/api/level1/${encodeURIComponent(level1)}`;
  }

  function indexCloseSeries(rows) {
    let close = 100;
    return rows.map((row, index) => {
      if (index > 0 && finite(row.weighted_ret)) close *= 1 + Number(row.weighted_ret) / 100;
      return { x: row.trade_date, y: close };
    });
  }

  function windowedIndex(rows) {
    const all = indexCloseSeries(rows);
    const active = $("#window-control button.is-active")?.dataset.window || "all";
    return active === "all" ? all : all.slice(-Number(active));
  }

  async function addIndexCloseOverlay() {
    const chart = $("#industry-metric-chart");
    const path = selectedGroupPath();
    if (!chart || !path || !window.Plotly || overlayPending) return;
    if ((chart.data || []).some((trace) => trace.meta === "r8-index-close")) return;
    overlayPending = true;
    try {
      const payload = await fetchJSON(path);
      const points = windowedIndex(payload.series || []);
      if (!points.length || (chart.data || []).some((trace) => trace.meta === "r8-index-close")) return;
      await Plotly.addTraces(chart, {
        x: points.map((point) => point.x),
        y: points.map((point) => point.y),
        type: "scatter",
        mode: "lines",
        name: "板块指数收盘净值（2024=100）",
        meta: "r8-index-close",
        yaxis: "y2",
        line: { color: "#2f75b5", width: 1.8 },
        hovertemplate: "%{x}<br>板块指数收盘净值 %{y:.2f}<extra></extra>",
      });
      await Plotly.relayout(chart, {
        showlegend: true,
        "legend.orientation": "h",
        "legend.x": 0,
        "legend.y": 1.10,
        "margin.r": 58,
        "yaxis2.overlaying": "y",
        "yaxis2.side": "right",
        "yaxis2.showgrid": false,
        "yaxis2.zeroline": false,
        "yaxis2.title.text": "指数净值",
        "yaxis2.tickformat": ".1f",
      });
    } catch (error) {
      console.warn("Index close overlay skipped:", error);
    } finally {
      overlayPending = false;
    }
  }

  function bindIndexOverlay() {
    const chart = $("#industry-metric-chart");
    if (!chart || chart.dataset.indexOverlayBound || typeof chart.on !== "function") return false;
    chart.dataset.indexOverlayBound = "1";
    chart.on("plotly_afterplot", () => {
      if (!(chart.data || []).some((trace) => trace.meta === "r8-index-close")) {
        window.setTimeout(addIndexCloseOverlay, 0);
      }
    });
    addIndexCloseOverlay();
    return true;
  }

  function bindControls() {
    $("#level1-select")?.addEventListener("change", () => {
      window.setTimeout(renderScoreTable, 0);
      window.setTimeout(addIndexCloseOverlay, 250);
    });
    $("#industry-select")?.addEventListener("change", () => window.setTimeout(addIndexCloseOverlay, 250));
    $("#metric-select")?.addEventListener("change", () => {
      syncMetricTitle();
      window.setTimeout(addIndexCloseOverlay, 0);
    });
    $("#smooth-control")?.addEventListener("click", (event) => {
      if (!event.target.closest("button")) return;
      window.setTimeout(renderScoreTable, 0);
    });
    $("#level1-tabs")?.addEventListener("click", (event) => {
      if (!event.target.closest("button")) return;
      window.setTimeout(renderScoreTable, 0);
      window.setTimeout(addIndexCloseOverlay, 250);
    });
  }

  async function init() {
    snapshot = await fetchJSON("/api/snapshot");
    ensureScoreTable();
    const optionTimer = window.setInterval(() => {
      if (syncMetricOptions()) {
        window.clearInterval(optionTimer);
        syncMetricTitle();
      }
    }, 100);
    const plotTimer = window.setInterval(() => {
      if (bindIndexOverlay()) window.clearInterval(plotTimer);
    }, 100);
    renderScoreTable();
    bindControls();
  }

  async function mount(root, options = {}) {
    domRoot = root;
    if (options.basePath) basePath = String(options.basePath).replace(/\/$/, "");
    snapshot = null;
    await init();
  }

  function invalidate() { cache.clear(); snapshot = null; }

  window.AIMonitorFeatures = { mount, invalidate };
})();
