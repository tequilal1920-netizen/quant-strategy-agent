(function () {
  "use strict";

  var MODULE_ORDER = ["macro", "global_markets", "sw_industries", "commodities", "stock", "news_events"];
  var MODULE_LABELS = {
    macro: "宏观", global_markets: "全球市场", sw_industries: "申万行业",
    commodities: "大宗商品", stock: "个股", news_events: "新闻事件"
  };
  var STATUS_LABELS = {
    ok: "正常", partial: "部分可用", stale: "已滞后", failed: "失败",
    unavailable: "暂无数据", warning: "待复核", unknown: "未标记"
  };
  var VIEW_KEYS = ["overview", "trend", "cross_section", "events", "quality"];
  var COLORS = ["#c00000", "#ffc000", "#2f75b5", "#808080", "#ed7d31", "#7030a0", "#00b050", "#5b9bd5", "#a5a5a5", "#ff0000"];
  var CHART_STYLE = { aspectRatio: "5 / 3", baseWidth: 480, baseHeight: 288, fontSize: 12, lineWidth: 2.333, referenceLineWidth: 1, axisTicks: 12 };
  var STORAGE_KEY = "research-market-board.saved-views.v2";
  var DEFAULT_STATUSES = ["ok", "partial", "stale", "warning"];
  var state = {
    snapshot: null,
    activeModule: initialModule(),
    activeView: initialView(),
    moduleOverrides: {},
    selectedByModule: {},
    plotNodes: [],
    tableStates: {},
    renderFrame: 0,
    controls: {
      window: "1y", frequency: "auto", transform: "raw", benchmark: "",
      chartType: "line", statuses: DEFAULT_STATUSES.slice()
    },
    savedViews: []
  };

  function byId(id) { return document.getElementById(id); }
  function array(value) { return Array.isArray(value) ? value : []; }
  function object(value) { return value && typeof value === "object" && !Array.isArray(value) ? value : {}; }
  function create(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function clearNode(node) { while (node && node.firstChild) node.removeChild(node.firstChild); }
  function display(value, fallback) {
    if (value === null || value === undefined || value === "") return fallback || "暂无数据";
    if (typeof value === "object") return fallback || "暂无数据";
    return String(value);
  }
  function safeStatus(status) {
    var key = String(status || "").toLowerCase();
    if (key === "live") key = "ok";
    if (key === "metadata_only" || key === "metadata-only") key = "unavailable";
    return Object.prototype.hasOwnProperty.call(STATUS_LABELS, key) ? key : "unknown";
  }
  function statusName(status) { return STATUS_LABELS[safeStatus(status)]; }
  function initialModule() {
    var requested = new URLSearchParams(window.location.search).get("module");
    return MODULE_ORDER.indexOf(requested) >= 0 ? requested : "macro";
  }
  function initialView() {
    var requested = new URLSearchParams(window.location.search).get("view");
    return VIEW_KEYS.indexOf(requested) >= 0 ? requested : "overview";
  }
  function activeModuleData() {
    if (!state.snapshot) return {};
    return object(state.moduleOverrides[state.activeModule] || object(state.snapshot.modules)[state.activeModule]);
  }
  function finite(value) {
    if (value === null || value === undefined || value === "") return null;
    var numeric = typeof value === "number" ? value : Number(value);
    return Number.isFinite(numeric) ? numeric : null;
  }
  function parseDate(value) {
    if (value === null || value === undefined || value === "") return null;
    var text = String(value).trim();
    var match = /^(\d{4})[-/]?(\d{2})[-/]?(\d{2})(?:[T\s].*)?$/.exec(text);
    var date;
    if (match) {
      date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
    } else {
      date = new Date(text);
    }
    return Number.isFinite(date.getTime()) ? date : null;
  }
  function dateLabel(date) {
    return date.getUTCFullYear() + "-" + String(date.getUTCMonth() + 1).padStart(2, "0") + "-" + String(date.getUTCDate()).padStart(2, "0");
  }
  function pointFrom(rawPoint) {
    var raw = object(rawPoint);
    var date = parseDate(raw.date !== undefined ? raw.date : raw.x);
    if (!date) return null;
    var point = {
      date: display(raw.date !== undefined ? raw.date : raw.x, dateLabel(date)),
      dateObject: date,
      time: date.getTime(),
      value: finite(raw.value !== undefined ? raw.value : raw.y)
    };
    ["open", "high", "low", "close"].forEach(function (key) { point[key] = finite(raw[key]); });
    return point;
  }
  function cleanPoints(data) {
    var lookup = {};
    array(data).forEach(function (raw) {
      var point = pointFrom(raw);
      if (point) lookup[String(point.time)] = point;
    });
    return Object.keys(lookup).map(function (key) { return lookup[key]; }).sort(function (a, b) { return a.time - b.time; });
  }
  function normaliseSeries(rawSeries, index, context) {
    var raw = object(rawSeries);
    var ctx = object(context);
    var id = display(raw.id, (ctx.id || "series") + "-" + (index + 1));
    var label = display(raw.label || raw.name, "未命名指标 " + (index + 1));
    return {
      id: id,
      label: label,
      submodule: display(raw.submodule || raw.group || ctx.submodule, "未分类"),
      unit: display(raw.unit !== undefined ? raw.unit : ctx.unit, "未标单位"),
      frequency: display(raw.frequency || ctx.frequency, "未标频率"),
      status: safeStatus(raw.status || ctx.status),
      source: display(raw.source || ctx.source, "未提供"),
      as_of: display(raw.as_of || ctx.as_of, "暂无数据"),
      data: array(raw.data),
      raw: raw
    };
  }
  function moduleSeries(module) {
    var result = [];
    var seen = {};
    function add(series) {
      var base = String(series.id);
      var id = base;
      var suffix = 2;
      while (seen[id]) { id = base + "-" + suffix; suffix += 1; }
      series.id = id;
      seen[id] = true;
      result.push(series);
    }

    if (Array.isArray(module.series)) {
      module.series.forEach(function (raw, index) {
        add(normaliseSeries(raw, index, {
          id: "indicator", status: module.status, source: module.source, as_of: module.as_of
        }));
      });
      return result;
    }

    // Compatibility path for snapshots created before modules[*].series became canonical.
    array(module.charts).forEach(function (rawChart, chartIndex) {
      var chart = object(rawChart);
      array(chart.series).forEach(function (raw, seriesIndex) {
        var candidate = normaliseSeries(raw, seriesIndex, {
          id: display(chart.id, "chart-" + (chartIndex + 1)),
          submodule: display(chart.title, "图表序列"),
          unit: chart.unit,
          status: chart.status,
          source: chart.source,
          as_of: chart.as_of
        });
        candidate.id = display(object(raw).id, display(chart.id, "chart-" + (chartIndex + 1)) + "::" + candidate.label);
        var duplicate = result.some(function (existing) {
          return existing.id === candidate.id || (existing.label === candidate.label && existing.unit === candidate.unit);
        });
        if (!duplicate) add(candidate);
      });
    });

    array(module.kpis).forEach(function (rawKpi, index) {
      var kpi = object(rawKpi);
      var duplicate = result.some(function (existing) {
        return existing.id === kpi.id || existing.label === kpi.label;
      });
      if (duplicate) return;
      var value = finite(kpi.value);
      var data = value === null ? [] : [{ date: kpi.as_of || module.as_of, value: value }];
      add(normaliseSeries({
        id: kpi.id || "kpi-" + (index + 1), label: kpi.label, submodule: "关键指标",
        unit: kpi.unit, frequency: "快照", status: kpi.status, source: kpi.source,
        as_of: kpi.as_of, data: data
      }, index, {}));
    });
    return result;
  }
  function hasNumericData(series) {
    return cleanPoints(series.data).some(function (point) { return point.value !== null; });
  }
  function getModuleSeries() { return moduleSeries(activeModuleData()); }
  function getSelectedSet() {
    if (!state.selectedByModule[state.activeModule]) {
      var defaults = getModuleSeries().filter(function (series) {
        return DEFAULT_STATUSES.indexOf(series.status) >= 0 && hasNumericData(series);
      }).map(function (series) { return series.id; });
      state.selectedByModule[state.activeModule] = new Set(defaults);
    }
    return state.selectedByModule[state.activeModule];
  }
  function filteredSelectedSeries() {
    var selected = getSelectedSet();
    return getModuleSeries().filter(function (series) {
      return selected.has(series.id) && state.controls.statuses.indexOf(series.status) >= 0;
    });
  }

  function fetchJson(url) {
    var controller = new AbortController();
    var timeout = window.setTimeout(function () { controller.abort(); }, 15000);
    return fetch(url, {
      method: "GET", headers: { Accept: "application/json" }, credentials: "same-origin",
      cache: "no-store", signal: controller.signal
    }).then(function (response) {
      return response.json().catch(function () {
        return { status: "failed", message: "服务返回了无法读取的内容" };
      }).then(function (payload) {
        window.clearTimeout(timeout);
        if (!response.ok) {
          var error = new Error(display(payload.message, "数据请求失败"));
          error.status = response.status;
          error.payload = payload;
          throw error;
        }
        return payload;
      });
    }).catch(function (error) { window.clearTimeout(timeout); throw error; });
  }

  function resample(points, frequency) {
    if (frequency === "auto") return points.slice();
    var buckets = {};
    points.forEach(function (point) {
      var date = point.dateObject;
      var key;
      if (frequency === "monthly") {
        key = date.getUTCFullYear() + "-" + String(date.getUTCMonth() + 1).padStart(2, "0");
      } else if (frequency === "weekly") {
        var thursday = new Date(date.getTime());
        var day = (thursday.getUTCDay() + 6) % 7;
        thursday.setUTCDate(thursday.getUTCDate() - day + 3);
        var firstThursday = new Date(Date.UTC(thursday.getUTCFullYear(), 0, 4));
        var week = 1 + Math.round((thursday - firstThursday) / 604800000);
        key = thursday.getUTCFullYear() + "-W" + String(week).padStart(2, "0");
      } else {
        key = dateLabel(date);
      }
      buckets[key] = point;
    });
    return Object.keys(buckets).map(function (key) { return buckets[key]; }).sort(function (a, b) { return a.time - b.time; });
  }
  function windowStart(points, windowCode) {
    if (!points.length || windowCode === "all") return null;
    var end = new Date(points[points.length - 1].time);
    var start = new Date(end.getTime());
    if (windowCode === "ytd") return Date.UTC(end.getUTCFullYear(), 0, 1);
    var months = { "1m": 1, "3m": 3, "6m": 6, "1y": 12, "3y": 36 }[windowCode] || 12;
    start.setUTCMonth(start.getUTCMonth() - months);
    return start.getTime();
  }
  function applyWindow(points, windowCode) {
    var start = windowStart(points, windowCode);
    return start === null ? points.slice() : points.filter(function (point) { return point.time >= start; });
  }
  function previousAtOrBefore(points, target) {
    var low = 0;
    var high = points.length - 1;
    var found = null;
    while (low <= high) {
      var middle = Math.floor((low + high) / 2);
      if (points[middle].time <= target) { found = points[middle]; low = middle + 1; } else { high = middle - 1; }
    }
    return found;
  }
  function transformPoints(points, transform, frequency) {
    if (transform === "raw") return points.map(function (point) { return Object.assign({}, point); });
    if (transform === "mom") {
      return points.map(function (point, index) {
        if (!index || points[index - 1].value === null || points[index - 1].value === 0 || point.value === null) return null;
        return Object.assign({}, point, { value: (point.value / points[index - 1].value - 1) * 100 });
      }).filter(Boolean);
    }
    if (transform === "yoy") {
      return points.map(function (point) {
        if (point.value === null) return null;
        var target = new Date(point.time);
        target.setUTCFullYear(target.getUTCFullYear() - 1);
        var prior = previousAtOrBefore(points, target.getTime());
        if (!prior || prior.value === null || prior.value === 0 || target.getTime() - prior.time > 45 * 86400000) return null;
        return Object.assign({}, point, { value: (point.value / prior.value - 1) * 100 });
      }).filter(Boolean);
    }
    if (transform === "zscore") {
      var length = frequency === "monthly" ? 36 : frequency === "weekly" ? 52 : 60;
      var minimum = Math.max(5, Math.floor(length * .2));
      return points.map(function (point, index) {
        var windowPoints = points.slice(Math.max(0, index - length + 1), index + 1)
          .map(function (item) { return item.value; }).filter(function (value) { return value !== null; });
        if (point.value === null || windowPoints.length < minimum) return null;
        var mean = windowPoints.reduce(function (sum, value) { return sum + value; }, 0) / windowPoints.length;
        var variance = windowPoints.reduce(function (sum, value) { return sum + Math.pow(value - mean, 2); }, 0) / windowPoints.length;
        var deviation = Math.sqrt(variance);
        if (!deviation) return null;
        return Object.assign({}, point, { value: (point.value - mean) / deviation });
      }).filter(Boolean);
    }
    if (transform === "rebased") {
      var basePoint = points.find(function (point) { return point.value !== null && point.value !== 0; });
      if (!basePoint) return [];
      return points.filter(function (point) { return point.value !== null; })
        .map(function (point) { return Object.assign({}, point, { value: point.value / basePoint.value * 100 }); });
    }
    return points;
  }
  function relativeStrength(seriesPoints, benchmarkPoints) {
    var valid = [];
    seriesPoints.forEach(function (point) {
      if (point.value === null) return;
      var benchmark = previousAtOrBefore(benchmarkPoints, point.time);
      if (!benchmark || benchmark.value === null || benchmark.value === 0 || point.time - benchmark.time > 45 * 86400000) return;
      valid.push(Object.assign({}, point, { ratio: point.value / benchmark.value }));
    });
    if (!valid.length || !valid[0].ratio) return [];
    var base = valid[0].ratio;
    return valid.map(function (point) {
      var result = Object.assign({}, point, { value: point.ratio / base * 100 });
      delete result.ratio;
      return result;
    });
  }
  function processSeries(series, benchmark) {
    var points = resample(cleanPoints(series.data), state.controls.frequency);
    var result;
    if (benchmark) {
      var benchmarkPoints = applyWindow(resample(cleanPoints(benchmark.data), state.controls.frequency), state.controls.window);
      result = relativeStrength(applyWindow(points, state.controls.window), benchmarkPoints);
    } else if (state.controls.transform === "raw") {
      result = applyWindow(points, state.controls.window);
    } else if (state.controls.transform === "rebased") {
      result = transformPoints(applyWindow(points, state.controls.window), "rebased", state.controls.frequency);
    } else {
      result = applyWindow(transformPoints(points, state.controls.transform, state.controls.frequency), state.controls.window);
    }
    return Object.assign({}, series, { points: result });
  }
  function effectiveUnit(series, benchmark) {
    if (benchmark) return "相对强弱（基期=100）";
    if (state.controls.transform === "yoy" || state.controls.transform === "mom") return "%";
    if (state.controls.transform === "zscore") return "Z-score";
    if (state.controls.transform === "rebased") return "指数（基期=100）";
    return series.unit;
  }
  function transformLabel(benchmark) {
    if (benchmark) return "相对 " + benchmark.label + "（基期=100）";
    return { raw: "原值", yoy: "同比", mom: "环比", zscore: "滚动 Z-score", rebased: "起点=100" }[state.controls.transform] || "原值";
  }
  function updateOverall(snapshot) {
    var status = safeStatus(snapshot.status);
    var badge = byId("overall-badge");
    badge.className = "status-badge status-" + status;
    byId("overall-status").textContent = statusName(status);
    byId("fresh-status").textContent = statusName(status);
    byId("as-of").textContent = display(snapshot.as_of);
    byId("generated-at").textContent = display(snapshot.generated_at);
    var quality = object(snapshot.quality);
    byId("fresh-quality").textContent = display(quality.status || quality.summary || quality.message, "未提供");
    byId("data-warning").classList.toggle("is-hidden", status !== "failed");
    document.querySelectorAll(".module-nav__item").forEach(function (button) {
      var module = object(object(snapshot.modules)[button.dataset.module]);
      var dot = button.querySelector(".status-dot");
      if (dot) dot.className = "status-dot status-" + safeStatus(module.status);
    });
  }
  function setActiveNavigation(key) {
    document.querySelectorAll("[data-module]").forEach(function (button) {
      var active = button.dataset.module === key;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }
  function renderAlerts(alerts) {
    var container = byId("alerts");
    clearNode(container);
    array(alerts).forEach(function (rawAlert) {
      var alert = object(rawAlert);
      if (!alert.message) return;
      var level = ["info", "success", "warning", "error"].indexOf(alert.level) >= 0 ? alert.level : "info";
      var row = create("div", "alert alert-" + level);
      row.appendChild(create("span", "", alert.message));
      var meta = [alert.source, alert.as_of].filter(Boolean).join(" · ");
      if (meta) row.appendChild(create("small", "", meta));
      container.appendChild(row);
    });
  }
  function isNegative(value) {
    if (typeof value === "number") return value < 0;
    return typeof value === "string" && /^\s*[-−]/.test(value);
  }
  function renderKpis(kpis) {
    var container = byId("kpi-grid");
    clearNode(container);
    var visible = array(kpis).filter(function (raw) {
      return state.controls.statuses.indexOf(safeStatus(object(raw).status)) >= 0;
    });
    visible.forEach(function (rawKpi) {
      var kpi = object(rawKpi);
      var status = safeStatus(kpi.status);
      var card = create("article", "kpi-card status-" + status);
      card.appendChild(create("div", "kpi-label", display(kpi.label, "未命名指标")));
      var displayValue = display(kpi.display);
      var value = create("div", "kpi-value", displayValue);
      if (kpi.unit && displayValue.indexOf(String(kpi.unit)) < 0) {
        value.appendChild(create("span", "kpi-unit", kpi.unit));
      }
      card.appendChild(value);
      var meta = create("div", "kpi-meta");
      var changeClass = "kpi-change";
      if (isNegative(kpi.change !== null && kpi.change !== undefined ? kpi.change : kpi.change_display)) changeClass += " is-negative";
      else if (!kpi.change_display || kpi.change_display === "—" || status !== "ok") changeClass += " is-neutral";
      meta.appendChild(create("span", changeClass, display(kpi.change_display, "—")));
      meta.appendChild(create("span", "", display(kpi.as_of, statusName(status))));
      card.appendChild(meta);
      card.title = "状态：" + statusName(status) + "；来源：" + display(kpi.source, "未提供");
      container.appendChild(card);
    });
    byId("kpi-count").textContent = visible.length + " / " + array(kpis).length + " 项";
  }

  function aggregateStatus(series) {
    if (!series.length) return "unavailable";
    var statuses = series.map(function (item) { return safeStatus(item.status); });
    if (statuses.every(function (status) { return status === "failed" || status === "unavailable"; })) return "failed";
    if (statuses.indexOf("stale") >= 0) return "stale";
    if (statuses.some(function (status) { return status !== "ok"; })) return "partial";
    return "ok";
  }
  function panelState(status) {
    var safe = safeStatus(status);
    return create("span", "panel-state status-" + safe, statusName(safe));
  }
  function uniqueText(values) {
    return Array.from(new Set(values.filter(function (value) { return value && value !== "未提供" && value !== "暂无数据"; }))).join("；");
  }
  function legacyChartTraces(chart) {
    var kind = String(chart.kind || "line").toLowerCase();
    var series = array(chart.series);
    if (kind.indexOf("candle") >= 0) {
      var candleSeries = object(series[0]);
      var candlePoints = cleanPoints(candleSeries.data).filter(function (point) {
        return point.open !== null && point.high !== null && point.low !== null && point.close !== null;
      });
      if (!candlePoints.length) return [];
      return [{
        type: "candlestick", name: display(candleSeries.name, "OHLC"),
        x: candlePoints.map(function (point) { return point.date; }),
        open: candlePoints.map(function (point) { return point.open; }),
        high: candlePoints.map(function (point) { return point.high; }),
        low: candlePoints.map(function (point) { return point.low; }),
        close: candlePoints.map(function (point) { return point.close; }),
        increasing: { line: { color: "#c00000" } }, decreasing: { line: { color: "#168a47" } }
      }];
    }
    if (kind.indexOf("heat") >= 0) {
      var dates = [];
      series.forEach(function (raw) {
        cleanPoints(object(raw).data).forEach(function (point) { if (dates.indexOf(point.date) < 0) dates.push(point.date); });
      });
      dates.sort();
      var z = series.map(function (raw) {
        var lookup = {};
        cleanPoints(object(raw).data).forEach(function (point) { lookup[point.date] = point.value; });
        return dates.map(function (date) { return Object.prototype.hasOwnProperty.call(lookup, date) ? lookup[date] : null; });
      });
      if (!dates.length || !z.length) return [];
      return [{
        type: "heatmap", x: dates,
        y: series.map(function (raw, index) { return display(object(raw).name, "序列 " + (index + 1)); }),
        z: z, zmid: 0, colorscale: [[0, "#2f75b5"], [.5, "#f2f4f7"], [1, "#c00000"]],
        hoverongaps: false, colorbar: { thickness: 9, len: .72, outlinewidth: 0, tickfont: { size: 9 } }
      }];
    }
    return series.map(function (raw, index) {
      var item = object(raw);
      var points = cleanPoints(item.data).filter(function (point) { return point.value !== null; });
      if (!points.length) return null;
      var isBar = kind.indexOf("bar") >= 0 || kind.indexOf("column") >= 0;
      var isScatter = kind === "scatter";
      var trace = {
        type: isBar ? "bar" : "scatter", name: display(item.name, "序列 " + (index + 1)),
        x: points.map(function (point) { return point.date; }),
        y: points.map(function (point) { return point.value; }),
        hovertemplate: "%{x}<br>%{y:.4g}<extra>%{fullData.name}</extra>",
        opacity: safeStatus(item.status || chart.status) === "stale" ? .55 : 1
      };
      if (isBar) trace.marker = { color: COLORS[index % COLORS.length] };
      else {
        trace.mode = isScatter ? "markers" : "lines";
        trace.line = { color: COLORS[index % COLORS.length], width: CHART_STYLE.lineWidth };
        trace.marker = { color: COLORS[index % COLORS.length], size: isScatter ? 6 : 3 };
        trace.connectgaps = false;
      }
      return trace;
    }).filter(Boolean);
  }
  function chartLayout(unit, extra) {
    var layout = {
      autosize: true, paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "#ffffff",
      margin: { l: 52, r: 22, t: 10, b: 58 },
      font: { family: "Arial, KaiTi, Microsoft YaHei, sans-serif", size: CHART_STYLE.fontSize, color: "#111111" },
      hoverlabel: { bgcolor: "#ffffff", bordercolor: "#d9e1e8", font: { size: 10 } },
      hovermode: "x unified", dragmode: "zoom", showlegend: true,
      legend: { orientation: "h", x: 0, y: -.22, xanchor: "left", yanchor: "top", font: { size: CHART_STYLE.fontSize, color: "#667085" } },
      xaxis: {
        automargin: true, showgrid: false, showline: true, linecolor: "#111111", linewidth: .8,
        ticks: "outside", ticklen: 3, tickcolor: "#111111", nticks: CHART_STYLE.axisTicks, fixedrange: false, rangeslider: { visible: false }
      },
      yaxis: {
        automargin: true, title: { text: unit || "", font: { size: 9 } }, showgrid: false,
        zeroline: true, zerolinecolor: "#BFBFBF", zerolinewidth: CHART_STYLE.referenceLineWidth, showline: true,
        linecolor: "#111111", linewidth: .8, ticks: "outside", ticklen: 3, tickcolor: "#111111", nticks: CHART_STYLE.axisTicks, fixedrange: false
      },
      barmode: "group", bargap: .28
    };
    return Object.assign(layout, object(extra));
  }
  function plotConfig(title) {
    return {
      responsive: true, displayModeBar: true, displaylogo: false, scrollZoom: true, showTips: true,
      toImageButtonOptions: { format: "png", filename: String(title || "chart").replace(/[^\w\u4e00-\u9fff-]+/g, "_"), scale: 2 },
      modeBarButtonsToRemove: ["lasso2d", "select2d"]
    };
  }
  function purgeNode(node) {
    if (window.Plotly && typeof window.Plotly.purge === "function") {
      try { window.Plotly.purge(node); } catch (_error) { /* node already removed */ }
    }
    state.plotNodes = state.plotNodes.filter(function (item) { return item !== node; });
  }
  function purgeWithin(container) {
    state.plotNodes.slice().forEach(function (node) {
      if (!container || container.contains(node) || !document.body.contains(node)) purgeNode(node);
    });
  }
  function renderChartFallback(frame, message, unavailableLabels) {
    clearNode(frame);
    var wrapper = create("div", "chart-fallback");
    var inner = create("div");
    inner.appendChild(create("strong", "", message || "暂无可绘制数据"));
    var labels = array(unavailableLabels);
    if (labels.length) {
      var list = create("ul", "chart-fallback__list");
      labels.forEach(function (label) { list.appendChild(create("li", "", label)); });
      inner.appendChild(list);
    }
    wrapper.appendChild(inner);
    frame.appendChild(wrapper);
  }
  function renderPlot(frame, traces, layout, title, emptyMessage, unavailableLabels) {
    if (!array(traces).length) {
      renderChartFallback(frame, emptyMessage || "暂无可绘制数值；缺失状态已保留", unavailableLabels);
      return;
    }
    var ready = window.dashboardPlotlyReady || Promise.resolve(window.Plotly || null);
    ready.then(function (Plotly) {
      if (!document.body.contains(frame)) return;
      if (!Plotly || typeof Plotly.newPlot !== "function") {
        renderChartFallback(frame, "图表组件不可用；可使用 CSV 导出读取原始数据", unavailableLabels);
        return;
      }
      return Plotly.newPlot(frame, traces, layout, plotConfig(title)).then(function () {
        state.plotNodes.push(frame);
      });
    }).catch(function () {
      if (document.body.contains(frame)) renderChartFallback(frame, "图表渲染失败；原始缺失状态未被替换", unavailableLabels);
    });
  }
  function chartPanel(options) {
    var opts = object(options);
    var panel = create("article", "chart-panel" + (opts.wide ? " chart-panel--wide" : ""));
    var header = create("div", "panel-header");
    var heading = create("div");
    heading.appendChild(create("h3", "", display(opts.title, "未命名图表")));
    heading.appendChild(create("p", "panel-unit", "单位：" + display(opts.unit, "未标单位")));
    header.appendChild(heading);
    header.appendChild(panelState(opts.status));
    panel.appendChild(header);
    var frame = create("div", "plot-frame");
    frame.setAttribute("role", "img");
    frame.setAttribute("aria-label", display(opts.title, "数据图表"));
    panel.appendChild(frame);
    renderPlot(frame, opts.traces, opts.layout || chartLayout(opts.unit), opts.title, opts.emptyMessage, opts.unavailableLabels);
    var source = create("div", "panel-source");
    source.appendChild(create("span", "", "数据源：" + display(opts.source, "未提供")));
    source.appendChild(create("span", "", display(opts.asOf)));
    panel.appendChild(source);
    return panel;
  }
  function renderOverviewCharts(charts) {
    var container = byId("chart-grid");
    purgeWithin(container);
    clearNode(container);
    array(charts).forEach(function (rawChart) {
      var chart = object(rawChart);
      var traces = legacyChartTraces(chart);
      var candleMissing = String(chart.kind || "").toLowerCase().indexOf("candle") >= 0 && !traces.length;
      container.appendChild(chartPanel({
        title: chart.title, unit: chart.unit, status: chart.status, source: chart.source,
        asOf: chart.as_of || activeModuleData().as_of || state.snapshot.as_of,
        traces: traces, layout: chartLayout(chart.unit),
        emptyMessage: candleMissing ? "K线不可用：该序列未提供完整 open / high / low / close 字段" : "暂无可绘制数值；缺失状态已保留"
      }));
    });
    byId("chart-count").textContent = array(charts).length + " 张";
  }
  function benchmarkSeries() {
    if (!state.controls.benchmark) return null;
    return getModuleSeries().find(function (series) { return series.id === state.controls.benchmark; }) || null;
  }
  function groupedProcessedSeries() {
    var benchmark = benchmarkSeries();
    var processed = filteredSelectedSeries().map(function (series) { return processSeries(series, benchmark); });
    var groups = {};
    processed.forEach(function (series) {
      var unit = effectiveUnit(series, benchmark);
      if (!groups[unit]) groups[unit] = [];
      groups[unit].push(series);
    });
    return { benchmark: benchmark, processed: processed, groups: groups };
  }
  function customTimeTraces(series, kind) {
    if (kind === "candlestick") {
      return series.map(function (item) {
        var points = item.points.filter(function (point) {
          return point.open !== null && point.high !== null && point.low !== null && point.close !== null;
        });
        if (!points.length) return null;
        return {
          type: "candlestick", name: item.label, x: points.map(function (point) { return point.date; }),
          open: points.map(function (point) { return point.open; }), high: points.map(function (point) { return point.high; }),
          low: points.map(function (point) { return point.low; }), close: points.map(function (point) { return point.close; }),
          opacity: item.status === "stale" ? .55 : 1,
          increasing: { line: { color: "#c00000" } }, decreasing: { line: { color: "#168a47" } }
        };
      }).filter(Boolean);
    }
    if (kind === "heatmap") {
      var dates = [];
      series.forEach(function (item) { item.points.forEach(function (point) { if (dates.indexOf(point.date) < 0) dates.push(point.date); }); });
      dates.sort();
      if (!dates.length) return [];
      return [{
        type: "heatmap", x: dates, y: series.map(function (item) { return item.label; }),
        z: series.map(function (item) {
          var values = {};
          item.points.forEach(function (point) { values[point.date] = point.value; });
          return dates.map(function (date) { return Object.prototype.hasOwnProperty.call(values, date) ? values[date] : null; });
        }),
        zmid: 0, colorscale: [[0, "#2f75b5"], [.5, "#f2f4f7"], [1, "#c00000"]],
        hoverongaps: false, colorbar: { thickness: 9, len: .72, outlinewidth: 0 }
      }];
    }
    return series.map(function (item, index) {
      var points = item.points.filter(function (point) { return point.value !== null; });
      if (!points.length) return null;
      var isBar = kind === "bar";
      var isScatter = kind === "scatter";
      var trace = {
        type: isBar ? "bar" : "scatter", name: item.label,
        x: points.map(function (point) { return point.date; }),
        y: points.map(function (point) { return point.value; }),
        opacity: item.status === "stale" ? .55 : 1,
        hovertemplate: "%{x}<br>%{y:.4g}<extra>%{fullData.name}</extra>"
      };
      if (isBar) trace.marker = { color: COLORS[index % COLORS.length] };
      else {
        trace.mode = isScatter ? "markers" : "lines";
        trace.line = { color: COLORS[index % COLORS.length], width: CHART_STYLE.lineWidth };
        trace.marker = { color: COLORS[index % COLORS.length], size: isScatter ? 6 : 3 };
        trace.connectgaps = false;
      }
      return trace;
    }).filter(Boolean);
  }
  function renderTrend() {
    var container = byId("trend-grid");
    purgeWithin(container);
    clearNode(container);
    var bundle = groupedProcessedSeries();
    var selected = bundle.processed;
    var noData = selected.filter(function (series) { return !series.points.length; });
    Object.keys(bundle.groups).forEach(function (unit) {
      var group = bundle.groups[unit];
      var traces = customTimeTraces(group, state.controls.chartType);
      var unavailable = group.filter(function (series) { return !series.points.length; })
        .map(function (series) { return series.label + "（" + statusName(series.status) + "）"; });
      var candleMissing = state.controls.chartType === "candlestick" && !traces.length;
      container.appendChild(chartPanel({
        title: MODULE_LABELS[state.activeModule] + " · " + transformLabel(bundle.benchmark) + " · " + unit,
        unit: unit, status: aggregateStatus(group), source: uniqueText(group.map(function (series) { return series.source; })),
        asOf: uniqueText(group.map(function (series) { return series.as_of; })), traces: traces, wide: true,
        layout: chartLayout(unit), unavailableLabels: unavailable,
        emptyMessage: candleMissing ? "K线不可用：所选指标没有完整 OHLC 数据" : "所选指标在当前窗口没有可绘制数值"
      }));
    });
    if (!Object.keys(bundle.groups).length) {
      container.appendChild(chartPanel({
        title: "多指标趋势", unit: "—", status: "unavailable", source: "—", asOf: "—", traces: [], wide: true,
        emptyMessage: "没有符合指标选择与状态筛选的序列。请在“指标多选”中选择指标。"
      }));
    }
    byId("trend-count").textContent = (selected.length - noData.length) + " / " + selected.length + " 个序列";
  }
  function crossTraces(group, kind) {
    var latest = group.map(function (series) {
      var points = series.points.filter(function (point) { return point.value !== null; });
      return points.length ? { label: series.label, value: points[points.length - 1].value, date: points[points.length - 1].date, status: series.status } : null;
    }).filter(Boolean).sort(function (a, b) { return b.value - a.value; });
    if (!latest.length) return [];
    if (kind === "heatmap") {
      return [{
        type: "heatmap", x: latest.map(function (item) { return item.label; }), y: ["最新值"],
        z: [latest.map(function (item) { return item.value; })], zmid: 0,
        customdata: [latest.map(function (item) { return item.date; })],
        hovertemplate: "%{x}<br>%{z:.4g}<br>%{customdata}<extra></extra>",
        colorscale: [[0, "#2f75b5"], [.5, "#f2f4f7"], [1, "#c00000"]], colorbar: { thickness: 9, len: .72, outlinewidth: 0 }
      }];
    }
    if (kind === "scatter") {
      return [{
        type: "scatter", mode: "markers+text", x: latest.map(function (_item, index) { return index + 1; }),
        y: latest.map(function (item) { return item.value; }), text: latest.map(function (item) { return item.label; }),
        textposition: "top center", customdata: latest.map(function (item) { return item.date; }),
        marker: { color: latest.map(function (item) { return item.status === "stale" ? "#98a2b3" : "#c00000"; }), size: 8 },
        hovertemplate: "%{text}<br>%{y:.4g}<br>%{customdata}<extra></extra>", showlegend: false
      }];
    }
    return [{
      type: "bar", x: latest.map(function (item) { return item.label; }), y: latest.map(function (item) { return item.value; }),
      customdata: latest.map(function (item) { return item.date; }),
      marker: { color: latest.map(function (item) { return item.value >= 0 ? "#c00000" : "#2f75b5"; }), opacity: latest.map(function (item) { return item.status === "stale" ? .55 : 1; }) },
      hovertemplate: "%{x}<br>%{y:.4g}<br>%{customdata}<extra></extra>", showlegend: false
    }];
  }
  function renderCrossSection() {
    var container = byId("cross-grid");
    purgeWithin(container);
    clearNode(container);
    var bundle = groupedProcessedSeries();
    var count = 0;
    Object.keys(bundle.groups).forEach(function (unit) {
      var group = bundle.groups[unit];
      var kind = state.controls.chartType === "heatmap" || state.controls.chartType === "scatter" ? state.controls.chartType : "bar";
      var traces = crossTraces(group, kind);
      count += group.filter(function (series) { return series.points.some(function (point) { return point.value !== null; }); }).length;
      container.appendChild(chartPanel({
        title: MODULE_LABELS[state.activeModule] + " · 最新横截面 · " + transformLabel(bundle.benchmark),
        unit: unit, status: aggregateStatus(group), source: uniqueText(group.map(function (series) { return series.source; })),
        asOf: uniqueText(group.map(function (series) { return series.as_of; })), traces: traces, wide: true,
        layout: chartLayout(unit, { hovermode: "closest" }),
        emptyMessage: "所选指标在当前窗口没有可比较的最新数值"
      }));
    });
    if (!Object.keys(bundle.groups).length) {
      container.appendChild(chartPanel({
        title: "最新横截面", unit: "—", status: "unavailable", source: "—", asOf: "—", traces: [], wide: true,
        emptyMessage: "没有符合指标选择与状态筛选的序列。"
      }));
    }
    byId("cross-count").textContent = count + " 个指标";
  }
  function resolveColumns(table) {
    var columns = array(table.columns).map(function (column) {
      if (typeof column === "string") return { key: column, label: column };
      var raw = object(column);
      return { key: display(raw.key, ""), label: display(raw.label || raw.key, "未命名字段") };
    }).filter(function (column) { return column.key; });
    if (columns.length) return columns;
    var first = array(table.rows)[0];
    if (first && typeof first === "object" && !Array.isArray(first)) {
      return Object.keys(first).map(function (key) { return { key: key, label: key }; });
    }
    if (Array.isArray(first)) {
      return first.map(function (_value, index) { return { key: String(index), label: "字段 " + (index + 1) }; });
    }
    return [];
  }
  function rawCell(row, column, index) {
    if (Array.isArray(row)) return row[index];
    return object(row)[column.key];
  }
  function comparable(value) {
    var numeric = finite(value);
    if (numeric !== null) return { type: "number", value: numeric };
    var date = parseDate(value);
    if (date && /^\d{4}[-/]?\d{2}/.test(String(value))) return { type: "number", value: date.getTime() };
    return { type: "text", value: String(value === null || value === undefined ? "" : value).toLocaleLowerCase("zh-CN") };
  }
  function csvCell(value) {
    if (value === null || value === undefined) return "";
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
    var text = String(value).replace(/\r?\n/g, " ");
    if (/^[=+@]/.test(text) || (/^-/.test(text) && finite(text) === null)) text = "'" + text;
    return '"' + text.replace(/"/g, '""') + '"';
  }
  function downloadCsv(filename, columns, rows) {
    var csv = [columns.map(function (column) { return csvCell(column.label); }).join(",")];
    rows.forEach(function (row) {
      csv.push(columns.map(function (column, index) { return csvCell(rawCell(row, column, index)); }).join(","));
    });
    var blob = new Blob(["\ufeff" + csv.join("\r\n")], { type: "text/csv;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = String(filename || "dashboard.csv").replace(/[\\/:*?"<>|]+/g, "_");
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }
  function renderTablePanel(rawTable, target) {
    var table = object(rawTable);
    var module = activeModuleData();
    var snapshot = object(state.snapshot);
    var tableSource = table.source || module.source || snapshot.source;
    var tableAsOf = table.as_of || module.as_of || snapshot.as_of;
    var columns = resolveColumns(table);
    var rows = array(table.rows);
    var tableKey = state.activeModule + "::" + display(table.id, table.title || "table");
    if (!state.tableStates[tableKey]) {
      state.tableStates[tableKey] = { search: "", sortKey: "", sortDirection: 1, page: 1, pageSize: 25 };
    }
    var tableState = state.tableStates[tableKey];
    var panel = create("article", "table-panel");
    var header = create("div", "panel-header");
    var heading = create("div");
    heading.appendChild(create("h3", "", display(table.title, "未命名数据表")));
    heading.appendChild(create("p", "panel-unit", "数据源：" + display(tableSource, "未提供")));
    header.appendChild(heading);
    header.appendChild(panelState(table.status));
    panel.appendChild(header);

    if (!rows.length || !columns.length) {
      panel.appendChild(create("div", "table-empty", "暂无可展示明细；缺失状态已保留"));
      var emptySource = create("div", "panel-source");
      emptySource.appendChild(create("span", "", "0 行"));
      emptySource.appendChild(create("span", "", display(tableAsOf)));
      panel.appendChild(emptySource);
      target.appendChild(panel);
      return;
    }

    var tools = create("div", "table-tools");
    var search = create("input");
    search.type = "search";
    search.placeholder = "搜索全部字段";
    search.value = tableState.search;
    search.setAttribute("aria-label", "搜索 " + display(table.title, "数据表"));
    var pageSize = create("select");
    pageSize.setAttribute("aria-label", "每页行数");
    [[25, "25 行"], [50, "50 行"], [100, "100 行"], [0, "全部"]].forEach(function (optionData) {
      var option = create("option", "", optionData[1]);
      option.value = String(optionData[0]);
      option.selected = tableState.pageSize === optionData[0];
      pageSize.appendChild(option);
    });
    var exportButton = create("button", "", "导出 CSV");
    exportButton.type = "button";
    tools.appendChild(search);
    tools.appendChild(pageSize);
    tools.appendChild(exportButton);
    panel.appendChild(tools);

    var scroll = create("div", "table-scroll");
    var pager = create("div", "table-pager");
    panel.appendChild(scroll);
    panel.appendChild(pager);

    function filteredRows() {
      var query = tableState.search.trim().toLocaleLowerCase("zh-CN");
      var filtered = !query ? rows.slice() : rows.filter(function (row) {
        return columns.some(function (column, index) {
          var value = rawCell(row, column, index);
          return String(value === null || value === undefined ? "" : value).toLocaleLowerCase("zh-CN").indexOf(query) >= 0;
        });
      });
      if (tableState.sortKey) {
        var columnIndex = columns.findIndex(function (column) { return column.key === tableState.sortKey; });
        var column = columns[columnIndex];
        filtered = filtered.map(function (row, index) { return { row: row, index: index }; }).sort(function (left, right) {
          var a = comparable(rawCell(left.row, column, columnIndex));
          var b = comparable(rawCell(right.row, column, columnIndex));
          var result = a.value < b.value ? -1 : a.value > b.value ? 1 : left.index - right.index;
          return result * tableState.sortDirection;
        }).map(function (item) { return item.row; });
      }
      return filtered;
    }
    function draw() {
      clearNode(scroll);
      clearNode(pager);
      var filtered = filteredRows();
      var size = tableState.pageSize || Math.max(filtered.length, 1);
      var pages = Math.max(1, Math.ceil(filtered.length / size));
      tableState.page = Math.min(Math.max(1, tableState.page), pages);
      var start = (tableState.page - 1) * size;
      var pageRows = tableState.pageSize === 0 ? filtered : filtered.slice(start, start + size);

      var element = create("table", "data-table");
      var thead = create("thead");
      var trHead = create("tr");
      columns.forEach(function (column) {
        var th = create("th");
        var sort = create("button", "", column.label + (tableState.sortKey === column.key ? (tableState.sortDirection > 0 ? " ↑" : " ↓") : ""));
        sort.type = "button";
        sort.addEventListener("click", function () {
          if (tableState.sortKey === column.key) tableState.sortDirection *= -1;
          else { tableState.sortKey = column.key; tableState.sortDirection = 1; }
          tableState.page = 1;
          draw();
        });
        th.appendChild(sort);
        trHead.appendChild(th);
      });
      thead.appendChild(trHead);
      element.appendChild(thead);
      var tbody = create("tbody");
      pageRows.forEach(function (row) {
        var tr = create("tr");
        columns.forEach(function (column, index) {
          var value = rawCell(row, column, index);
          var td = create("td", "", display(value));
          td.title = display(value);
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
      element.appendChild(tbody);
      scroll.appendChild(element);

      var previous = create("button", "", "上一页");
      previous.type = "button";
      previous.disabled = tableState.page <= 1;
      previous.addEventListener("click", function () { tableState.page -= 1; draw(); });
      var next = create("button", "", "下一页");
      next.type = "button";
      next.disabled = tableState.page >= pages;
      next.addEventListener("click", function () { tableState.page += 1; draw(); });
      pager.appendChild(create("span", "", "共 " + filtered.length + " 行 · 第 " + tableState.page + " / " + pages + " 页"));
      pager.appendChild(previous);
      pager.appendChild(next);
    }
    search.addEventListener("input", function () { tableState.search = search.value; tableState.page = 1; draw(); });
    pageSize.addEventListener("change", function () { tableState.pageSize = Number(pageSize.value); tableState.page = 1; draw(); });
    exportButton.addEventListener("click", function () {
      downloadCsv(display(table.title, "数据表") + ".csv", columns, filteredRows());
    });
    draw();

    var source = create("div", "panel-source");
    source.appendChild(create("span", "", "支持全字段搜索、排序、分页与完整导出"));
    source.appendChild(create("span", "", display(tableAsOf)));
    panel.appendChild(source);
    target.appendChild(panel);
  }
  function renderTables(tables, targetId) {
    var target = byId(targetId);
    clearNode(target);
    var visible = array(tables).filter(function (table) {
      return state.controls.statuses.indexOf(safeStatus(object(table).status)) >= 0;
    });
    if (!visible.length) {
      var fallback = {
        id: "no-details", title: "明细数据", status: "unavailable", source: "—", as_of: activeModuleData().as_of,
        columns: [], rows: []
      };
      renderTablePanel(fallback, target);
    } else {
      visible.forEach(function (table) { renderTablePanel(table, target); });
    }
    byId("table-count").textContent = visible.length + " / " + array(tables).length + " 张";
  }
  function coverageStats(series) {
    var stats = { total: series.length, withData: 0, ok: 0, partial: 0, stale: 0, warning: 0, unavailable: 0, failed: 0, unknown: 0 };
    series.forEach(function (item) {
      stats[safeStatus(item.status)] += 1;
      if (hasNumericData(item)) stats.withData += 1;
    });
    stats.coverage = stats.total ? stats.withData / stats.total : 0;
    return stats;
  }
  function renderQuality() {
    var series = getModuleSeries();
    var stats = coverageStats(series);
    var summary = byId("quality-summary");
    clearNode(summary);
    [
      ["指标总数", stats.total], ["有观测值", stats.withData], ["覆盖率", (stats.coverage * 100).toFixed(1) + "%"],
      ["正常", stats.ok], ["滞后 / 待复核", stats.stale + stats.warning], ["不可用 / 失败", stats.unavailable + stats.failed]
    ].forEach(function (item) {
      var card = create("article", "quality-card");
      card.appendChild(create("span", "", item[0]));
      card.appendChild(create("strong", "", item[1]));
      summary.appendChild(card);
    });
    var target = byId("quality-table");
    clearNode(target);
    var qualityRows = series.map(function (item) {
      return {
        id: item.id, indicator: item.label, submodule: item.submodule, status: statusName(item.status),
        observations: cleanPoints(item.data).filter(function (point) { return point.value !== null; }).length,
        frequency: item.frequency, unit: item.unit, as_of: item.as_of, source: item.source
      };
    });
    renderTablePanel({
      id: "quality-indicators", title: "全量指标质量清单", status: aggregateStatus(series),
      source: "运行时指标合同", as_of: activeModuleData().as_of,
      columns: [
        { key: "indicator", label: "指标" }, { key: "submodule", label: "子模块" },
        { key: "status", label: "状态" }, { key: "observations", label: "观测数" },
        { key: "frequency", label: "频率" }, { key: "unit", label: "单位" },
        { key: "as_of", label: "数据日期" }, { key: "source", label: "数据源" }, { key: "id", label: "指标ID" }
      ],
      rows: qualityRows
    }, target);
    byId("quality-count").textContent = series.length + " 项";
  }

  function populateIndicatorPicker() {
    var container = byId("indicator-options");
    clearNode(container);
    var allSeries = getModuleSeries();
    var selected = getSelectedSet();
    var groups = {};
    allSeries.forEach(function (series) {
      if (!groups[series.submodule]) groups[series.submodule] = [];
      groups[series.submodule].push(series);
    });
    Object.keys(groups).sort(function (a, b) { return a.localeCompare(b, "zh-CN"); }).forEach(function (groupName) {
      var group = create("section", "indicator-group");
      group.appendChild(create("h3", "indicator-group__title", groupName + "（" + groups[groupName].length + "）"));
      groups[groupName].forEach(function (series) {
        var label = create("label", "indicator-option");
        label.dataset.search = [series.label, series.submodule, series.source, series.unit, series.frequency, series.id].join(" ").toLocaleLowerCase("zh-CN");
        var input = create("input");
        input.type = "checkbox";
        input.value = series.id;
        input.checked = selected.has(series.id);
        input.addEventListener("change", function () {
          if (input.checked) selected.add(series.id); else selected.delete(series.id);
          updateIndicatorSummary();
          queueDependentRender();
        });
        label.appendChild(input);
        label.appendChild(create("span", "indicator-option__label", series.label));
        label.appendChild(create("span", "indicator-option__meta", series.frequency + " · " + series.unit));
        label.appendChild(create("span", "mini-status status-" + series.status, statusName(series.status)));
        group.appendChild(label);
      });
      container.appendChild(group);
    });
    applyIndicatorSearch();
    updateIndicatorSummary();
    populateBenchmark();
  }
  function applyIndicatorSearch() {
    var query = byId("indicator-search").value.trim().toLocaleLowerCase("zh-CN");
    document.querySelectorAll(".indicator-option").forEach(function (option) {
      option.classList.toggle("is-hidden", Boolean(query) && option.dataset.search.indexOf(query) < 0);
    });
    document.querySelectorAll(".indicator-group").forEach(function (group) {
      group.classList.toggle("is-hidden", !group.querySelector(".indicator-option:not(.is-hidden)"));
    });
  }
  function updateIndicatorSummary() {
    var total = getModuleSeries().length;
    var selected = getSelectedSet().size;
    byId("indicator-summary").textContent = "已选 " + selected + " / " + total + " 项";
  }
  function populateBenchmark() {
    var select = byId("benchmark-select");
    var current = state.controls.benchmark;
    clearNode(select);
    var none = create("option", "", "不使用基准");
    none.value = "";
    select.appendChild(none);
    getModuleSeries().filter(hasNumericData).forEach(function (series) {
      var option = create("option", "", series.label + " · " + series.unit);
      option.value = series.id;
      select.appendChild(option);
    });
    if (Array.from(select.options).some(function (option) { return option.value === current; })) select.value = current;
    else { select.value = ""; state.controls.benchmark = ""; }
  }
  function catalogStats(moduleKey) {
    var stats = { total: 0, live: 0, stale: 0, unavailable: 0, metadata_only: 0, other: 0 };
    array(object(state.snapshot).catalog_status).forEach(function (raw) {
      var row = object(raw);
      if (moduleKey && row.module !== moduleKey) return;
      var status = String(row.status || "unknown").toLowerCase().replace(/-/g, "_");
      stats.total += 1;
      if (status === "live" || status === "ok") stats.live += 1;
      else if (status === "stale") stats.stale += 1;
      else if (status === "metadata_only") stats.metadata_only += 1;
      else if (status === "unavailable" || status === "failed") stats.unavailable += 1;
      else stats.other += 1;
    });
    return stats;
  }
  function updateModuleCoverage(module) {
    var series = moduleSeries(module);
    var stats = coverageStats(series);
    var catalog = catalogStats(state.activeModule);
    var globalCatalog = catalogStats("");
    byId("fresh-coverage").textContent = stats.total ? stats.withData + " / " + stats.total + "（" + (stats.coverage * 100).toFixed(1) + "%）" : "暂无指标合同";
    byId("fresh-breakdown").textContent = (stats.ok + stats.partial) + " / " + (stats.stale + stats.warning) + " / " + (stats.failed + stats.unavailable);
    byId("fresh-catalog").textContent = catalog.total ? catalog.live + " / " + catalog.total + "（全局 " + globalCatalog.live + " / " + globalCatalog.total + "）" : "目录无本模块字段";
    byId("fresh-catalog-breakdown").textContent = catalog.live + " / " + catalog.stale + " / " + catalog.unavailable + " / " + catalog.metadata_only + (catalog.other ? " · 其他 " + catalog.other : "");
  }
  function updateControlNote() {
    var benchmark = benchmarkSeries();
    var note = "窗口 " + state.controls.window.toUpperCase() + " · " +
      display(byId("frequency-select").selectedOptions[0] && byId("frequency-select").selectedOptions[0].textContent, "原频率") + " · " +
      transformLabel(benchmark) + "；不同单位自动分面，缺失值断线，不补零。";
    if (benchmark) note += " 启用基准时统一转换为相对强弱，其他变换不叠加。";
    if (state.controls.chartType === "candlestick") note += " K线仅在完整 OHLC 字段存在时绘制。";
    byId("control-note").textContent = note;
  }
  function syncControlDom() {
    document.querySelectorAll("#window-buttons [data-window]").forEach(function (button) {
      button.classList.toggle("is-active", button.dataset.window === state.controls.window);
    });
    byId("frequency-select").value = state.controls.frequency;
    byId("transform-select").value = state.controls.transform;
    byId("chart-type-select").value = state.controls.chartType;
    document.querySelectorAll("#status-filters input").forEach(function (input) {
      input.checked = state.controls.statuses.indexOf(input.value) >= 0;
    });
    populateIndicatorPicker();
    byId("benchmark-select").value = state.controls.benchmark;
    updateControlNote();
  }
  function setActiveView(view, updateUrl) {
    if (VIEW_KEYS.indexOf(view) < 0) view = "overview";
    state.activeView = view;
    document.querySelectorAll(".submodule-tab").forEach(function (button) {
      var active = button.dataset.view === view;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
    document.querySelectorAll("[data-analysis-view]").forEach(function (panel) {
      panel.classList.toggle("is-hidden", panel.dataset.analysisView !== view);
    });
    renderActiveView();
    if (updateUrl) updateUrlState();
  }
  function renderActiveView() {
    var module = activeModuleData();
    if (state.activeView === "overview") {
      renderKpis(module.kpis);
      renderOverviewCharts(array(module.charts).filter(function (chart) {
        return state.controls.statuses.indexOf(safeStatus(object(chart).status)) >= 0;
      }));
    } else if (state.activeView === "trend") {
      renderTrend();
    } else if (state.activeView === "cross_section") {
      renderCrossSection();
    } else if (state.activeView === "events") {
      renderTables(module.tables, "table-grid");
    } else if (state.activeView === "quality") {
      renderQuality();
    }
  }
  function queueDependentRender() {
    if (state.renderFrame) window.cancelAnimationFrame(state.renderFrame);
    state.renderFrame = window.requestAnimationFrame(function () {
      state.renderFrame = 0;
      updateControlNote();
      renderActiveView();
    });
  }
  function updateUrlState() {
    var url = new URL(window.location.href);
    url.searchParams.set("module", state.activeModule);
    url.searchParams.set("view", state.activeView);
    window.history.replaceState({ module: state.activeModule, view: state.activeView }, "", url);
  }
  function renderModule(key) {
    if (!state.snapshot || MODULE_ORDER.indexOf(key) < 0) return;
    state.activeModule = key;
    var module = activeModuleData();
    setActiveNavigation(key);
    byId("module-title").textContent = display(module.title, MODULE_LABELS[key]);
    byId("module-subtitle").textContent = display(module.subtitle, "该模块未提供说明");
    byId("fresh-module").textContent = MODULE_LABELS[key];
    byId("fresh-as-of").textContent = display(module.as_of || state.snapshot.as_of);
    byId("stock-search").classList.toggle("is-hidden", key !== "stock");
    renderAlerts(module.alerts);
    updateModuleCoverage(module);
    syncControlDom();
    renderActiveView();
    var hasContent = getModuleSeries().length || array(module.kpis).length || array(module.charts).length || array(module.tables).length;
    byId("module-empty").classList.toggle("is-hidden", Boolean(hasContent));
  }
  function activateModule(key, updateUrl) {
    if (MODULE_ORDER.indexOf(key) < 0) return;
    state.activeModule = key;
    state.controls.benchmark = "";
    renderModule(key);
    if (updateUrl) updateUrlState();
  }

  function exportCurrentSeries() {
    var bundle = groupedProcessedSeries();
    var rows = [];
    bundle.processed.forEach(function (series) {
      series.points.forEach(function (point) {
        rows.push({
          module: MODULE_LABELS[state.activeModule], id: series.id, indicator: series.label,
          submodule: series.submodule, date: point.date, value: point.value,
          unit: effectiveUnit(series, bundle.benchmark), frequency: series.frequency,
          status: statusName(series.status), source: series.source, as_of: series.as_of
        });
      });
    });
    if (!rows.length) {
      byId("control-note").textContent = "当前筛选没有可导出的观测值；未生成空文件。";
      return;
    }
    var columns = [
      { key: "module", label: "模块" }, { key: "id", label: "指标ID" }, { key: "indicator", label: "指标" },
      { key: "submodule", label: "子模块" }, { key: "date", label: "日期" }, { key: "value", label: "数值" },
      { key: "unit", label: "单位" }, { key: "frequency", label: "原频率" }, { key: "status", label: "状态" },
      { key: "source", label: "数据源" }, { key: "as_of", label: "数据水位" }
    ];
    downloadCsv(MODULE_LABELS[state.activeModule] + "_" + state.controls.window + "_" + transformLabel(bundle.benchmark) + ".csv", columns, rows);
  }

  function loadSavedViews() {
    try {
      var parsed = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "[]");
      state.savedViews = array(parsed).filter(function (view) {
        return view && typeof view === "object" && typeof view.id === "string" && typeof view.name === "string";
      }).slice(0, 30);
    } catch (_error) { state.savedViews = []; }
  }
  function persistSavedViews() {
    try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state.savedViews)); } catch (_error) { /* storage disabled */ }
  }
  function renderSavedViewSelect(selectedId) {
    var select = byId("saved-view-select");
    clearNode(select);
    var defaultOption = create("option", "", "默认视图");
    defaultOption.value = "";
    select.appendChild(defaultOption);
    state.savedViews.forEach(function (view) {
      var option = create("option", "", view.name);
      option.value = view.id;
      select.appendChild(option);
    });
    select.value = selectedId || "";
    byId("delete-view").disabled = !select.value;
  }
  function captureView() {
    return {
      module: state.activeModule, view: state.activeView, window: state.controls.window,
      frequency: state.controls.frequency, transform: state.controls.transform,
      benchmark: state.controls.benchmark, chartType: state.controls.chartType,
      statuses: state.controls.statuses.slice(), selected: Array.from(getSelectedSet())
    };
  }
  function saveView() {
    var proposed = MODULE_LABELS[state.activeModule] + " · " + new Date().toLocaleDateString("zh-CN");
    var name = window.prompt("为当前观察组合命名（仅保存在本机浏览器）", proposed);
    if (name === null) return;
    name = name.trim().slice(0, 40);
    if (!name) { byId("control-note").textContent = "视图名称不能为空。"; return; }
    var view = { id: String(Date.now()), name: name, settings: captureView() };
    state.savedViews.push(view);
    if (state.savedViews.length > 30) state.savedViews.shift();
    persistSavedViews();
    renderSavedViewSelect(view.id);
    byId("control-note").textContent = "视图“" + name + "”已保存在本机浏览器。";
  }
  function applySavedView(id) {
    var saved = state.savedViews.find(function (view) { return view.id === id; });
    if (!saved) return;
    var settings = object(saved.settings);
    var module = MODULE_ORDER.indexOf(settings.module) >= 0 ? settings.module : "macro";
    var view = VIEW_KEYS.indexOf(settings.view) >= 0 ? settings.view : "overview";
    state.controls.window = ["1m", "3m", "6m", "ytd", "1y", "3y", "all"].indexOf(settings.window) >= 0 ? settings.window : "1y";
    state.controls.frequency = ["auto", "daily", "weekly", "monthly"].indexOf(settings.frequency) >= 0 ? settings.frequency : "auto";
    state.controls.transform = ["raw", "yoy", "mom", "zscore", "rebased"].indexOf(settings.transform) >= 0 ? settings.transform : "raw";
    state.controls.chartType = ["line", "bar", "scatter", "heatmap", "candlestick"].indexOf(settings.chartType) >= 0 ? settings.chartType : "line";
    state.controls.statuses = array(settings.statuses).filter(function (status) { return Object.prototype.hasOwnProperty.call(STATUS_LABELS, status); });
    if (!state.controls.statuses.length) state.controls.statuses = DEFAULT_STATUSES.slice();
    state.activeModule = module;
    var availableSeries = moduleSeries(activeModuleData());
    var availableIds = new Set(availableSeries.map(function (series) { return series.id; }));
    if (Array.isArray(settings.selected)) {
      state.selectedByModule[module] = new Set(settings.selected.map(String).filter(function (seriesId) {
        return availableIds.has(seriesId);
      }));
    } else {
      delete state.selectedByModule[module];
    }
    var requestedBenchmark = display(settings.benchmark, "");
    state.controls.benchmark = availableSeries.some(function (series) {
      return series.id === requestedBenchmark && hasNumericData(series);
    }) ? requestedBenchmark : "";
    renderModule(module);
    setActiveView(view, true);
    renderSavedViewSelect(id);
  }
  function deleteSavedView() {
    var id = byId("saved-view-select").value;
    if (!id) return;
    state.savedViews = state.savedViews.filter(function (view) { return view.id !== id; });
    persistSavedViews();
    renderSavedViewSelect("");
    byId("control-note").textContent = "已删除本机保存的视图。";
  }

  function bindNavigation() {
    document.querySelectorAll("[data-module]").forEach(function (button) {
      button.addEventListener("click", function () { activateModule(button.dataset.module, true); });
    });
    document.querySelectorAll(".submodule-tab").forEach(function (button) {
      button.addEventListener("click", function () { setActiveView(button.dataset.view, true); });
    });
    window.addEventListener("popstate", function () {
      state.activeModule = initialModule();
      state.activeView = initialView();
      renderModule(state.activeModule);
      setActiveView(state.activeView, false);
    });
  }
  function bindControls() {
    byId("window-buttons").addEventListener("click", function (event) {
      var button = event.target.closest("[data-window]");
      if (!button) return;
      state.controls.window = button.dataset.window;
      syncControlDom();
      queueDependentRender();
    });
    ["frequency-select", "transform-select", "chart-type-select"].forEach(function (id) {
      byId(id).addEventListener("change", function () {
        var key = id === "frequency-select" ? "frequency" : id === "transform-select" ? "transform" : "chartType";
        state.controls[key] = byId(id).value;
        queueDependentRender();
      });
    });
    byId("benchmark-select").addEventListener("change", function () {
      state.controls.benchmark = byId("benchmark-select").value;
      queueDependentRender();
    });
    byId("status-filters").addEventListener("change", function () {
      state.controls.statuses = Array.from(document.querySelectorAll("#status-filters input:checked")).map(function (input) { return input.value; });
      queueDependentRender();
    });
    byId("indicator-search").addEventListener("input", applyIndicatorSearch);
    byId("select-visible").addEventListener("click", function () {
      var selected = getSelectedSet();
      document.querySelectorAll(".indicator-option:not(.is-hidden) input").forEach(function (input) {
        input.checked = true;
        selected.add(input.value);
      });
      updateIndicatorSummary();
      queueDependentRender();
    });
    byId("clear-indicators").addEventListener("click", function () {
      getSelectedSet().clear();
      document.querySelectorAll(".indicator-option input").forEach(function (input) { input.checked = false; });
      updateIndicatorSummary();
      queueDependentRender();
    });
    byId("reset-controls").addEventListener("click", function () {
      state.controls = {
        window: "1y", frequency: "auto", transform: "raw", benchmark: "",
        chartType: "line", statuses: DEFAULT_STATUSES.slice()
      };
      delete state.selectedByModule[state.activeModule];
      byId("indicator-search").value = "";
      syncControlDom();
      renderActiveView();
    });
    byId("export-series").addEventListener("click", exportCurrentSeries);
    byId("save-view").addEventListener("click", saveView);
    byId("delete-view").addEventListener("click", deleteSavedView);
    byId("saved-view-select").addEventListener("change", function () {
      var id = byId("saved-view-select").value;
      byId("delete-view").disabled = !id;
      if (id) applySavedView(id);
    });
    document.addEventListener("click", function (event) {
      var picker = byId("indicator-picker");
      if (picker.open && !picker.contains(event.target)) picker.open = false;
    });
  }
  function renderStockResult(code, data) {
    var raw = object(data);
    var tables = array(raw.tables).slice();
    var record = object(raw.record);
    var recordKeys = Object.keys(record);
    if (recordKeys.length) {
      var detailRow = {};
      recordKeys.forEach(function (key) {
        var value = record[key];
        detailRow[key] = value && typeof value === "object" ? JSON.stringify(value) : value;
      });
      tables.unshift({
        id: "stock-record-detail", title: "证券详情", status: raw.status || "ok",
        source: raw.source || "个股快照接口", as_of: raw.as_of || state.snapshot.as_of,
        columns: recordKeys.map(function (key) { return { key: key, label: key }; }),
        rows: [detailRow]
      });
    }
    state.moduleOverrides.stock = {
      title: display(raw.title || raw.name, code + " 个股快照"),
      subtitle: "证券代码 " + code + " · 仅展示已入库数据",
      status: raw.status || "ok", as_of: raw.as_of || state.snapshot.as_of,
      kpis: array(raw.kpis), charts: array(raw.charts), tables: tables,
      alerts: array(raw.alerts), series: array(raw.series)
    };
    delete state.selectedByModule.stock;
    state.activeModule = "stock";
    state.controls.benchmark = "";
    renderModule("stock");
    updateUrlState();
  }
  function bindStockSearch() {
    var form = byId("stock-search");
    var input = byId("stock-code");
    var status = byId("stock-search-status");
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var code = input.value.trim().toUpperCase();
      if (!/^[A-Z0-9][A-Z0-9._-]{0,31}$/.test(code) || code.indexOf("..") >= 0) {
        status.textContent = "证券代码格式无效。";
        return;
      }
      status.textContent = "正在读取快照…";
      fetchJson("/api/v1/stock/" + encodeURIComponent(code)).then(function (payload) {
        status.textContent = code + " 已加载。";
        renderStockResult(code, payload.data);
      }).catch(function (error) {
        var payload = object(error.payload);
        status.textContent = display(payload.message || error.message, "该证券暂无可用数据");
      });
    });
  }
  function showLoadFailure() {
    byId("data-warning").classList.remove("is-hidden");
    byId("overall-badge").className = "status-badge status-failed";
    byId("overall-status").textContent = STATUS_LABELS.failed;
    byId("fresh-status").textContent = STATUS_LABELS.failed;
    byId("module-empty").classList.remove("is-hidden");
    renderAlerts([{ level: "error", message: "数据快照暂不可用；页面未填充任何推测值。" }]);
  }
  function initialise() {
    bindNavigation();
    bindControls();
    bindStockSearch();
    loadSavedViews();
    renderSavedViewSelect("");
    fetchJson("/api/v1/snapshot").then(function (snapshot) {
      state.snapshot = object(snapshot);
      updateOverall(state.snapshot);
      renderModule(state.activeModule);
      setActiveView(state.activeView, false);
    }).catch(showLoadFailure);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialise, { once: true });
  else initialise();
})();
