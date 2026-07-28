(() => {
  "use strict";

  const config = window.AI_MONITOR_CONFIG || { basePath: "" };
  let basePath = String(config.basePath || "").replace(/\/$/, "");
  let domRoot = document;
  const dimensions = [
    { key: "return_breadth_score", label: "收益广度", weight: 25, color: "#c00000" },
    { key: "contribution_diffusion_score", label: "贡献扩散", weight: 20, color: "#ffc000" },
    { key: "expectation_revision_score", label: "预期修正", weight: 25, color: "#2f75b5" },
    { key: "research_heat_score", label: "调研热度", weight: 15, color: "#808080" },
    { key: "funding_crowding_score", label: "资金扩散", weight: 15, color: "#ed7d31" },
  ];
  const $ = (selector) => domRoot.querySelector(selector);
  const finite = (value) => value !== null && value !== "" && Number.isFinite(Number(value));
  const oneDecimal = (value) => finite(value) ? Number(value).toFixed(1) : "-";
  const cache = new Map();

  let dataset = null;
  let snapshot = null;
  let industryGroups = new Map();
  let level1Groups = new Map();
  let computedIndustry = new Map();
  let computedLevel1 = new Map();
  let renderTimer = 0;
  let renderSequence = 0;

  async function fetchJSON(path) {
    if (cache.has(path)) return cache.get(path);
    const response = await fetch(`${basePath}${path}`, { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    cache.set(path, payload);
    return payload;
  }

  function groupRows(rows, key) {
    const grouped = new Map();
    (rows || []).forEach((row) => {
      const value = row[key];
      if (!grouped.has(value)) grouped.set(value, []);
      grouped.get(value).push(row);
    });
    grouped.forEach((items) => items.sort((left, right) => String(left.trade_dt).localeCompare(String(right.trade_dt))));
    return grouped;
  }

  function insertWeightControls() {
    if ($("#weight-control-panel")) return;
    const controls = $(".primary-controls");
    if (!controls) return;
    const panel = domRoot.ownerDocument.createElement("section");
    panel.id = "weight-control-panel";
    panel.className = "weight-control-panel";
    panel.dataset.ready = "false";
    panel.setAttribute("aria-label", "扩散指数权重");
    panel.innerHTML = `
      <div class="weight-control-title">
        <h2>扩散指数权重</h2>
        <span>线性合成 · 自动归一</span>
      </div>
      <div class="weight-input-grid">
        ${dimensions.map((item) => `
          <label class="weight-input" style="--weight-color:${item.color}">
            <i aria-hidden="true"></i>
            <span>${item.label}</span>
            <input type="number" min="0" max="100" step="1" value="${item.weight}" data-weight-key="${item.key}" aria-label="${item.label}权重">
            <b>%</b>
          </label>`).join("")}
      </div>
      <div class="weight-actions">
        <span id="weight-total" class="weight-total">合计 100%</span>
        <button id="weight-reset" class="weight-reset" type="button" title="恢复默认权重" aria-label="恢复默认权重">↺</button>
      </div>`;
    controls.insertAdjacentElement("afterend", panel);
  }

  function currentWeights() {
    const weights = {};
    dimensions.forEach((item) => {
      const input = $(`[data-weight-key="${item.key}"]`);
      weights[item.key] = Math.max(0, finite(input?.value) ? Number(input.value) : item.weight);
    });
    return weights;
  }

  function updateWeightTotal() {
    const total = Object.values(currentWeights()).reduce((sum, value) => sum + value, 0);
    $("#weight-total").textContent = `合计 ${oneDecimal(total)}%`;
  }

  function weightedScore(row, weights) {
    let numerator = 0;
    let denominator = 0;
    dimensions.forEach((item) => {
      const sourceValue = row[item.key];
      const weight = Number(weights[item.key]);
      if (finite(sourceValue) && weight > 0) {
        const value = Number(sourceValue);
        numerator += value * weight;
        denominator += weight;
      }
    });
    if (denominator <= 0) return null;
    const raw = numerator / denominator;
    const reliability = finite(row.sample_reliability)
      ? Number(row.sample_reliability)
      : Number(row.valid_count) / (Number(row.valid_count) + 5);
    return 50 + reliability * (raw - 50);
  }

  function ewm(values, span, minPeriods) {
    const decay = 1 - 2 / (span + 1);
    let numerator = 0;
    let denominator = 0;
    let validCount = 0;
    return values.map((value) => {
      numerator *= decay;
      denominator *= decay;
      if (finite(value)) {
        numerator += Number(value);
        denominator += 1;
        validCount += 1;
      }
      return validCount >= minPeriods && denominator > 0 ? numerator / denominator : null;
    });
  }

  function processGroup(rows, weights) {
    const daily = rows.map((row) => weightedScore(row, weights));
    const smooth5 = ewm(daily, 5, 3);
    const smooth20 = ewm(daily, 20, 8);
    return rows.map((row, index) => ({ row, daily: daily[index], smooth5: smooth5[index], smooth20: smooth20[index] }));
  }

  function recompute(weights) {
    computedIndustry = new Map();
    industryGroups.forEach((rows, code) => computedIndustry.set(code, processGroup(rows, weights)));
    computedLevel1 = new Map();
    level1Groups.forEach((rows, name) => computedLevel1.set(name, processGroup(rows, weights)));
  }

  function mode() {
    const active = $("#smooth-control button.is-active")?.dataset.smooth || "5";
    return active === "raw" ? "daily" : active === "20" ? "smooth20" : "smooth5";
  }

  function windowed(points) {
    const active = $("#window-control button.is-active")?.dataset.window || "all";
    return active === "all" ? points : points.slice(-Number(active));
  }

  function pointValue(point) {
    return point?.[mode()];
  }

  function latestValue(points) {
    for (let index = points.length - 1; index >= 0; index -= 1) {
      const value = pointValue(points[index]);
      if (finite(value)) return Number(value);
    }
    return null;
  }

  function change20(points) {
    const values = points.map(pointValue).filter(finite).map(Number);
    return values.length > 20 ? values[values.length - 1] - values[values.length - 21] : null;
  }

  function scoreColor(value) {
    if (!finite(value)) return "#98a2b3";
    if (Number(value) >= 55) return "#c00000";
    if (Number(value) <= 45) return "#00b050";
    return "#808080";
  }

  function dynamicRange(values) {
    const clean = values.filter(finite).map(Number);
    if (!clean.length) return [0, 100];
    const minimum = Math.min(...clean);
    const maximum = Math.max(...clean);
    const span = Math.max(maximum - minimum, 1);
    const padding = Math.max(2, span * 0.08);
    return [Math.max(0, Math.floor(minimum - padding)), Math.min(100, Math.ceil(maximum + padding))];
  }

  async function updateLevel1Chart() {
    const chart = $("#level1-chart");
    if (!chart?.data || !window.Plotly) return;
    const allValues = [];
    for (let index = 0; index < chart.data.length; index += 1) {
      const trace = chart.data[index];
      const points = windowed(computedLevel1.get(trace.name) || []);
      const x = points.map((point) => point.row.trade_date);
      const y = points.map(pointValue);
      allValues.push(...y);
      await Plotly.restyle(chart, { x: [x], y: [y] }, [index]);
    }
    const range = dynamicRange(allValues);
    await Plotly.relayout(chart, { "yaxis.range": range, "yaxis.autorange": false });
  }

  function marketSeries() {
    const dates = new Map();
    computedIndustry.forEach((points) => {
      points.forEach((point) => {
        if (!point.row.is_reliable || !finite(pointValue(point))) return;
        const date = point.row.trade_date;
        const weight = Math.max(Number(point.row.valid_count) || 0, 1);
        const current = dates.get(date) || { numerator: 0, denominator: 0 };
        current.numerator += Number(pointValue(point)) * weight;
        current.denominator += weight;
        dates.set(date, current);
      });
    });
    return Array.from(dates.entries())
      .sort((left, right) => left[0].localeCompare(right[0]))
      .map(([date, value]) => ({ date, value: value.numerator / value.denominator }));
  }

  async function updateMarketChart() {
    const chart = $("#market-chart");
    if (!chart?.data || !window.Plotly) return;
    const points = windowed(marketSeries());
    const x = points.map((point) => point.date);
    const y = points.map((point) => point.value);
    await Plotly.restyle(chart, {
      x: [x],
      y: [y],
      name: [`自定义权重 · ${mode() === "daily" ? "原值" : mode() === "smooth20" ? "20日" : "5日"}`],
    }, [0]);
    const latest = y.filter(finite).at(-1);
    if ($("#market-last")) $("#market-last").textContent = finite(latest) ? `${oneDecimal(latest)} 分` : "-";
  }

  function rollingPercentile(values, period = 252, minPeriods = 40) {
    return values.map((value, index) => {
      const start = Math.max(0, index - period + 1);
      const windowValues = values.slice(start, index + 1).filter(finite).map(Number);
      if (!finite(value) || windowValues.length < minPeriods) return null;
      return windowValues.filter((item) => item <= Number(value)).length / windowValues.length * 100;
    });
  }

  function selectedGroup() {
    const industry = $("#industry-select")?.value;
    const level1 = $("#level1-select")?.value;
    return industry && industry !== "__L1__" ? computedIndustry.get(industry) : computedLevel1.get(level1);
  }

  async function updateSelectedCharts(weights) {
    const points = selectedGroup() || [];
    if (!points.length || !window.Plotly) return;
    const visible = windowed(points);
    const x = visible.map((point) => point.row.trade_date);
    const y = visible.map(pointValue);
    const allSelected = points.map(pointValue);
    const percentiles = rollingPercentile(allSelected);
    const visiblePercentiles = windowed(points.map((point, index) => ({ ...point, percentile: percentiles[index] })))
      .map((point) => point.percentile);
    const scoreChart = $("#industry-score-chart");
    if (scoreChart?.data?.length >= 2) {
      await Plotly.restyle(scoreChart, { x: [x], y: [y], name: ["自定义权重扩散"] }, [0]);
      await Plotly.restyle(scoreChart, { x: [x], y: [visiblePercentiles] }, [1]);
    }

    const driverChart = $("#driver-area-chart");
    if (driverChart?.data?.length >= dimensions.length) {
      for (let index = 0; index < dimensions.length; index += 1) {
        const item = dimensions[index];
        const driver = visible.map((point) => {
          const score = Number(point.row[item.key]);
          return Number.isFinite(score) ? score * Number(weights[item.key]) : 0;
        });
        await Plotly.restyle(driverChart, { x: [x], y: [driver] }, [index]);
      }
    }

    const latest = latestValue(points);
    const firstDimension = $("#dimension-strip .dimension-cell strong");
    if (firstDimension) firstDimension.textContent = oneDecimal(latest);
  }

  function latestIndustryRows() {
    return Array.from(computedIndustry.entries()).map(([code, points]) => ({
      code,
      points,
      latest: points[points.length - 1]?.row || {},
      score: latestValue(points),
      change20: change20(points),
    }));
  }

  function renderRank(target, rows) {
    const sorted = rows.filter((item) => finite(item.score)).sort((left, right) => left.score - right.score);
    Plotly.react(target, [{
      x: sorted.map((item) => item.score),
      y: sorted.map((item) => item.latest.industry_name),
      type: "bar",
      orientation: "h",
      marker: { color: sorted.map((item) => scoreColor(item.score)) },
      text: sorted.map((item) => oneDecimal(item.score)),
      textposition: "auto",
      customdata: sorted.map((item) => [item.code, item.change20, item.latest.sample_grade]),
      hovertemplate: "%{y}<br>扩散 %{x:.1f}<br>20日 %{customdata[1]:+.1f}<br>样本 %{customdata[2]}<extra></extra>",
    }], {
      showlegend: false,
      margin: { l: 116, r: 16, t: 10, b: 36 },
      paper_bgcolor: "#fff",
      plot_bgcolor: "#fff",
      font: { family: 'Arial, KaiTi, "楷体", sans-serif', size: 11, color: "#667085" },
      xaxis: { range: [0, 100], gridcolor: "#e7ebef", zeroline: false },
      yaxis: { automargin: true, gridcolor: "#fff", tickfont: { size: 11 } },
    }, { displayModeBar: false, responsive: true });
  }

  function updateRankings() {
    if (!window.Plotly) return;
    const reliableOnly = Boolean($("#reliable-only")?.checked);
    const level1 = $("#level1-select")?.value;
    const rows = latestIndustryRows().filter((item) => !reliableOnly || item.latest.is_reliable);
    renderRank($("#all-rank-chart"), rows);
    renderRank($("#level1-rank-chart"), rows.filter((item) => item.latest.level1_name === level1));
  }

  function scoreClass(value) {
    if (!finite(value)) return "score-neutral";
    if (Number(value) >= 55) return "score-strong";
    if (Number(value) <= 45) return "score-weak";
    return "score-neutral";
  }

  function updateScoreTable() {
    const body = $("#industry-score-table-body");
    if (!body) return;
    const level1 = $("#level1-select")?.value;
    const rows = latestIndustryRows()
      .filter((item) => item.latest.level1_name === level1)
      .sort((left, right) => Number(right.score) - Number(left.score));
    $("#industry-score-table-title").textContent = `${level1} · 三级行业多维得分`;
    $("#industry-score-table-count").textContent = `${rows.length} 个三级行业`;
    body.innerHTML = rows.map((item, index) => {
      const fields = [item.score, ...dimensions.map((dimension) => item.latest[dimension.key])];
      return `<tr data-industry-code="${item.code}">
        <td class="rank-cell">${index + 1}</td>
        <td class="industry-name-cell">${item.latest.industry_name}</td>
        ${fields.map((value) => `<td class="${scoreClass(value)}">${oneDecimal(value)}</td>`).join("")}
      </tr>`;
    }).join("");
  }

  function regime(score, change) {
    if (score >= 60 && change >= 3) return "扩散加速";
    if (score <= 42 && change <= -5) return "缩圈";
    if (score >= 55) return "偏扩散";
    if (score <= 45) return "偏缩圈";
    return "均衡";
  }

  function updateIndustryMap() {
    const latest = new Map(latestIndustryRows().map((item) => [item.code, item]));
    $("#industry-board")?.querySelectorAll(".industry-card[data-code]").forEach((card) => {
      const item = latest.get(card.dataset.code);
      if (!item) return;
      const score = card.querySelector(".industry-score");
      if (score) score.textContent = oneDecimal(item.score);
      const meta = card.querySelectorAll(".industry-card-meta span");
      if (meta[0]) meta[0].textContent = `20日 ${finite(item.change20) && item.change20 > 0 ? "+" : ""}${oneDecimal(item.change20)}`;
      if (meta[1]) meta[1].textContent = regime(item.score, item.change20);
    });
    const level1 = $("#level1-select")?.value;
    const reliableOnly = Boolean($("#reliable-only")?.checked);
    const selected = Array.from(latest.values()).filter((item) =>
      item.latest.level1_name === level1 && (!reliableOnly || item.latest.is_reliable)
    );
    const badges = $("#map-summary")?.querySelectorAll(".summary-badge");
    if (badges?.[0]) badges[0].textContent = `${selected.length} 个三级行业`;
    if (badges?.[1]) badges[1].textContent = `${selected.filter((item) => item.score >= 55).length} 个偏扩散`;
  }

  async function applyDynamicWeights(weights, sequence) {
    recompute(weights);
    if (sequence !== renderSequence) return;
    await Promise.all([updateMarketChart(), updateLevel1Chart()]);
    if (sequence !== renderSequence) return;
    updateRankings();
    updateScoreTable();
    updateIndustryMap();
    await updateSelectedCharts(weights);
  }

  function scheduleRender(delay = 90) {
    window.clearTimeout(renderTimer);
    const sequence = ++renderSequence;
    renderTimer = window.setTimeout(() => {
      window.requestAnimationFrame(() => {
        applyDynamicWeights(currentWeights(), sequence).catch((error) => console.warn("Weight render failed:", error));
      });
    }, delay);
  }

  function bindControls() {
    dimensions.forEach((item) => {
      $(`[data-weight-key="${item.key}"]`)?.addEventListener("input", () => {
        updateWeightTotal();
        scheduleRender();
      });
    });
    $("#weight-reset")?.addEventListener("click", () => {
      dimensions.forEach((item) => {
        const input = $(`[data-weight-key="${item.key}"]`);
        if (input) input.value = item.weight;
      });
      updateWeightTotal();
      scheduleRender(0);
    });
    ["#level1-select", "#industry-select", "#reliable-only"].forEach((selector) => {
      $(selector)?.addEventListener("change", () => scheduleRender(80));
    });
    $("#window-control")?.addEventListener("click", (event) => {
      if (event.target.closest("button")) scheduleRender(30);
    });
    $("#smooth-control")?.addEventListener("click", (event) => {
      if (event.target.closest("button")) scheduleRender(30);
    });
    $("#level1-tabs")?.addEventListener("click", (event) => {
      if (event.target.closest("button")) scheduleRender(120);
    });
    $("#industry-board")?.addEventListener("click", (event) => {
      if (event.target.closest(".industry-card")) scheduleRender(180);
    });
  }

  async function init() {
    insertWeightControls();
    updateWeightTotal();
    [dataset, snapshot] = await Promise.all([
      fetchJSON("/api/dynamic-series"),
      fetchJSON("/api/snapshot"),
    ]);
    industryGroups = groupRows(dataset.industry, "industry_code");
    level1Groups = groupRows(dataset.level1, "industry_name");
    $("#weight-control-panel").dataset.ready = "true";
    bindControls();
    scheduleRender(0);
  }

  async function mount(root, options = {}) {
    domRoot = root;
    if (options.basePath) basePath = String(options.basePath).replace(/\/$/, "");
    cache.clear();
    dataset = null;
    snapshot = null;
    industryGroups = new Map();
    level1Groups = new Map();
    computedIndustry = new Map();
    computedLevel1 = new Map();
    await init();
  }

  function invalidate() {
    cache.clear();
    dataset = null;
    snapshot = null;
  }

  window.AIMonitorWeights = { mount, invalidate };
})();
