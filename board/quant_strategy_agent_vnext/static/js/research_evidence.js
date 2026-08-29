(function () {
  "use strict";

  const cache = new Map();
  const supported = /^(allocation|liquidity|rotation|factorlab|technical|portfolio):/;
  const splitLabel = { train: "训练", validation: "验证", test: "封存测试" };
  const statusLabel = {
    current_champion: "当前最优",
    current_champion_with_shadow: "当前最优 + 影子候选",
    mixed_governance: "分模型治理",
    conditional_champion: "条件冠军",
    tracking_not_return_model: "跟踪模块",
    observe_only: "仅观察",
  };

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function baseUrl(path) {
    const prefix = String((window.APP_BOOT && window.APP_BOOT.basePath) || "").replace(/\/$/, "");
    return prefix + path;
  }

  async function loadPlotly() {
    if (window.Plotly) return window.Plotly;
    if (!window.__researchEvidencePlotly) {
      window.__researchEvidencePlotly = new Promise(function (resolve, reject) {
        const script = document.createElement("script");
        script.src = window.APP_BOOT.plotlyUrl;
        script.onload = function () { resolve(window.Plotly); };
        script.onerror = reject;
        document.head.appendChild(script);
      });
    }
    return window.__researchEvidencePlotly;
  }

  async function fetchPayload(route) {
    if (!cache.has(route)) {
      cache.set(
        route,
        fetch(baseUrl("/api/research-evidence?route=" + encodeURIComponent(route)), {
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        }).then(function (response) {
          if (!response.ok) throw new Error("research_evidence_http_" + response.status);
          return response.json();
        })
      );
    }
    return cache.get(route);
  }

  function metricTable(rows) {
    if (!rows || !rows.length) return "<p>本页不是收益模型，不生成夏普指标。</p>";
    return (
      '<table class="research-evidence__table"><thead><tr>' +
      "<th>样本</th><th>年化</th><th>超额</th><th>夏普</th><th>IR</th><th>回撤</th><th>换手</th>" +
      "</tr></thead><tbody>" +
      rows.map(function (row) {
        return (
          "<tr><td>" + esc(splitLabel[row.split] || row.split) + "</td>" +
          "<td>" + (Number(row.annual_return || 0) * 100).toFixed(1) + "%</td>" +
          "<td>" + (Number(row.annual_excess_return || 0) * 100).toFixed(1) + "%</td>" +
          "<td>" + Number(row.sharpe || 0).toFixed(2) + "</td>" +
          "<td>" + Number(row.information_ratio || 0).toFixed(2) + "</td>" +
          "<td>" + (Number(row.max_drawdown || 0) * 100).toFixed(1) + "%</td>" +
          "<td>" + Number(row.turnover || 0).toFixed(2) + "</td></tr>"
        );
      }).join("") +
      "</tbody></table>"
    );
  }

  function gateItems(payload) {
    const gates = (payload.governance && payload.governance.gates) || [];
    if (gates.length) {
      return gates.map(function (gate) {
        return '<span data-pass="' + Boolean(gate.passed) + '"><strong>' +
          esc(gate.label || gate.gate) + "</strong><br>" +
          Number(gate.observed || 0).toFixed(3) + " / " +
          esc(gate.comparison === "le" ? "≤" : "≥") +
          Number(gate.threshold || 0).toFixed(3) + "</span>";
      }).join("");
    }
    const promotion = (payload.governance && payload.governance.promotion_gate) || {};
    const checks = promotion.checks || {};
    const items = Object.keys(checks).map(function (key) {
      return '<span data-pass="' + Boolean(checks[key]) + '"><strong>' +
        esc(key) + "</strong><br>" + (checks[key] ? "通过" : "未通过") + "</span>";
    });
    if (payload.governance && payload.governance.warning) {
      items.push('<span data-pass="false"><strong>当前限制</strong><br>' +
        esc(payload.governance.warning) + "</span>");
    }
    if (payload.governance && payload.governance.reason) {
      items.push('<span data-pass="false"><strong>当前限制</strong><br>' +
        esc(payload.governance.reason) + "</span>");
    }
    return items.join("") || '<span><strong>治理状态</strong><br>' +
      esc((payload.governance && payload.governance.test_policy) || payload.status) + "</span>";
  }

  function referenceLinks(rows) {
    if (!rows || !rows.length) return "";
    return '<details class="research-evidence__references"><summary>方法来源：公开PDF，仅用于框架借鉴</summary>' +
      rows.map(function (row) {
        return '<a href="' + esc(row.url) + '" target="_blank" rel="noopener noreferrer">' +
          esc(row.broker + " " + row.date + " " + row.title) + "</a>";
      }).join("") + "</details>";
  }

  function html(route, payload) {
    const flow = (payload.mechanism && payload.mechanism.nodes) || [];
    const description = payload.status === "tracking_not_return_model"
      ? "资金页面保留跟踪属性，证据层补充来源、时滞和数据质量，不制造策略夏普。"
      : payload.status === "observe_only"
        ? "当前证据不足以晋级生产模型，页面明确展示缺口与下一轮验证路径。"
        : "当前页面只展示治理后的冠军及不可晋级的影子诊断；封存测试不参与选模。";
    return (
      '<section class="research-evidence" data-route="' + esc(route) + '">' +
      '<header class="research-evidence__head"><div><small>模型与数据证据层</small>' +
      "<h2>机理、诊断、回测与归因</h2><p>" + esc(description) + "</p></div>" +
      '<span class="research-evidence__status">' +
      esc(statusLabel[payload.status] || payload.status) + "</span></header>" +
      '<div class="research-evidence__layers">' +
      (payload.layers || []).map(function (layer) { return "<span>" + esc(layer.label) + "</span>"; }).join("") +
      "</div>" +
      '<div class="research-evidence__flow">' +
      flow.map(function (node) { return "<span>" + esc(node) + "</span>"; }).join("") +
      "</div>" +
      '<div class="research-evidence__formula">' + esc((payload.mechanism || {}).formula || "") + "</div>" +
      '<div class="research-evidence__grid">' +
      '<article class="research-evidence__panel"><h3>训练、验证、封存测试</h3>' +
      metricTable(payload.metrics) + "</article>" +
      '<article class="research-evidence__panel"><h3>模型治理</h3><div class="research-evidence__gate">' +
      gateItems(payload) + "</div></article>" +
      '<article class="research-evidence__panel"><h3>样本外表现与稳健性</h3>' +
      '<div class="research-evidence__plot" data-evidence-plot="performance"></div></article>' +
      '<article class="research-evidence__panel"><h3>收益损失与候选诊断</h3>' +
      '<div class="research-evidence__plot" data-evidence-plot="diagnostics"></div></article>' +
      "</div>" + referenceLinks(payload.references) + "</section>"
    );
  }

  function plotPerformance(host, payload) {
    const rows = payload.metrics || [];
    if (!rows.length) {
      const quality = payload.descriptive && payload.descriptive.quality_counts;
      const inventory = payload.chart_inventory || [];
      const x = quality ? Object.keys(quality) : inventory.map(function (row) { return row.title; }).slice(0, 10);
      const y = quality ? x.map(function (key) { return quality[key]; }) : inventory.map(function () { return 1; }).slice(0, 10);
      return window.Plotly.newPlot(host, [{ type: "bar", x: x, y: y, marker: { color: "#2f75b5" } }], {
        height: 320, margin: { l: 45, r: 16, t: 18, b: 90 },
        xaxis: { tickangle: -30 }, yaxis: { title: quality ? "来源数" : "图表覆盖" },
      }, { responsive: true, displaylogo: false });
    }
    const x = rows.map(function (row) { return splitLabel[row.split] || row.split; });
    return window.Plotly.newPlot(host, [
      { type: "bar", name: "年化收益", x: x, y: rows.map(function (row) { return Number(row.annual_return) * 100; }), marker: { color: "#b42318" } },
      { type: "bar", name: "年化超额", x: x, y: rows.map(function (row) { return Number(row.annual_excess_return) * 100; }), marker: { color: "#c46a08" } },
      { type: "scatter", mode: "lines+markers", name: "夏普", x: x, y: rows.map(function (row) { return Number(row.sharpe); }), yaxis: "y2", line: { color: "#2f75b5", width: 2.4 } },
      { type: "scatter", mode: "lines+markers", name: "IR", x: x, y: rows.map(function (row) { return Number(row.information_ratio); }), yaxis: "y2", line: { color: "#168a47", width: 2.1, dash: "dot" } },
    ], {
      height: 320, barmode: "group", margin: { l: 50, r: 52, t: 18, b: 50 },
      yaxis: { title: "收益（%）" }, yaxis2: { title: "风险调整指标", overlaying: "y", side: "right" },
      legend: { orientation: "h", y: -0.2 },
    }, { responsive: true, displaylogo: false });
  }

  function plotDiagnostics(host, payload) {
    const candidates = payload.candidate_diagnostics || [];
    if (candidates.length) {
      return window.Plotly.newPlot(host, [{
        type: "scatter", mode: "markers",
        x: candidates.map(function (row) { return row.validation_turnover; }),
        y: candidates.map(function (row) { return row.validation_sharpe; }),
        text: candidates.map(function (row) { return row.candidate; }),
        marker: {
          size: 11,
          color: candidates.map(function (row) { return row.validation_rank_ic; }),
          colorscale: "RdBu", reversescale: true, showscale: true,
          colorbar: { title: "验证IC" },
        },
        hovertemplate: "%{text}<br>验证换手 %{x:.3f}<br>验证夏普 %{y:.3f}<extra></extra>",
      }], {
        height: 320, margin: { l: 52, r: 28, t: 18, b: 48 },
        xaxis: { title: "验证期换手" }, yaxis: { title: "验证期夏普" },
        shapes: [{ type: "line", x0: 0.65, x1: 0.65, y0: 0, y1: 1, yref: "paper", line: { color: "#b42318", dash: "dash" } }],
      }, { responsive: true, displaylogo: false });
    }
    const costs = payload.cost_sensitivity || (payload.robustness && payload.robustness.cost_sensitivity_test) || [];
    if (costs.length) {
      return window.Plotly.newPlot(host, [
        { type: "scatter", mode: "lines+markers", name: "年化", x: costs.map(function (row) { return row.cost_bps; }), y: costs.map(function (row) { return Number(row.annual_return) * 100; }), line: { color: "#b42318", width: 2.4 } },
        { type: "scatter", mode: "lines+markers", name: "夏普", x: costs.map(function (row) { return row.cost_bps; }), y: costs.map(function (row) { return Number(row.sharpe); }), yaxis: "y2", line: { color: "#2f75b5", width: 2.4 } },
      ], {
        height: 320, margin: { l: 50, r: 52, t: 18, b: 48 },
        xaxis: { title: "单边成本（bp）" }, yaxis: { title: "年化（%）" },
        yaxis2: { title: "夏普", overlaying: "y", side: "right" },
        legend: { orientation: "h", y: -0.2 },
      }, { responsive: true, displaylogo: false });
    }
    if (payload.models && payload.models.length) {
      const rows = [];
      payload.models.forEach(function (model) {
        (model.metrics || []).forEach(function (metric) {
          rows.push({ model: model.model, split: metric.split, sharpe: metric.sharpe, ir: metric.information_ratio });
        });
      });
      const models = Array.from(new Set(rows.map(function (row) { return row.model; })));
      return window.Plotly.newPlot(host, models.map(function (model, index) {
        const part = rows.filter(function (row) { return row.model === model; });
        return {
          type: "bar", name: model,
          x: part.map(function (row) { return splitLabel[row.split] || row.split; }),
          y: part.map(function (row) { return Number(row.sharpe); }),
          marker: { color: ["#b42318", "#2f75b5", "#c46a08"][index % 3] },
        };
      }), {
        height: 320, barmode: "group", margin: { l: 50, r: 16, t: 18, b: 48 },
        yaxis: { title: "夏普" }, legend: { orientation: "h", y: -0.2 },
      }, { responsive: true, displaylogo: false });
    }
    if (payload.shadow && payload.shadow.metrics) {
      const champion = payload.metrics || [];
      const shadow = payload.shadow.metrics || [];
      return window.Plotly.newPlot(host, [
        { type: "bar", name: "当前冠军", x: champion.map(function (row) { return splitLabel[row.split] || row.split; }), y: champion.map(function (row) { return row.information_ratio; }), marker: { color: "#b42318" } },
        { type: "bar", name: "仅诊断影子", x: shadow.map(function (row) { return splitLabel[row.split] || row.split; }), y: shadow.map(function (row) { return row.information_ratio; }), marker: { color: "#98a2b3" } },
      ], {
        height: 320, barmode: "group", margin: { l: 50, r: 16, t: 18, b: 48 },
        yaxis: { title: "信息比率" }, legend: { orientation: "h", y: -0.2 },
      }, { responsive: true, displaylogo: false });
    }
    const requirements = payload.descriptive && payload.descriptive.required_validation;
    host.innerHTML = requirements && requirements.length
      ? '<ol>' + requirements.map(function (row) { return "<li>" + esc(row) + "</li>"; }).join("") + "</ol>"
      : "<p>现有页面已覆盖该板块的历史图表；证据层不重复绘制。</p>";
    return Promise.resolve();
  }

  async function mount(route, workspaceSection) {
    if (!supported.test(route)) return;
    const evidenceRoute = route === "factorlab:strategy" &&
      (!workspaceSection || workspaceSection.kind !== "index")
      ? "factorlab:dashboard"
      : route;
    const payload = await fetchPayload(evidenceRoute);
    if (!payload || payload.status === "not_applicable") return;
    const root = document.getElementById("view-root");
    if (!root) return;
    const pane = root.querySelector('.view-cache-pane[data-view="' + CSS.escape(route) + '"]');
    const host = pane || root;
    const old = host.querySelector('.research-evidence[data-route="' + CSS.escape(route) + '"]');
    if (old) old.remove();
    host.insertAdjacentHTML("beforeend", html(route, payload));
    const section = host.querySelector('.research-evidence[data-route="' + CSS.escape(route) + '"]');
    if (!section) return;
    await loadPlotly();
    await Promise.all([
      plotPerformance(section.querySelector('[data-evidence-plot="performance"]'), payload),
      plotDiagnostics(section.querySelector('[data-evidence-plot="diagnostics"]'), payload),
    ]);
  }

  window.ResearchEvidence = { mount: mount, clearCache: function () { cache.clear(); } };
})();
