(function () {
  "use strict";

  const cache = new Map();
  const supported = /^(allocation|liquidity|rotation|factorlab|technical|portfolio):/;
  const statusLabels = {
    current_champion: "当前研究冠军",
    current_champion_with_shadow: "当前冠军 + 影子候选",
    mixed_governance: "分模型治理",
    conditional_champion: "条件研究冠军",
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

  function number(value, digits) {
    const result = Number(value);
    return Number.isFinite(result) ? result.toFixed(digits == null ? 2 : digits) : "—";
  }

  function sparkline(values) {
    const clean = (Array.isArray(values) ? values : [])
      .map(Number)
      .filter(Number.isFinite);
    if (!clean.length) return '<span class="dense-empty">—</span>';
    const width = 116;
    const height = 32;
    const pad = 2;
    const min = Math.min.apply(null, clean);
    const max = Math.max.apply(null, clean);
    const scale = max - min || 1;
    const points = clean.map(function (value, index) {
      const x = pad + index * (width - pad * 2) / Math.max(clean.length - 1, 1);
      const y = height - pad - (value - min) * (height - pad * 2) / scale;
      return x.toFixed(1) + "," + y.toFixed(1);
    }).join(" ");
    const positive = clean[clean.length - 1] >= clean[0];
    return '<svg class="dense-sparkline" viewBox="0 0 ' + width + " " + height +
      '" preserveAspectRatio="none" aria-label="历史趋势"><polyline points="' +
      points + '" fill="none" stroke="' + (positive ? "#163d7a" : "#b42318") +
      '" stroke-width="2"/><circle cx="' + points.split(" ").slice(-1)[0].split(",")[0] +
      '" cy="' + points.split(" ").slice(-1)[0].split(",")[1] +
      '" r="2.4" fill="' + (positive ? "#163d7a" : "#b42318") + '"/></svg>';
  }

  function statusCell(value) {
    const normalized = String(value == null ? "" : value).toLowerCase();
    const pass = value === true || ["pass", "passed", "live", "ready", "optimal", "true", "a"].includes(normalized);
    const fail = value === false || ["fail", "failed", "false", "rejected", "not_installed"].includes(normalized);
    const label = value === true ? "通过" : value === false ? "未通过" : String(value || "—");
    return '<span class="dense-status" data-tone="' + (pass ? "pass" : fail ? "fail" : "neutral") + '">' +
      esc(label) + "</span>";
  }

  function formattedCell(row, column) {
    const value = row[column.key];
    const format = column.format || "text";
    if (format === "sparkline") return sparkline(value);
    if (format === "status") return statusCell(value);
    if (format === "arrow") {
      const numeric = Number(value);
      const arrow = numeric > 1e-12 ? "↑" : numeric < -1e-12 ? "↓" : "→";
      return '<span class="dense-arrow" data-sign="' + (numeric > 0 ? "positive" : numeric < 0 ? "negative" : "flat") + '">' + arrow + "</span>";
    }
    if (format === "integer") return esc(Math.round(Number(value) || 0));
    if (format === "scientific") {
      const numeric = Number(value);
      return Number.isFinite(numeric) ? esc(numeric.toExponential(2)) : "—";
    }
    if (format === "percent" || format === "signed_percent") {
      const numeric = Number(value);
      const text = Number.isFinite(numeric) ? (numeric * 100).toFixed(1) + "%" : "—";
      return '<span class="dense-number" data-sign="' +
        (format === "signed_percent" ? (numeric > 0 ? "positive" : numeric < 0 ? "negative" : "flat") : "flat") +
        '">' + esc(text) + "</span>";
    }
    if (format === "percentile") {
      const numeric = Number(value);
      const ratio = Number.isFinite(numeric) ? Math.max(0, Math.min(1, numeric)) : 0;
      return '<span class="dense-heat" style="--ratio:' + (ratio * 100).toFixed(1) + '%">' +
        (Number.isFinite(numeric) ? (numeric * 100).toFixed(0) + "%" : "—") + "</span>";
    }
    if (format === "number" || format === "signed") {
      const numeric = Number(value);
      return '<span class="dense-number" data-sign="' +
        (format === "signed" ? (numeric > 0 ? "positive" : numeric < 0 ? "negative" : "flat") : "flat") +
        '">' + esc(number(numeric, 2)) + "</span>";
    }
    return esc(value);
  }

  function denseTable(table) {
    const columns = (table && table.columns) || [];
    const rows = (table && table.rows) || [];
    if (!columns.length) return '<div class="dense-empty">该层暂无可审计结构化数据。</div>';
    return '<div class="dense-table-wrap"><table class="dense-table"><thead><tr>' +
      columns.map(function (column) { return "<th>" + esc(column.label) + "</th>"; }).join("") +
      "</tr></thead><tbody>" +
      rows.map(function (row) {
        return "<tr>" + columns.map(function (column) {
          return '<td data-format="' + esc(column.format || "text") + '">' +
            formattedCell(row, column) + "</td>";
        }).join("") + "</tr>";
      }).join("") +
      "</tbody></table></div>";
  }

  function primitiveRows(payload) {
    const rows = [
      { item: "证据状态", value: statusLabels[payload.status] || payload.status },
      { item: "数据时点", value: typeof payload.as_of === "object" ? JSON.stringify(payload.as_of) : payload.as_of },
    ];
    const objects = [payload.champion || {}, payload.solver || {}];
    objects.forEach(function (object) {
      Object.keys(object).forEach(function (key) {
        const value = object[key];
        if (value == null || typeof value === "object") return;
        rows.push({ item: key, value: value });
      });
    });
    return rows.slice(0, 14);
  }

  function mechanismBlock(payload) {
    const nodes = (payload.mechanism && payload.mechanism.nodes) || [];
    const table = {
      columns: [
        { key: "item", label: "模型对象", format: "text" },
        { key: "value", label: "当前值", format: "text" },
      ],
      rows: primitiveRows(payload),
    };
    return '<section class="dense-block" data-block="mechanism">' +
      '<header><span>01</span><div><h3>原理与机理</h3><p>从输入、变换、估计、约束到输出的完整链条，右侧保留当前模型与治理口径。</p></div></header>' +
      '<div class="dense-block__body"><div class="dense-visual dense-flow">' +
      nodes.map(function (node, index) {
        return '<div><b>' + String(index + 1).padStart(2, "0") + "</b><span>" + esc(node) + "</span></div>";
      }).join("") + '</div><div class="dense-data">' + denseTable(table) + "</div></div>" +
      '<div class="dense-formula">' + esc((payload.mechanism || {}).formula || "") + "</div></section>";
  }

  const blockMeta = {
    descriptive: ["02", "数据描述与实时监测"],
    history: ["03", "历史复盘与最新跟踪"],
    diagnostics: ["04", "模型拟合、预测与诊断"],
    strategy: ["05", "策略回测、归因与更新"],
  };

  function evidenceBlock(key, block) {
    const meta = blockMeta[key];
    return '<section class="dense-block" data-block="' + key + '"><header><span>' + meta[0] +
      '</span><div><h3>' + meta[1] + '</h3><p>' + esc(block.note || "") +
      '</p></div></header><h4>' + esc(block.title || meta[1]) +
      '</h4><div class="dense-block__body"><div class="dense-data">' +
      denseTable(block.table) + '</div><div class="dense-visual"><div class="dense-plot" data-dense-plot="' +
      key + '"></div></div></div></section>';
  }

  function referenceLinks(rows) {
    if (!rows || !rows.length) return "";
    return '<details class="research-evidence__references"><summary>公开PDF方法来源</summary>' +
      rows.map(function (row) {
        return '<a href="' + esc(row.url) + '" target="_blank" rel="noopener noreferrer">' +
          esc(row.broker + " " + row.date + " " + row.title) + "</a>";
      }).join("") + "</details>";
  }

  function highDensityHtml(route, payload) {
    const visuals = payload.visuals || {};
    return '<section class="research-evidence research-evidence--dense" data-route="' + esc(route) + '">' +
      '<header class="research-evidence__head"><div><small>模型与数据证据层</small>' +
      '<h2>高密度研究工作台</h2><p>图表只回答机理、状态、样本外稳定性、执行成本和收益来源，不以测试期结果反向选模。</p></div>' +
      '<span class="research-evidence__status">' + esc(statusLabels[payload.status] || payload.status) +
      "</span></header>" +
      mechanismBlock(payload) +
      ["descriptive", "history", "diagnostics", "strategy"].map(function (key) {
        return visuals[key] ? evidenceBlock(key, visuals[key]) : "";
      }).join("") +
      referenceLinks(payload.references) + "</section>";
  }

  function plotTrace(trace) {
    const type = trace.type || "scatter";
    const values = (trace.y || []).map(Number);
    const result = {
      type: type,
      mode: trace.mode || (type === "bar" ? undefined : "lines"),
      name: trace.name,
      x: trace.x || [],
      y: trace.y || [],
      yaxis: trace.axis === "y2" ? "y2" : "y",
      text: trace.text || undefined,
      hovertemplate: trace.text ? "%{text}<br>x=%{x}<br>y=%{y:.4f}<extra></extra>" : undefined,
    };
    if (type === "bar") {
      result.marker = {
        color: values.map(function (value) { return value >= 0 ? "#2f75b5" : "#b42318"; }),
        line: { color: "#ffffff", width: 0.4 },
      };
    } else if ((trace.mode || "").indexOf("markers") >= 0) {
      result.marker = {
        size: 10,
        color: trace.color || values,
        colorscale: trace.color ? undefined : "RdBu",
        reversescale: true,
        line: { color: "#ffffff", width: 0.8 },
      };
    } else {
      result.line = { color: trace.color || undefined, width: 2.2 };
    }
    return result;
  }

  function renderPlot(host, chart) {
    if (!host || !chart) return Promise.resolve();
    let traces;
    if (chart.heatmap) {
      traces = [{
        type: "heatmap",
        x: chart.heatmap.x || [],
        y: chart.heatmap.y || [],
        z: chart.heatmap.z || [],
        zmin: 0,
        zmax: 1,
        colorscale: [[0, "#f5c2c0"], [0.49, "#f5c2c0"], [0.5, "#c7d9ee"], [1, "#163d7a"]],
        showscale: false,
        hovertemplate: "%{y}<br>%{x}: %{z}<extra></extra>",
      }];
    } else {
      traces = (chart.traces || []).map(plotTrace);
    }
    if (!traces.length) {
      host.innerHTML = '<div class="dense-empty">当前冻结快照没有可绘制序列。</div>';
      return Promise.resolve();
    }
    const shapes = [];
    if (Number.isFinite(Number(chart.vline))) {
      shapes.push({
        type: "line",
        x0: Number(chart.vline),
        x1: Number(chart.vline),
        y0: 0,
        y1: 1,
        yref: "paper",
        line: { color: "#b42318", dash: "dash", width: 1.5 },
      });
    }
    const heatmapRows = chart.heatmap ? (chart.heatmap.y || []).length : 0;
    const plotHeight = chart.heatmap
      ? Math.max(390, Math.min(700, heatmapRows * 28 + 130)) : 370;
    const layout = {
      title: { text: chart.title || "", x: 0.01, xanchor: "left", font: { size: 14, color: "#172033" } },
      height: plotHeight,
      margin: { l: 58, r: chart.y2_title ? 58 : 22, t: 48, b: 82 },
      paper_bgcolor: "#ffffff",
      plot_bgcolor: "#fbfcfe",
      hovermode: "closest",
      dragmode: false,
      barmode: "group",
      xaxis: {
        title: chart.x_title || "",
        autorange: true,
        automargin: true,
        rangeslider: { visible: false },
        tickangle: chart.heatmap ? -35 : -18,
        gridcolor: "#e7ebf0",
        zerolinecolor: "#98a2b3",
      },
      yaxis: {
        autorange: true,
        automargin: true,
        title: chart.y_title || "",
        gridcolor: "#e7ebf0",
        zerolinecolor: "#98a2b3",
      },
      legend: { orientation: "h", y: -0.27, x: 0 },
      shapes: shapes,
      font: { family: "Arial, KaiTi, STKaiti, sans-serif", size: 12, color: "#172033" },
    };
    if (chart.y2_title) {
      layout.yaxis2 = {
        title: chart.y2_title,
        overlaying: "y",
        side: "right",
        showgrid: false,
      };
    }
    return window.Plotly.newPlot(host, traces, layout, {
      responsive: true,
      scrollZoom: false,
      doubleClick: false,
      displaylogo: false,
      modeBarButtonsToRemove: ["lasso2d", "select2d", "pan2d", "zoom2d", "zoomIn2d", "zoomOut2d"],
    });
  }

  async function mount(route, workspaceSection) {
    if (!supported.test(route)) return;
    const evidenceRoute = route === "factorlab:strategy" &&
      (!workspaceSection || workspaceSection.kind !== "index")
      ? "factorlab:dashboard"
      : route;
    const payload = await fetchPayload(evidenceRoute);
    if (!payload || !payload.visuals) return;
    const root = document.getElementById("view-root");
    if (!root) return;
    const pane = root.querySelector('.view-cache-pane[data-view="' + CSS.escape(route) + '"]');
    const host = pane || root;
    const previous = host.querySelector('.research-evidence[data-route="' + CSS.escape(route) + '"]');
    if (previous) previous.remove();
    host.insertAdjacentHTML("beforeend", highDensityHtml(route, payload));
    const section = host.querySelector('.research-evidence[data-route="' + CSS.escape(route) + '"]');
    if (!section) return;
    await loadPlotly();
    await Promise.all(
      ["descriptive", "history", "diagnostics", "strategy"].map(function (key) {
        const chart = payload.visuals[key] && payload.visuals[key].chart;
        return renderPlot(section.querySelector('[data-dense-plot="' + key + '"]'), chart);
      })
    );
  }

  window.ResearchEvidence = {
    mount: mount,
    clearCache: function () { cache.clear(); },
  };
})();
