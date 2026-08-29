(function () {
  "use strict";
  const BASE = (window.APP_BOOT && window.APP_BOOT.basePath) || "",
    C = {
      red: "#c00000",
      gold: "#ffc000",
      blue: "#2f75b5",
      green: "#177245",
      grey: "#808080",
      grid: "#e8edf2",
    },
    FONT = "Arial, KaiTi, STKaiti, sans-serif";
  const LABEL = {
      benchmark: "中证500基准",
      direct_score_top50: "得分Top50",
      same_support_score_weighted: "同标的得分加权",
      constrained_optimizer: "约束优化组合",
    },
    CAT = {
      holdings: "持仓",
      industry: "行业",
      style: "风格",
      active_risk: "主动风险",
      trading: "交易",
      liquidity: "流动性",
      lists: "名单",
    },
    STYLE = {
      beta: "Beta",
      style_beta: "Beta",
      size: "规模",
      value: "价值",
      momentum: "动量",
      liquidity: "流动性",
      style_size: "规模",
      style_value: "价值",
      style_momentum: "动量",
      style_liquidity: "流动性",
    },
    FACTOR = {
      ai_factor_blend_v5: "AI因子融合",
      deep_factor_agent_v4: "深度因子",
      factor_domain_agent_v9: "因子域模型",
      fundamental_quality_v4: "基本面质量",
      index_industry_risk_alpha_v8: "行业风险Alpha",
      portfolio_optimizer_score_v2: "组合优化反馈",
      quality_value_low_crowding_v8: "质量价值低拥挤",
      small_value_quality_momo: "小盘价值质量动量",
    };
  const DEF = {
    universe: {
      code: "000905.SH",
      name: "中证500",
      rebalance_frequency: "monthly",
      holdings: 50,
      score_source: "",
    },
    objective: {
      type: "active_alpha",
      alpha_scale: 1,
      risk_aversion: 0.5,
      turnover_penalty: 1,
      cost_penalty: 1,
    },
    holdings: {
      long_only: true,
      fully_invested: true,
      min_weight: 0.002,
      max_weight: 0.03,
    },
    industry: { classification: "SW_L1", max_active_deviation: 0.02 },
    style: {
      max_abs_exposure: 0.2,
      size: 0.2,
      value: 0.2,
      momentum: 0.2,
      liquidity: 0.2,
    },
    active_risk: {
      tracking_error_limit: 0.06,
      max_active_weight: 0.03,
      covariance_model: "factor",
    },
    trading: { turnover_limit: 1.0, transaction_cost_bps: 10 },
    liquidity: {
      max_adv_participation: 0.05,
      exclude_suspended: true,
      exclude_limit_locked: true,
    },
    backtest: { start: "20190531", end: "20260630" },
    lists: { include: "", exclude: "" },
  };
  const state = {
    bootstrap: null,
    snapshot: null,
    config: clone(DEF),
    instruction: "",
    planOptions: [],
    selectedPlanId: "",
    draft: [],
    validation: null,
    run: null,
    pollTimer: null,
    plotlyPromise: null,
    rotation: null,
    timingIndex: "",
    timingFactor: "",
    currentPage: "basic",
  };
  function clone(v) {
    return JSON.parse(JSON.stringify(v));
  }
  function obj(v) {
    return v && typeof v === "object" && !Array.isArray(v) ? v : {};
  }
  function arr(v) {
    return Array.isArray(v) ? v : [];
  }
  function num(v) {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  function esc(v) {
    return String(v == null ? "" : v).replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  }
  function fixed(v, d = 2) {
    const n = num(v);
    return n == null ? "--" : n.toFixed(d);
  }
  function pct(v, d = 2) {
    const n = num(v);
    return n == null ? "--" : (n * 100).toFixed(d) + "%";
  }
  function dateText(v) {
    const s = String(v || "");
    return s
      ? /^[0-9]{8}$/.test(s)
        ? s.slice(0, 4) + "-" + s.slice(4, 6) + "-" + s.slice(6, 8)
        : s.slice(0, 10)
      : "--";
  }
  function present(a, b) {
    return a != null ? a : b;
  }
  function root() {
    return document.getElementById("view-root");
  }
  function get(source, path) {
    return path.split(".").reduce((v, k) => (v == null ? v : v[k]), source);
  }
  function set(target, path, value) {
    const keys = path.split(".");
    let c = target;
    keys.slice(0, -1).forEach((k) => {
      c[k] = c[k] || {};
      c = c[k];
    });
    c[keys.at(-1)] = value;
  }
  function stable(v) {
    if (Array.isArray(v)) return v.map(stable);
    if (v && typeof v === "object")
      return Object.keys(v)
        .sort()
        .reduce((o, k) => ((o[k] = stable(v[k])), o), {});
    return v;
  }
  function fingerprint(v) {
    return JSON.stringify(stable(v));
  }
  function merge(t, s) {
    Object.keys(obj(s)).forEach((k) => {
      if (obj(s[k]) === s[k]) {
        t[k] = t[k] || {};
        merge(t[k], s[k]);
      } else if (s[k] != null) t[k] = s[k];
    });
    return t;
  }
  async function api(path, opt) {
    const o = Object.assign(
      {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
      },
      opt || {},
    );
    if (o.body && typeof o.body !== "string") {
      o.headers = Object.assign({}, o.headers, {
        "Content-Type": "application/json",
      });
      o.body = JSON.stringify(o.body);
    }
    const r = await fetch(BASE + path, o);
    let p = {};
    try {
      p = await r.json();
    } catch (_) {
      p = {};
    }
    if (!r.ok)
      throw new Error(
        p.message || p.error || "请求失败（HTTP " + r.status + "）",
      );
    return p;
  }
  function conclusion(text, bad) {
    const b = document.getElementById("core-conclusion");
    if (!b) return;
    b.hidden = false;
    b.classList.toggle("optimizer-conclusion-blocked", !!bad);
    b.innerHTML =
      '<span class="eyebrow">核心结论</span><p>' + esc(text) + "</p>";
  }
  function block(t, d) {
    return (
      '<section class="optimizer-blocked"><strong>' +
      esc(t) +
      "</strong><span>" +
      esc(d || "") +
      "</span></section>"
    );
  }
  function section(t, s, b) {
    return (
      '<section class="optimizer-section"><header class="optimizer-section-head"><div><h2>' +
      esc(t) +
      "</h2>" +
      (s ? "<p>" + esc(s) + "</p>" : "") +
      "</div></header>" +
      b +
      "</section>"
    );
  }
  function card(t, id, n) {
    return (
      '<article class="optimizer-chart-card"><header><h3>' +
      esc(t) +
      "</h3>" +
      (n ? "<span>" + esc(n) + "</span>" : "") +
      '</header><div id="' +
      id +
      '" class="optimizer-chart"></div></article>'
    );
  }
  function charts(x) {
    return '<div class="optimizer-chart-grid">' + x.join("") + "</div>";
  }
  function chartsSingle(x) {
    return '<div class="optimizer-chart-grid optimizer-chart-grid-single">' + x.join("") + "</div>";
  }
  function kpi(l, v, n, t) {
    return (
      '<article class="optimizer-kpi ' +
      (t || "") +
      '"><span>' +
      esc(l) +
      "</span><strong>" +
      esc(v) +
      "</strong><small>" +
      esc(n || "") +
      "</small></article>"
    );
  }
  function evidence(x) {
    return (
      '<div class="optimizer-evidence">' +
      x.map((i) => kpi(i[0], i[1], i[2], i[3])).join("") +
      "</div>"
    );
  }
  function strip(x, t) {
    return (
      '<div class="optimizer-status-strip ' +
      (t || "") +
      '">' +
      x
        .map(
          (i) =>
            "<span>" + esc(i[0]) + "<strong>" + esc(i[1]) + "</strong></span>",
        )
        .join("") +
      "</div>"
    );
  }
  function boot() {
    return obj((state.bootstrap && state.bootstrap.data) || state.bootstrap);
  }
  function init() {
    state.config = clone(DEF);
    merge(state.config, obj(boot().defaults));
    const s = arr(boot().score_sources)[0];
    if (s) state.config.universe.score_source = s.id || s.code || s.name || "";
    state.config.universe.holdings = 50;
  }
  async function loadBootstrap(force) {
    if (state.bootstrap && !force) return state.bootstrap;
    state.bootstrap = await api("/api/optimizer/bootstrap");
    init();
    return state.bootstrap;
  }
  async function loadSnapshot(force) {
    await loadBootstrap(force);
    if (state.snapshot && !force) return state.snapshot;
    if (state.snapshotPromise && !force) return state.snapshotPromise;
    state.snapshotPromise = api("/api/optimizer/strategy-snapshot")
      .then((payload) => {
        state.snapshot = payload;
        return payload;
      })
      .finally(() => {
        state.snapshotPromise = null;
      });
    return state.snapshotPromise;
  }
  async function load(force) {
    if (state.bootstrap && state.snapshot && !force) return;
    await loadBootstrap(force);
    await loadSnapshot(force);
  }
  async function loadRotation(force) {
    if (state.rotation && !force) return state.rotation;
    state.rotation = await api("/api/rotation/snapshot");
    return state.rotation;
  }
  function problems() {
    const x = [],
      b = boot(),
      s = obj(state.snapshot);
    if (b.data_ready === false) x.push(b.block_reason || "研究数据未就绪");
    if (s.status !== "ready") x.push(s.message || "策略快照未就绪");
    if (arr(s.assets).length !== 500) x.push("中证500资产池不是500只");
    return x;
  }
  function chosen() {
    const s = obj(obj(state.snapshot).selected_run);
    return s.run_id && s.result
      ? {
          id: s.run_id,
          run_id: s.run_id,
          run_name: s.run_name,
          status: "completed",
          result: s.result,
        }
      : null;
  }
  function diagnostic() {
    const s = obj(obj(state.snapshot).latest_diagnostic_run);
    return s.run_id && s.result
      ? {
          id: s.run_id,
          run_id: s.run_id,
          run_name: s.run_name,
          status: "research_diagnostic",
          diagnostic: true,
          development_gate: obj(s.development_gate),
          publication_gate: obj(s.sealed_test_publication_gate),
          result: s.result,
        }
      : null;
  }
  function plotly() {
    if (window.Plotly) return Promise.resolve(window.Plotly);
    if (state.plotlyPromise) return state.plotlyPromise;
    return (state.plotlyPromise = new Promise((ok, no) => {
      const s = document.createElement("script");
      s.src =
        (window.APP_BOOT && window.APP_BOOT.plotlyUrl) ||
        BASE + "/static/vendor/plotly.min.js";
      s.onload = () => ok(window.Plotly);
      s.onerror = () => no(new Error("图表组件加载失败"));
      document.head.appendChild(s);
    }));
  }
  function plot(id, traces, extra) {
    const el = document.getElementById(id);
    if (!el) return;
    plotly()
      .then((P) =>
        P.react(
          el,
          traces,
          Object.assign(
            {
              margin: { l: 52, r: 20, t: 20, b: 48 },
              paper_bgcolor: "transparent",
              plot_bgcolor: "transparent",
              font: { family: FONT, size: 12, color: "#4f5968" },
              hoverlabel: { font: { family: FONT, size: 12 } },
              legend: { orientation: "h", x: 0, y: 1.14, font: { size: 11 } },
              xaxis: { gridcolor: C.grid, zeroline: false, automargin: true },
              yaxis: { gridcolor: C.grid, zeroline: false, automargin: true },
              uirevision: "r34",
            },
            extra || {},
          ),
          { displayModeBar: false, responsive: true },
        ),
      )
      .catch((e) => (el.innerHTML = block("图表不可用", e.message)));
  }
  function line(rows, name, color, w) {
    return {
      x: rows.map((r) => dateText(r.date)),
      y: rows.map((r) => present(r.nav, r.value)),
      type: "scatter",
      mode: "lines",
      name,
      line: { color, width: w || 2 },
    };
  }
  function mean(v) {
    const a = v.filter(Number.isFinite);
    return a.length ? a.reduce((x, y) => x + y, 0) / a.length : null;
  }
  function corr(a, b) {
    const r = a
      .map((x, i) => [num(x), num(b[i])])
      .filter((x) => x[0] != null && x[1] != null);
    if (r.length < 3) return null;
    const x = mean(r.map((v) => v[0])),
      y = mean(r.map((v) => v[1]));
    let c = 0,
      va = 0,
      vb = 0;
    r.forEach((v) => {
      const dx = v[0] - x,
        dy = v[1] - y;
      c += dx * dy;
      va += dx * dx;
      vb += dy * dy;
    });
    return va && vb ? c / Math.sqrt(va * vb) : null;
  }
  function wmean(rows, path, weight) {
    let s = 0,
      w = 0;
    rows.forEach((r) => {
      const v = num(get(r, path)),
        q = num(get(r, weight));
      if (v != null && q != null) {
        s += v * q;
        w += q;
      }
    });
    return w ? s / w : null;
  }
  function strategy(r, k) {
    const a = {
        benchmark: ["benchmark", "index", "csi500"],
        direct_score_top50: [
          "direct_score_top50",
          "score_top50",
          "top50",
          "direct",
        ],
        same_support_score_weighted: [
          "same_support_score_weighted",
          "same_support",
        ],
        constrained_optimizer: [
          "constrained_optimizer",
          "optimizer",
          "optimized",
          "portfolio",
        ],
      },
      boxes = [
        obj(r.strategies),
        obj(r.series),
        obj(r.curves),
        obj(r.nav_series),
        r,
      ];
    let f = null;
    boxes.some((b) =>
      a[k].some((x) => {
        if (b[x] != null) {
          f = b[x];
          return true;
        }
        return false;
      }),
    );
    return Array.isArray(f) ? { nav: f } : obj(f);
  }
  function nav(s) {
    return arr(s.nav || s.nav_series || s.series || s.curve || s.values)
      .map((v, i) =>
        Array.isArray(v)
          ? { date: String(v[0]), nav: num(v[1]) }
          : typeof v === "number"
            ? { date: String(i), nav: num(v) }
            : {
                date: String(v.date || v.trade_date || v.period || i),
                nav: num(present(v.nav, v.value)),
              },
      )
      .filter((v) => v.nav != null);
  }
  function align(a, b) {
    const m = new Map(b.map((r) => [r.date, r.nav]));
    return a
      .filter((r) => m.has(r.date))
      .map((r) => ({ date: r.date, a: r.nav, b: m.get(r.date) }));
  }
  function dd(a) {
    let p = -Infinity;
    return a.map(
      (r) => ((p = Math.max(p, r.nav)), { date: r.date, value: r.nav / p - 1 }),
    );
  }
  function excess(a, b) {
    return align(a, b).map((r) => ({ date: r.date, value: r.a / r.b - 1 }));
  }
  function rolling(a, b, w) {
    const z = align(a, b),
      e = [];
    for (let i = 1; i < z.length; i++)
      e.push({
        date: z[i].date,
        value: z[i].a / z[i - 1].a - z[i].b / z[i - 1].b,
      });
    const o = [];
    for (let i = w - 1; i < e.length; i++) {
      const v = e.slice(i - w + 1, i + 1).map((x) => x.value),
        m = mean(v),
        sd = Math.sqrt(
          v.reduce((s, x) => s + (x - m) ** 2, 0) / Math.max(1, v.length - 1),
        );
      o.push({ date: e[i].date, value: sd ? (m / sd) * Math.sqrt(12) : null });
    }
    return o;
  }
  function vm() {
    const run =
      state.run && state.run.result ? state.run : chosen() || diagnostic();
    if (!run) return null;
    const r = obj(run.result),
      s = {},
      n = {};
    Object.keys(LABEL).forEach((k) => {
      s[k] = strategy(r, k);
      n[k] = nav(s[k]);
    });
    const mr = obj(r.metrics || r.performance),
      metrics = Object.keys(LABEL).map((k) => {
        const x = Object.assign({}, obj(s[k].metrics), obj(mr[k]));
        return {
          key: k,
          annual_return: present(x.annual_return, x.annualized_return),
          volatility: present(x.annual_volatility, x.volatility),
          sharpe: present(x.sharpe, x.sharpe_ratio),
          information_ratio: present(x.information_ratio, x.ir),
          annual_excess_return: present(x.annual_excess_return, x.excess_return),
          positive_month_rate: present(x.positive_month_rate, x.win_rate),
          max_drawdown: x.max_drawdown,
          tracking_error: x.tracking_error,
          turnover: present(x.turnover, x.average_turnover),
          cost: present(x.transaction_cost, x.cost),
        };
      });
    return {
      run,
      result: r,
      nav: n,
      metrics,
      diagnostic: run.diagnostic === true,
      publicationGate: obj(run.publication_gate),
      formal:
        r.formal_metrics_valid === true &&
        obj(r.backtest_audit).formal_metrics_valid === true,
    };
  }
  function list(v) {
    return Array.isArray(v)
      ? v
      : v && typeof v === "object"
        ? Object.keys(v).map((k) =>
            typeof v[k] === "object"
              ? Object.assign({ name: k }, v[k])
              : { name: k, value: v[k] },
          )
        : [];
  }
  function pick(r, paths) {
    for (const p of paths) {
      const v = get(r, p);
      if (v != null) return list(v);
    }
    return [];
  }
  function weights(r) {
    return pick(r, ["portfolio.weights", "weights", "holdings"]);
  }
  function exposures(r) {
    return pick(r, ["risk.exposures", "exposures", "portfolio.exposures"]);
  }
  function trades(r) {
    return pick(r, ["trades", "portfolio.trades", "trade_list"]);
  }
  function metric(v, k) {
    return v.metrics.find((x) => x.key === k) || {};
  }
  function scope(v) {
    const l = obj(obj(v.result.backtest_audit).longest_contiguous_segment);
    return v.formal
      ? "完整连续请求窗口"
      : "诊断连续段 " +
          dateText(l.start) +
          "—" +
          dateText(l.end) +
          "（非正式绩效）";
  }
  function publicationMessage(v) {
    if (!v.diagnostic) return null;
    const gate = v.publicationGate;
    return (
      "研究诊断，禁止公开：封存测试年化超额 " +
      pct(gate.annual_excess_return) +
      "，夏普 " +
      fixed(gate.sharpe) +
      "，信息比率 " +
      fixed(gate.information_ratio) +
      "；未通过生产发布门禁。"
    );
  }
  function solver(r) {
    const s = obj(r.solver || r.optimization),
      p1 = obj(s.phase_i),
      p2 = obj(s.phase_ii);
    return {
      s,
      p1,
      p2,
      cert: s.certified === true || p2.certified === true,
      res: num(present(s.max_residual, s.max_violation)),
      fallback: r.fallback_used === true || s.fallback_used === true,
    };
  }
  function use(r) {
    const out = [],
      sl = obj(r.slack),
      ws = weights(r),
      ex = exposures(r),
      add = (name, a, l, g) => {
        a = num(a);
        l = num(l);
        if (a != null && l > 0)
          out.push({ name, a, l, g, ratio: Math.abs(a) / l });
      };
    if (num(sl.tracking_error) != null)
      add(
        "跟踪误差",
        state.config.active_risk.tracking_error_limit - num(sl.tracking_error),
        state.config.active_risk.tracking_error_limit,
        "主动风险",
      );
    if (num(sl.one_way_turnover) != null)
      add(
        "单期换手",
        state.config.trading.turnover_limit - num(sl.one_way_turnover),
        state.config.trading.turnover_limit,
        "交易",
      );
    add(
      "单股主动权重",
      Math.max(0, ...ws.map((x) => Math.abs(num(x.active_weight) || 0))),
      state.config.active_risk.max_active_weight,
      "持仓",
    );
    ex.forEach((x) => {
      const v = num(present(x.active_exposure, x.value));
      if (v == null) return;
      if (x.category === "style")
        add(
          STYLE[x.name] || x.name,
          v,
          state.config.style.max_abs_exposure,
          "风格",
        );
      if (x.category === "industry")
        add(
          (x.name || x.industry) === "UNCLASSIFIED"
            ? "未分类"
            : x.name || x.industry,
          v,
          state.config.industry.max_active_deviation,
          "行业",
        );
    });
    return out.sort((a, b) => b.ratio - a.ratio);
  }
  function funnel(id, r) {
    const q = obj(obj(state.snapshot).data_quality),
      t = q.tradable_count || 500;
    plot(
      id,
      [
        {
          type: "funnel",
          orientation: "h",
          y: [
            "中证500资产池",
            "可交易资产",
            "HiGHS精确支持集",
            "Clarabel认证权重",
          ],
          x: [500, t, 50, 50],
          textinfo: "value+percent initial",
          marker: { color: [C.grey, C.blue, C.gold, C.red] },
        },
      ],
      {
        margin: { l: 115, r: 20, t: 16, b: 28 },
        xaxis: { visible: false },
        yaxis: { gridcolor: "transparent" },
      },
    );
  }
  function constraints(id, r, n = 12) {
    const a = use(r).slice(0, n).reverse();
    plot(
      id,
      [
        {
          type: "bar",
          orientation: "h",
          y: a.map((x) => x.name),
          x: a.map((x) => Math.min(x.ratio, 1.2)),
          customdata: a.map((x) => [x.a, x.l]),
          hovertemplate:
            "%{y}<br>利用率 %{x:.1%}<br>实际 %{customdata[0]:.4f}<br>上限 %{customdata[1]:.4f}<extra></extra>",
          marker: {
            color: a.map((x) =>
              x.ratio >= 0.98 ? C.red : x.ratio >= 0.85 ? C.gold : C.green,
            ),
          },
        },
      ],
      {
        margin: { l: 105, r: 24, t: 16, b: 38 },
        xaxis: { range: [0, 1.2], tickformat: ".0%", title: "约束利用率" },
        yaxis: { gridcolor: "transparent" },
        shapes: [
          {
            type: "line",
            x0: 1,
            x1: 1,
            y0: -0.5,
            y1: a.length - 0.5,
            line: { color: C.red, dash: "dot" },
          },
        ],
        showlegend: false,
      },
    );
  }
  function solverCharts(p, r) {
    const i = solver(r);
    plot(
      p + "-solver",
      [
        {
          type: "bar",
          x: ["HiGHS支持选择", "Clarabel权重优化"],
          y: [num(i.p1.attempt_count) || 1, num(i.p2.iterations) || 0],
          text: [num(i.p1.attempt_count) || 1, num(i.p2.iterations) || 0],
          textposition: "outside",
          marker: { color: [C.blue, C.red] },
        },
      ],
      { yaxis: { title: "尝试/迭代" }, showlegend: false },
    );
    const a = Math.max(
        Math.abs(num(i.p1.max_linear_constraint_violation) || 1e-18),
        1e-18,
      ),
      b = Math.max(Math.abs(i.res || 1e-18), 1e-18);
    plot(
      p + "-residual",
      [
        {
          type: "bar",
          x: ["Phase-I线性约束", "Phase-II独立复核"],
          y: [a, b],
          text: [a.toExponential(2), b.toExponential(2)],
          textposition: "outside",
          marker: { color: [C.blue, C.red] },
        },
      ],
      { yaxis: { type: "log", title: "最大残差（对数）" }, showlegend: false },
    );
  }
  function performance(p, v) {
    const n = v.nav,
      b = n.benchmark || [],
      color = {
        benchmark: C.grey,
        direct_score_top50: C.blue,
        same_support_score_weighted: C.gold,
        constrained_optimizer: C.red,
      };
    plot(
      p + "-nav",
      Object.keys(LABEL).map((k) =>
        line(
          n[k] || [],
          LABEL[k],
          color[k],
          k === "constrained_optimizer" ? 3 : 1.8,
        ),
      ),
      { hovermode: "x unified", yaxis: { title: "净值" } },
    );
    plot(
      p + "-excess",
      [
        "direct_score_top50",
        "same_support_score_weighted",
        "constrained_optimizer",
      ].map((k) =>
        line(
          excess(n[k] || [], b),
          LABEL[k],
          color[k],
          k === "constrained_optimizer" ? 3 : 1.8,
        ),
      ),
      {
        hovermode: "x unified",
        yaxis: { tickformat: ".1%", title: "累计超额" },
      },
    );
    plot(
      p + "-drawdown",
      Object.keys(LABEL).map((k) =>
        line(
          dd(n[k] || []),
          LABEL[k],
          color[k],
          k === "constrained_optimizer" ? 3 : 1.8,
        ),
      ),
      { hovermode: "x unified", yaxis: { tickformat: ".1%", title: "回撤" } },
    );
    plot(
      p + "-ir",
      [
        "direct_score_top50",
        "same_support_score_weighted",
        "constrained_optimizer",
      ].map((k) =>
        line(
          rolling(
            n[k] || [],
            b,
            Math.min(12, Math.max(3, (n[k] || []).length - 2)),
          ),
          LABEL[k],
          color[k],
          k === "constrained_optimizer" ? 3 : 1.8,
        ),
      ),
      { hovermode: "x unified", yaxis: { title: "滚动信息比率" } },
    );
    plot(
      p + "-metric",
      [
        {
          type: "bar",
          x: v.metrics.map((x) => LABEL[x.key]),
          y: v.metrics.map((x) => num(x.annual_return)),
          name: "年化收益",
          marker: { color: C.red },
        },
        {
          type: "bar",
          x: v.metrics.map((x) => LABEL[x.key]),
          y: v.metrics.map((x) => num(x.volatility)),
          name: "年化波动",
          marker: { color: C.grey },
        },
        {
          type: "bar",
          x: v.metrics.map((x) => LABEL[x.key]),
          y: v.metrics.map((x) => num(x.tracking_error)),
          name: "跟踪误差",
          marker: { color: C.blue },
        },
      ],
      { barmode: "group", yaxis: { tickformat: ".1%" } },
    );
    plot(
      p + "-ratio",
      [
        {
          type: "bar",
          x: v.metrics.map((x) => LABEL[x.key]),
          y: v.metrics.map((x) => num(x.sharpe)),
          name: "夏普",
          marker: { color: C.red },
        },
        {
          type: "bar",
          x: v.metrics.map((x) => LABEL[x.key]),
          y: v.metrics.map((x) => num(x.information_ratio)),
          name: "信息比率",
          marker: { color: C.blue },
        },
      ],
      { barmode: "group" },
    );
  }
  function field(path, label, percent, unit) {
    let v = get(state.config, path);
    if (percent && num(v) != null) v *= 100;
    return (
      '<label class="optimizer-control"><span>' +
      esc(label) +
      '</span><div><input data-path="' +
      path +
      '" type="number" value="' +
      v +
      '" step="' +
      (percent ? ".1" : "any") +
      '" ' +
      (path === "universe.holdings" ? "disabled" : "") +
      "><em>" +
      esc(unit || "") +
      "</em></div></label>"
    );
  }
  function bindConfig() {
    root()
      .querySelectorAll("[data-path]")
      .forEach(
        (i) =>
          (i.onchange = () => {
            let v = Number(i.value);
            if (
              [
                "holdings.max_weight",
                "industry.max_active_deviation",
                "style.max_abs_exposure",
                "active_risk.tracking_error_limit",
                "active_risk.max_active_weight",
                "trading.turnover_limit",
                "liquidity.max_adv_participation",
              ].includes(i.dataset.path)
            )
              v /= 100;
            set(state.config, i.dataset.path, v);
            state.validation = null;
            renderBasic();
          }),
      );
  }
  function renderBasic() {
    const s = obj(state.snapshot),
      q = obj(s.data_quality),
      score = obj(s.score),
      v = vm(),
      r = v ? v.result : {};
    root().innerHTML =
      '<div class="optimizer-shell">' +
      evidence([
        ["资产池", "中证500", "500只固定截面", "is-primary"],
        ["历史得分", dateText(score.signal_date), score.score_name],
        ["可交易", (q.tradable_count || "--") + "只", "停牌与涨跌停过滤"],
        ["目标持仓", "50只", "整数支持集"],
        ["求解器", "HiGHS + Clarabel", "无兜底"],
      ]) +
      section(
        "关键约束",
        "默认仅展示直接决定组合的参数",
        '<div class="optimizer-control-grid">' +
          field("universe.holdings", "持仓数量", false, "只") +
          field("holdings.max_weight", "单股权重上限", true, "%") +
          field("industry.max_active_deviation", "行业偏离上限", true, "%") +
          field("style.max_abs_exposure", "风格暴露上限", false, "") +
          field("active_risk.tracking_error_limit", "跟踪误差上限", true, "%") +
          field("trading.turnover_limit", "单期换手上限", true, "%") +
          field("trading.transaction_cost_bps", "交易成本", false, "bp") +
          field("objective.risk_aversion", "风险厌恶系数", false, "") +
          '</div><details class="optimizer-advanced"><summary>高级约束</summary><div class="optimizer-control-grid">' +
          field("holdings.min_weight", "入选最小权重", true, "%") +
          field("active_risk.max_active_weight", "单股主动权重", true, "%") +
          field("liquidity.max_adv_participation", "ADV参与率", true, "%") +
          field("objective.turnover_penalty", "换手惩罚", false, "") +
          '</div></details><div class="optimizer-action-row"><span>修改参数后进入LLM约束页生成可审计草案。</span><button class="action-button" id="to-llm">进入LLM约束</button></div>',
      ) +
      section(
        "求解结构",
        "资产过滤、精确选股与连续权重认证",
        charts([
          card("支持集求解流程", "basic-funnel", "500只到50只"),
          card("约束利用率", "basic-constraint", "接近100%为绑定约束"),
        ]),
      ) +
      section(
        "模型状态",
        "求解器与数据完整性",
        charts([
          card("两阶段求解", "basic-solver", "尝试与迭代"),
          card("独立约束残差", "basic-residual", "对数坐标"),
        ]),
      ) +
      "</div>";
    bindConfig();
    root().querySelector("#to-llm").onclick = () => go("llm");
    funnel("basic-funnel", r);
    constraints("basic-constraint", r, 10);
    solverCharts("basic", r);
    frameworkCharts("basic", v);
  }
  async function basicPage() {
    root().innerHTML =
      '<div class="loading-card">正在读取中证500资产池与认证模型…</div>';
    try {
      await load(false);
    } catch (e) {
      conclusion("真实数据接口不可用，求解已阻断。", true);
      root().innerHTML = block("数据加载失败", e.message);
      return;
    }
    const p = problems();
    if (p.length) {
      conclusion("资产池或历史得分不完整，未生成模拟数据。", true);
      root().innerHTML = block("不能进入求解", p.join("；"));
      return;
    }
    conclusion(
      "中证500的500只股票和月频历史得分已进入同一优化链；页面只保留关键约束，求解效果以图形展示。",
    );
    renderBasic();
  }
  function draftFp() {
    return fingerprint({ config: state.config, draft: state.draft });
  }
  function valid() {
    const p =
      state.validation && state.validation.fp === draftFp()
        ? obj(state.validation.payload)
        : {};
    return {
      p,
      checked: p.feasible === true && !!p.draft_id,
      confirmed:
        p.confirmation_valid === true &&
        !!(p.validation_id || get(p, "confirmation.confirm_hash")),
    };
  }
  function constraintCard(x, i) {
    return (
      '<details class="optimizer-constraint-card" data-index="' +
      i +
      '"><summary><span class="optimizer-constraint-title"><strong>' +
      esc(x.name || "约束条件") +
      "</strong><small>" +
      esc(CAT[x.category] || x.category || "主动风险") +
      '</small></span><span class="optimizer-formula">' +
      esc(x.formula || x.operator || "") +
      '</span></summary><div class="optimizer-constraint-edit"><label><span>名称</span><input data-key="name" value="' +
      esc(x.name || "约束条件") +
      '"></label><label><span>类别</span><select data-key="category">' +
      Object.keys(CAT)
        .map(
          (k) =>
            '<option value="' +
            k +
            '" ' +
            ((x.category || "active_risk") === k ? "selected" : "") +
            ">" +
            CAT[k] +
            "</option>",
        )
        .join("") +
      '</select></label><label><span>关系</span><select data-key="operator">' +
      ["<=", ">=", "=", "between", "in", "not_in"]
        .map(
          (k) =>
            "<option " +
            (x.operator === k ? "selected" : "") +
            ">" +
            k +
            "</option>",
        )
        .join("") +
      '</select></label><label><span>下界</span><input data-key="lower" type="number" value="' +
      esc(x.lower == null ? "" : x.lower) +
      '"></label><label><span>上界</span><input data-key="upper" type="number" value="' +
      esc(x.upper == null ? "" : x.upper) +
      '"></label><label><span>单位</span><input data-key="unit" value="' +
      esc(x.unit || "") +
      '"></label><label class="is-wide"><span>数学公式</span><input data-key="formula" value="' +
      esc(x.formula || "") +
      '"></label><label class="is-wide"><span>求解依据</span><textarea data-key="reference" rows="2">' +
      esc(x.reference || x.rationale || "") +
      '</textarea></label><button class="ghost-button is-danger" data-remove="' +
      i +
      '">删除约束</button></div></details>'
    );
  }
  function bindDraft() {
    root()
      .querySelectorAll(".optimizer-constraint-card")
      .forEach((c) => {
        const i = Number(c.dataset.index);
        c.querySelectorAll("[data-key]").forEach(
          (x) =>
            (x.onchange = () => {
              state.draft[i][x.dataset.key] =
                x.type === "number" && x.value !== ""
                  ? Number(x.value)
                  : x.value;
              state.validation = null;
              renderWorkflow();
            }),
        );
      });
    root()
      .querySelectorAll("[data-remove]")
      .forEach(
        (b) =>
          (b.onclick = () => {
            state.draft.splice(Number(b.dataset.remove), 1);
            state.validation = null;
            renderWorkflow();
          }),
      );
  }
  function activePlan() {
    const options = arr(state.planOptions);
    if (!options.length) return null;
    return (
      options.find((x) => String(x.id || "") === String(state.selectedPlanId || "")) ||
      options[0]
    );
  }
  function textItems(v, limit) {
    const out = [];
    if (typeof v === "string") return v.trim() ? [v.trim()] : [];
    if (Array.isArray(v)) {
      v.forEach((x) => {
        if (out.length >= limit) return;
        if (typeof x === "string" && x.trim()) out.push(x.trim());
        else if (x && typeof x === "object") out.push(x.formula || x.name || x.metric || JSON.stringify(x));
        else if (x != null) out.push(String(x));
      });
      return out;
    }
    if (v && typeof v === "object") {
      Object.keys(v).forEach((k) => {
        if (out.length >= limit) return;
        const item = v[k];
        out.push(k + ": " + (item && typeof item === "object" ? JSON.stringify(item) : String(item)));
      });
    }
    return out;
  }
  function renderPlanParams(params) {
    const p = obj(params),
      keys = Object.keys(p).slice(0, 6);
    if (!keys.length) return '<div class="optimizer-plan-empty">默认参数由现有配置继承</div>';
    return keys
      .map((k) => {
        const v = p[k],
          text = v && typeof v === "object" ? JSON.stringify(v) : String(v);
        return '<div><strong>' + esc(k) + '</strong><span>' + esc(text) + '</span></div>';
      })
      .join("");
  }
  function planCard(x, i) {
    const selected = String(x.id || "") === String((activePlan() || {}).id || ""),
      terms = textItems(x.objective_terms, 4),
      eqs = textItems(x.constraint_equations, 4),
      steps = textItems(x.solver_steps, 4),
      cons = arr(x.added_constraints).slice(0, 4);
    return (
      '<article class="optimizer-plan-card ' +
      (selected ? "is-selected" : "") +
      '"><header><span>方案 ' +
      (i + 1) +
      '</span><h3>' +
      esc(x.name || x.id || "组合优化方案") +
      '</h3><small>' +
      esc(x.profile || "约束组合优化") +
      '</small></header><p>' +
      esc(x.summary || "完整目标函数、约束和求解流程方案") +
      '</p><div class="optimizer-plan-equation"><strong>目标函数</strong><code>' +
      esc(x.objective_equation || "") +
      '</code></div>' +
      (terms.length
        ? '<div class="optimizer-plan-tags">' + terms.map((t) => '<em>' + esc(t) + '</em>').join("") + '</div>'
        : "") +
      '<div class="optimizer-plan-params">' +
      renderPlanParams(x.default_parameters) +
      '</div>' +
      (cons.length
        ? '<div class="optimizer-plan-list"><strong>新增/强化约束</strong>' +
          cons
            .map((c) => '<span>' + esc(c.name || c.metric || c.type || "约束") + '：' + esc(c.formula || c.equation || c.rationale || "") + '</span>')
            .join("") +
          '</div>'
        : "") +
      (eqs.length
        ? '<div class="optimizer-plan-list"><strong>方程</strong>' + eqs.map((t) => '<span>' + esc(t) + '</span>').join("") + '</div>'
        : "") +
      (steps.length
        ? '<div class="optimizer-plan-list"><strong>求解步骤</strong>' + steps.map((t) => '<span>' + esc(t) + '</span>').join("") + '</div>'
        : "") +
      '<footer><span>' +
      esc(x.expected_tradeoff || "等待人工选择后进入严格编译") +
      '</span><button class="ghost-button" data-plan-index="' +
      i +
      '">' +
      (selected ? "已选择" : "选择此方案") +
      "</button></footer></article>"
    );
  }
  function bindPlanOptions() {
    root()
      .querySelectorAll("[data-plan-index]")
      .forEach(
        (b) =>
          (b.onclick = () => {
            const option = arr(state.planOptions)[Number(b.dataset.planIndex)];
            state.selectedPlanId = option ? String(option.id || "") : "";
            state.validation = null;
            renderWorkflow();
          }),
      );
  }
  function renderWorkflow() {
    if (state.currentPage === "learning") return renderLearningContent();
    return renderLlm();
  }
  function renderLlm() {
    const v = valid(),
      kb = obj(boot().knowledge_base),
      issues = arr(v.p.errors).concat(arr(v.p.conflicts)),
      plan = activePlan();
    let phase = "等待生成方案";
    if (arr(state.planOptions).length && !state.draft.length) phase = "等待按方案生成草案";
    if (state.draft.length) phase = "等待校验";
    if (v.checked) phase = "等待人工确认";
    if (v.confirmed) phase = "已确认，可提交";
    root().innerHTML =
      '<div class="optimizer-shell">' +
      strip(
        [
          ["工作流", "输入 → 生成方案 → 选择/修改 → 草案 → 校验 → 确认 → 求解"],
          ["当前状态", phase],
          ["方案数量", String(arr(state.planOptions).length || "--")],
          ["约束数量", String(state.draft.length)],
          ["权重权限", "LLM不得输出权重"],
        ],
        v.confirmed ? "is-ready" : "",
      ) +
      '<div class="optimizer-llm-grid"><section class="optimizer-prompt"><header><h2>LLM方案输入</h2><p>先生成1–3套完整方程和流程方案；选择并修改后，再进入严格约束编译。</p></header><textarea id="instruction" rows="6" placeholder="例如：中证500内持有50只；行业偏离不超过2%；风格暴露不超过0.10；跟踪误差不超过6%；单期换手不超过100%；希望新增低换手或更严格行业约束。">' +
      esc(state.instruction) +
      '</textarea><div class="optimizer-action-row"><span>方案生成阶段同样走中转站，且禁止返回权重。</span><button class="action-button" id="generate-plans">生成方程方案</button></div></section><aside class="optimizer-kb"><h3>约束知识库</h3><div class="optimizer-kb-chips"><span>持仓与整数选择</span><span>行业主动偏离</span><span>风格中性</span><span>跟踪误差SOCP</span><span>换手与成本</span><span>流动性与名单</span></div><dl><div><dt>版本</dt><dd>' +
      esc(kb.version || boot().knowledge_base_version || "--") +
      "</dd></div><div><dt>来源</dt><dd>" +
      esc(kb.source_count || "--") +
      "</dd></div><div><dt>方案生成</dt><dd>中转站LLM</dd></div><div><dt>权重生成</dt><dd>禁止</dd></div></dl></aside></div>" +
      (arr(state.planOptions).length
        ? section(
            "方程方案",
            "每张卡片对应一套完整的目标函数、参数分类、约束方程和求解步骤。",
            '<div class="optimizer-plan-grid">' + arr(state.planOptions).map(planCard).join("") + "</div>",
          )
        : "") +
      (plan && !state.draft.length
        ? section(
            "选中方案",
            "可以直接编辑该方案的完整约束意图；点击生成后进入原有严格编译、校验与人工确认链路。",
            '<div class="optimizer-selected-plan"><textarea id="selected-plan-request" rows="7">' +
              esc(plan.mandate_request || state.instruction) +
              '</textarea><div class="optimizer-action-row"><span>该文本会作为当前方案传入 OptimizationMandate/v1 编译器。</span><button class="action-button" id="interpret">按选中方案生成约束草案</button></div></div>',
          )
        : "") +
      (state.draft.length
        ? section(
            "约束草案",
            "按类别折叠展示；默认不铺开参数表。",
            '<div class="optimizer-constraint-toolbar"><span>' +
              state.draft.length +
              '条硬约束草案</span><button class="ghost-button" id="add">新增约束</button></div><div class="optimizer-constraint-list">' +
              state.draft.map(constraintCard).join("") +
              "</div>",
          )
        : "") +
      (state.draft.length
        ? '<section class="optimizer-workflow"><div class="optimizer-workflow-state"><strong>' +
          phase +
          "</strong><span>" +
          esc(
            issues.length
              ? issues.join("；")
              : v.confirmed
                ? "确认哈希已锁定；修改任何约束都必须重新校验和确认。"
                : v.checked
                  ? "后端可行性校验通过，等待人工确认当前哈希。"
                  : "先执行结构、语义、可行性和求解器能力校验。",
          ) +
          '</span></div><div class="optimizer-actions"><button class="ghost-button" id="validate">校验</button><button class="ghost-button" id="confirm" ' +
          (v.checked && !v.confirmed ? "" : "disabled") +
          '>人工确认</button><button class="action-button" id="submit" ' +
          (v.confirmed ? "" : "disabled") +
          ">提交求解</button></div></section>"
        : "") +
      "</div>";
    root().querySelector("#instruction").oninput = (e) => {
      state.instruction = e.target.value;
      state.validation = null;
    };
    const gen = root().querySelector("#generate-plans");
    if (gen) gen.onclick = generatePlans;
    bindPlanOptions();
    const selected = root().querySelector("#selected-plan-request");
    if (selected && plan) {
      selected.oninput = (e) => {
        plan.mandate_request = e.target.value;
        plan.compile_instruction = e.target.value;
        state.validation = null;
      };
    }
    const interpretButton = root().querySelector("#interpret");
    if (interpretButton) interpretButton.onclick = interpret;
    if (state.draft.length) {
      bindDraft();
      root().querySelector("#add").onclick = () => {
        state.draft.push({
          name: "新增约束",
          category: "active_risk",
          operator: "<=",
          upper: "",
          unit: "",
          hard: true,
          formula: "",
          reference: "",
        });
        state.validation = null;
        renderWorkflow();
      };
      root().querySelector("#validate").onclick = validate;
      root().querySelector("#confirm").onclick = confirm;
      root().querySelector("#submit").onclick = submit;
    }
  }
  async function llmPage() {
    state.currentPage = "llm";
    try {
      await loadBootstrap(false);
    } catch (e) {
      root().innerHTML = block("工作台加载失败", e.message);
      return;
    }
    conclusion(
      "LLM先生成可选择的方程方案，再把选中方案编译成约束；人工确认后，HiGHS+Clarabel 才能求解权重。",
    );
    renderWorkflow();
  }
  async function generatePlans() {
    const b = root().querySelector("#generate-plans");
    if (b) b.disabled = true;
    try {
      const q = await api("/api/optimizer/constraints/plans", {
          method: "POST",
          body: {
            mode: "joint_cardinality",
            instruction: state.instruction,
            base_config: state.config,
            universe: "000905.SH",
            rebalance_frequency: "monthly",
            knowledge_base_version:
              obj(boot().knowledge_base).version ||
              boot().knowledge_base_version ||
              null,
          },
        }),
        d = obj(q.data || q),
        options = arr(d.options || d.plans || d.schemes);
      if (!options.length) throw new Error(arr(d.errors).join("；") || "接口未返回方程方案");
      state.planOptions = options;
      state.selectedPlanId = String(options[0].id || "");
      state.draft = [];
      state.validation = null;
      renderWorkflow();
      conclusion("方程方案已生成；请选择或修改后，再生成约束草案。");
    } catch (e) {
      if (b) b.disabled = false;
      conclusion("方程方案生成失败：" + e.message, true);
    }
  }
  async function interpret() {
    const b = root().querySelector("#interpret"),
      plan = activePlan(),
      selectedInstruction = String((plan && plan.mandate_request) || state.instruction || "").trim();
    if (b) b.disabled = true;
    try {
      if (!selectedInstruction) throw new Error("请先输入约束需求或选择方案");
      const q = await api("/api/optimizer/constraints/interpret", {
          method: "POST",
          body: {
            mode: "joint_cardinality",
            instruction: selectedInstruction,
            selected_plan: plan,
            base_config: state.config,
            universe: "000905.SH",
            rebalance_frequency: "monthly",
            knowledge_base_version:
              obj(boot().knowledge_base).version ||
              boot().knowledge_base_version ||
              null,
          },
        }),
        d = obj(q.data || q),
        a = arr(d.constraints || d.draft || d.items);
      if (!a.length) throw new Error(arr(d.errors).join("；") || "接口未返回约束草案");
      state.draft = a.map((x) =>
        Object.assign(
          {
            name: "约束条件",
            category: "active_risk",
            operator: "<=",
            hard: true,
          },
          obj(x),
        ),
      );
      state.validation = null;
      renderWorkflow();
      conclusion("选中方案已编译为约束草案；修改后执行校验和人工确认。");
    } catch (e) {
      if (b) b.disabled = false;
      conclusion("约束草案生成失败：" + e.message, true);
    }
  }
  async function validate() {
    const b = root().querySelector("#validate");
    b.disabled = true;
    try {
      const q = await api("/api/optimizer/constraints/validate", {
          method: "POST",
          body: {
            mode: "joint_cardinality",
            base_config: state.config,
            constraints: state.draft,
            universe: "000905.SH",
            score_source: state.config.universe.score_source,
          },
        }),
        d = obj(q.data || q);
      state.validation = { fp: draftFp(), payload: d };
      renderWorkflow();
      conclusion(
        d.feasible === true
          ? "结构、语义、可行性与求解器能力校验通过；仍需人工确认当前约束哈希。"
          : "约束校验未通过，求解已阻断。",
        d.feasible !== true,
      );
    } catch (e) {
      b.disabled = false;
      conclusion("约束校验失败：" + e.message, true);
    }
  }
  async function confirm() {
    const v = valid();
    if (!v.checked) return;
    try {
      const q = await api("/api/optimizer/constraints/validate", {
          method: "POST",
          body: {
            draft_id: v.p.draft_id,
            action: "confirm",
            expected_draft_hash: v.p.draft_hash,
            actor: "portfolio_reviewer",
          },
        }),
        d = obj(q.data || q);
      state.validation = { fp: draftFp(), payload: d };
      renderWorkflow();
      conclusion(
        d.confirmation_valid
          ? "当前约束哈希已由人工确认，可以提交真实求解。"
          : "确认未生效，求解仍被阻断。",
        !d.confirmation_valid,
      );
    } catch (e) {
      conclusion("人工确认失败：" + e.message, true);
    }
  }
  async function submit() {
    const v = valid();
    if (!v.confirmed) return;
    try {
      const id = v.p.validation_id || get(v.p, "confirmation.confirm_hash"),
        q = await api("/api/optimizer/runs", {
          method: "POST",
          body: {
            config: v.p.normalized_config || state.config,
            constraints: v.p.normalized_constraints || state.draft,
            validation_id: id,
            universe: "000905.SH",
            benchmark: "000905.SH",
            score_source: state.config.universe.score_source,
            rebalance_frequency: "monthly",
            holdings: 50,
            comparators: Object.keys(LABEL),
          },
        }),
        d = obj(q.data || q),
        run = d.run_id || d.id;
      if (!run) throw new Error("接口未返回run_id");
      state.run = { id: String(run), status: d.status || "queued" };
      go("results");
    } catch (e) {
      conclusion("求解任务创建失败：" + e.message, true);
    }
  }
  function progress(r) {
    root().innerHTML =
      '<div class="optimizer-shell">' +
      strip([
        ["任务", r.id || r.run_id || "--"],
        ["状态", r.status || "running"],
        ["阶段", r.stage || "真实数据回测与约束认证"],
      ]) +
      '<section class="optimizer-progress"><strong>精确求解正在执行</strong><span>HiGHS支持选择、Clarabel连续权重、独立约束复核与回测审计</span><div><i style="width:' +
      Math.max(5, Math.min(100, (num(r.progress) || 0.08) * 100)) +
      '%"></i></div></section></div>';
  }
  function clear() {
    if (state.pollTimer) {
      clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
  }
  async function poll() {
    try {
      const r = await api(
        "/api/optimizer/runs/" + encodeURIComponent(state.run.id) + "?live=1",
      );
      state.run = Object.assign({}, state.run, r, {
        id: r.run_id || state.run.id,
      });
      if (r.status === "completed" && r.result) {
        clear();
        await load(true);
        results(vm());
        return;
      }
      if (["failed", "blocked", "cancelled"].includes(r.status)) {
        clear();
        root().innerHTML = block(
          "求解被阻断",
          r.message || r.error || r.status,
        );
        return;
      }
      progress(state.run);
      state.pollTimer = setTimeout(poll, 2500);
    } catch (e) {
      clear();
      root().innerHTML = block("无法读取任务", e.message);
    }
  }
  function named(w) {
    const m = new Map(arr(obj(state.snapshot).assets).map((a) => [a.code, a])),
      a = m.get(w.code || w.ts_code) || {};
    return Object.assign({}, w, {
      name: w.security_name || w.label || a.name || w.code,
    });
  }
  function portfolio(p, r, ws) {
    const top = ws
      .slice()
      .sort((a, b) => (num(b.weight) || 0) - (num(a.weight) || 0))
      .slice(0, 18)
      .reverse();
    plot(
      p + "-weight",
      [
        {
          type: "bar",
          orientation: "h",
          y: top.map((x) => x.name),
          x: top.map((x) => num(x.weight)),
          name: "组合",
          marker: { color: C.red },
        },
        {
          type: "bar",
          orientation: "h",
          y: top.map((x) => x.name),
          x: top.map((x) => num(x.benchmark_weight)),
          name: "基准",
          marker: { color: C.grey },
        },
        {
          type: "bar",
          orientation: "h",
          y: top.map((x) => x.name),
          x: top.map((x) => num(x.active_weight)),
          name: "主动",
          marker: { color: C.blue },
        },
      ],
      {
        barmode: "group",
        margin: { l: 105, r: 18, t: 16, b: 42 },
        xaxis: { tickformat: ".1%" },
        yaxis: { gridcolor: "transparent" },
      },
    );
    plot(
      p + "-active",
      [
        {
          type: "histogram",
          x: ws.map((x) => num(x.active_weight)),
          nbinsx: 16,
          marker: { color: C.red },
        },
      ],
      { xaxis: { tickformat: ".1%" }, showlegend: false },
    );
    const e = exposures(r),
      i = e.filter((x) => x.category === "industry"),
      s = e.filter((x) => x.category === "style");
    plot(
      p + "-industry",
      [
        {
          type: "bar",
          x: i.map((x) => (x.name === "UNCLASSIFIED" ? "未分类" : x.name)),
          y: i.map((x) => num(present(x.active_exposure, x.value))),
          marker: {
            color: i.map((x) =>
              (num(present(x.active_exposure, x.value)) || 0) >= 0
                ? C.red
                : C.blue,
            ),
          },
        },
      ],
      { yaxis: { tickformat: ".1%" }, showlegend: false },
    );
    plot(
      p + "-style",
      [
        {
          type: "bar",
          x: s.map((x) => STYLE[x.name] || x.name),
          y: s.map((x) => num(present(x.active_exposure, x.value))),
          marker: {
            color: s.map((x) =>
              (num(present(x.active_exposure, x.value)) || 0) >= 0
                ? C.red
                : C.blue,
            ),
          },
        },
      ],
      { showlegend: false },
    );
  }
  function tradeAudit(p, r, t) {
    const z = t
        .slice()
        .sort(
          (a, b) =>
            Math.abs(num(b.trade_weight) || 0) -
            Math.abs(num(a.trade_weight) || 0),
        )
        .slice(0, 20)
        .reverse(),
      a = obj(r.backtest_audit),
      c = obj(a.continuity);
    plot(
      p + "-trades",
      [
        {
          type: "bar",
          orientation: "h",
          y: z.map((x) => x.security_name || x.name || x.code),
          x: z.map((x) => num(x.trade_weight || x.delta_weight)),
          marker: {
            color: z.map((x) =>
              (num(x.trade_weight || x.delta_weight) || 0) >= 0
                ? C.red
                : C.blue,
            ),
          },
        },
      ],
      {
        xaxis: { tickformat: ".1%" },
        yaxis: { gridcolor: "transparent" },
        margin: { l: 98, r: 18, t: 16, b: 42 },
        showlegend: false,
      },
    );
    const v = [
      num(c.complete_return_periods) || 0,
      num(c.requested_calendar_months) || 0,
      num(a.optimizer_certified_periods || a.certified_periods) || 0,
      num(a.rebalance_blocked_periods) || 0,
      num(a.carried_period_count) || 0,
    ];
    plot(
      p + "-coverage",
      [
        {
          type: "bar",
          x: ["完整收益期", "请求月份", "认证调仓", "阻断调仓", "原持仓延续"],
          y: v,
          text: v,
          textposition: "outside",
          marker: { color: [C.green, C.grey, C.red, C.blue, C.gold] },
        },
      ],
      { showlegend: false },
    );
  }
  function results(v) {
    if (!v) {
      root().innerHTML = block(
        "暂无认证结果",
        "请先在LLM约束页完成生成、校验、人工确认和求解。",
      );
      return;
    }
    const o = metric(v, "constrained_optimizer"),
      i = solver(v.result),
      ws = weights(v.result).map(named),
      t = trades(v.result),
      sc = scope(v);
    conclusion(
      v.diagnostic
        ? publicationMessage(v)
        : v.formal
        ? "模型已通过完整窗口、求解器和约束审计；公开结果不使用测试期收益挑选。"
        : "最新模型已通过HiGHS+Clarabel精确求解和约束审计；回测窗口不完整，绩效仅作最长连续段诊断。",
      !v.formal,
    );
    root().innerHTML =
      '<div class="optimizer-shell">' +
      strip(
        [
          ["任务", v.run.id],
          ["模型", "HiGHS + Clarabel"],
          ["支持集", ws.length + "只"],
          ["认证", i.cert && !i.fallback ? "通过" : "未通过"],
          ["发布口径", v.diagnostic ? "生产否决" : v.formal ? "正式" : "研究诊断"],
        ],
        i.cert && !i.fallback && !v.diagnostic ? "is-ready" : "is-blocked",
      ) +
      evidence([
        ["年化收益", pct(o.annual_return), sc, "is-primary"],
        ["夏普", fixed(o.sharpe), sc],
        ["信息比率", fixed(o.information_ratio), sc],
        ["跟踪误差", pct(o.tracking_error), "年化"],
        ["最大回撤", pct(o.max_drawdown), sc],
        ["求解残差", i.res == null ? "--" : i.res.toExponential(2), "独立复核"],
      ]) +
      section(
        "收益与超额",
        sc,
        charts([
          card("净值曲线", "result-nav", sc),
          card("累计超额", "result-excess", sc),
          card("回撤", "result-drawdown", sc),
          card("滚动信息比率", "result-ir", sc),
          card("收益、波动与跟踪误差", "result-metric", sc),
          card("夏普与信息比率", "result-ratio", sc),
        ]),
      ) +
      section(
        "求解器效果",
        "支持集、约束利用率与数值认证",
        charts([
          card("支持集求解流程", "result-funnel", "500只到50只"),
          card("约束利用率", "result-constraint", "100%为约束边界"),
          card("两阶段求解", "result-solver", "HiGHS + Clarabel"),
          card("独立约束残差", "result-residual", "对数坐标"),
        ]),
      ) +
      section(
        "组合结构",
        "权重、主动权重、行业与风格",
        charts([
          card("主要持仓权重", "result-weight", "组合、基准与主动权重"),
          card("主动权重分布", "result-active", "50只持仓"),
          card("行业主动暴露", "result-industry", "相对中证500"),
          card("风格主动暴露", "result-style", "四类风格"),
        ]),
      ) +
      section(
        "交易与审计",
        "调仓方向与回测覆盖",
        charts([
          card("主要调仓", "result-trades", "买入与卖出"),
          card("回测覆盖", "result-coverage", "完整、认证、阻断与延续"),
        ]) +
          '<details class="optimizer-detail-list"><summary>查看主要持仓</summary><div>' +
          ws
            .slice()
            .sort((a, b) => (num(b.weight) || 0) - (num(a.weight) || 0))
            .slice(0, 12)
            .map(
              (x) =>
                "<span><strong>" +
                esc(x.name) +
                "</strong><em>" +
                pct(x.weight) +
                "</em></span>",
            )
            .join("") +
          "</div></details>",
      ) +
      "</div>";
    performance("result", v);
    funnel("result-funnel", v.result);
    constraints("result-constraint", v.result);
    solverCharts("result", v.result);
    portfolio("result", v.result, ws);
    tradeAudit("result", v.result, t);
  }
  async function resultsPage() {
    state.currentPage = "results";
    clear();
    try {
      await load(false);
    } catch (e) {
      root().innerHTML = block("结果加载失败", e.message);
      return;
    }
    if (state.run && !state.run.result) {
      progress(state.run);
      await poll();
      return;
    }
    results(vm());
  }
  function frameworkData() {
    return obj(obj(state.snapshot).framework);
  }
  function frameworkComponent(id) {
    return arr(frameworkData().components).find((x) => x.id === id) || {};
  }
  function frameworkCard(x) {
    const tags = arr(x.solver_stack)
      .concat(arr(x.left_side))
      .concat(arr(x.right_side))
      .concat(arr(x.linked_modules))
      .slice(0, 4);
    return (
      '<article class="optimizer-framework-card"><header><span>' +
      esc(x.status || "--") +
      '</span><h3>' +
      esc(x.name || x.id || "??") +
      '</h3></header><p>' +
      esc(
        x.current_use ||
          (arr(x.input_contract).length
            ? "组合优化全链路已接入当前约束求解器"
            : "组合优化全链路等待正式求解快照"),
      ) +
      '</p><div>' +
      tags.map((t) => '<em>' + esc(t) + '</em>').join("") +
      '</div></article>'
    );
  }
  function frameworkCards() {
    const f = frameworkData(), comps = arr(f.components);
    if (!comps.length) return "";
    return '<div class="optimizer-framework-grid">' + comps.map(frameworkCard).join("") + "</div>";
  }
  function frameworkSummarySection(prefix, v) {
    const f = frameworkData(), comps = arr(f.components);
    if (!comps.length) return "";
    return section(
      "流程框架",
      "默认参数/约束解释/人工确认/HiGHS选股/Clarabel求权",
      frameworkCards() +
        charts([
          card("求解流程图", prefix + "-framework-flow", "五步组合优化链路"),
          card("模块联动", prefix + "-framework-link", "因子实验室与指数增强衔接"),
          card("环节状态", prefix + "-framework-state", "流程可用性检查"),
        ]),
    );
  }
  function frameworkCharts(prefix, v) {
    const f = frameworkData(), comps = arr(f.components);
    if (!comps.length) return;
    const solver = frameworkComponent("generic_optimizer"),
      timing = frameworkComponent("timing_framework"),
      index = frameworkComponent("index_enhancement"),
      counts = [
        ["输入字段", arr(solver.input_contract).length],
        ["约束条件", arr(solver.constraints).length],
        ["输出字段", arr(solver.output_contract).length],
        ["联动模块", arr(index.linked_modules).length],
      ];
    plot(
      prefix + "-framework-flow",
      [
        {
          type: "bar",
          orientation: "h",
          y: counts.map((x) => x[0]),
          x: counts.map((x) => x[1]),
          text: counts.map((x) => String(x[1])),
          textposition: "outside",
          marker: { color: [C.red, C.blue, C.gold, C.green] },
        },
      ],
      { margin: { l: 92, r: 30, t: 18, b: 42 }, showlegend: false },
    );
    const links = arr(f.module_linkage),
      labels = Array.from(new Set(links.flatMap((x) => [x.source, x.target]).filter(Boolean))),
      idx = new Map(labels.map((x, i) => [x, i]));
    plot(
      prefix + "-framework-link",
      [
        {
          type: "sankey",
          arrangement: "snap",
          node: { label: labels, pad: 12, thickness: 13, color: labels.map((_, i) => [C.grey, C.red, C.blue, C.gold, C.green][i % 5]) },
          link: { source: links.map((x) => idx.get(x.source)), target: links.map((x) => idx.get(x.target)), value: links.map(() => 1), color: "rgba(192,0,0,.20)" },
        },
      ],
      { margin: { l: 10, r: 10, t: 10, b: 10 } },
    );
    const stateValue = comps.map((x) => (String(x.status || "").startsWith("active") ? 1 : 0));
    plot(
      prefix + "-framework-state",
      [
        {
          type: "bar",
          x: comps.map((x) => x.name || x.id),
          y: stateValue,
          text: comps.map((x) => x.status || "--"),
          textposition: "auto",
          marker: { color: stateValue.map((x) => (x ? C.red : C.grey)) },
        },
      ],
      { yaxis: { range: [0, 1.2], tickvals: [0, 1], ticktext: ["未启用", "已启用"] }, showlegend: false },
    );
  }
  function timingScore(x) {
    const n = num(x);
    return n == null ? null : n;
  }
  function timingLegacy(v) {
    const t = obj(obj(v.result).timing_overlay),
      latest = obj(t.latest),
      periods = arr(t.periods),
      left = timingScore(latest.left_score),
      right = timingScore(latest.right_score),
      position = timingScore(latest.timing_position),
      budget = timingScore(latest.risk_budget_multiplier),
      valuation = obj(latest.valuation_regression),
      macro = obj(latest.dynamic_macro),
      trend = obj(latest.price_volume_trend),
      sentiment = obj(latest.nonlinear_sentiment);
    conclusion("宽基择时使用左侧估值/宏观均值回复与右侧趋势/情绪确认，输出仓位与风险预算；本页只展示择时信号，不替代组合优化约束。", false);
    root().innerHTML =
      '<div class="optimizer-shell">' +
      evidence([
        ["左侧得分", fixed(left, 3), "估值 / 宏观", "is-primary"],
        ["右侧得分", fixed(right, 3), "趋势 / 情绪"],
        ["主导方向", latest.active_side === "right" ? "右侧" : latest.active_side === "left" ? "左侧" : "--", "择时侧重"],
        ["目标仓位", fixed(position, 3), "timing position"],
        ["风险预算", fixed(budget, 3), "risk budget multiplier"],
        ["样本期数", periods.length + "期", "历史信号"],
      ]) +
      section(
        "择时信号",
        "左侧/右侧因子共同决定仓位和风险预算",
        charts([
          card("最新信号", "timing-latest", "左侧 / 右侧 / 仓位"),
          card("信号路径", "timing-path", "左侧 / 右侧 / 综合"),
          card("仓位预算", "timing-budget", "仓位与风险预算"),
        ]),
      ) +
      section(
        "维度拆解",
        "估值、宏观、趋势和情绪贡献",
        charts([
          card("四维得分", "timing-components", "当前分项"),
          card("侧向分布", "timing-regime", "左侧 / 右侧 / 中性"),
          card("情绪变量", "timing-sentiment", "情绪截面"),
        ]),
      ) +
      "</div>";
    plot(
      "timing-latest",
      [
        {
          type: "bar",
          x: ["左侧", "右侧", "仓位", "风险预算"],
          y: [left, right, position, budget],
          text: [left, right, position, budget].map((x) => fixed(x, 3)),
          textposition: "outside",
          marker: { color: [C.blue, C.red, C.gold, C.green] },
        },
      ],
      { yaxis: { range: [0, 1.1] }, showlegend: false },
    );
    const px = periods.map((x) => dateText(x.signal_date || x.date));
    plot(
      "timing-path",
      [
        { type: "scatter", mode: "lines", name: "左侧", x: px, y: periods.map((x) => num(x.left_score)), line: { color: C.blue, width: 2 } },
        { type: "scatter", mode: "lines", name: "右侧", x: px, y: periods.map((x) => num(x.right_score)), line: { color: C.red, width: 2 } },
        { type: "scatter", mode: "lines", name: "综合", x: px, y: periods.map((x) => num(x.composite_score)), line: { color: C.gold, width: 2 } },
      ],
      { yaxis: { range: [0, 1] }, hovermode: "x unified" },
    );
    plot(
      "timing-budget",
      [
        { type: "scatter", mode: "lines", name: "仓位", x: px, y: periods.map((x) => num(x.timing_position)), line: { color: C.red, width: 2.5 } },
        { type: "scatter", mode: "lines", name: "风险预算", x: px, y: periods.map((x) => num(x.risk_budget_multiplier)), line: { color: C.green, width: 2.5 } },
      ],
      { yaxis: { range: [0, 1.05] }, hovermode: "x unified" },
    );
    const comp = [
      ["估值回归", valuation.score],
      ["动态宏观", macro.score],
      ["价量趋势", trend.score],
      ["非线性情绪", sentiment.score],
    ];
    plot(
      "timing-components",
      [
        { type: "bar", x: comp.map((x) => x[0]), y: comp.map((x) => num(x[1])), marker: { color: [C.blue, C.blue, C.red, C.red] }, text: comp.map((x) => fixed(x[1], 3)), textposition: "outside" },
      ],
      { yaxis: { range: [0, 1.05] }, showlegend: false },
    );
    const counts = periods.reduce((out, row) => {
      const k = row.active_side === "right" ? "右侧" : row.active_side === "left" ? "左侧" : "中性";
      out[k] = (out[k] || 0) + 1;
      return out;
    }, {});
    plot(
      "timing-regime",
      [
        { type: "pie", labels: Object.keys(counts), values: Object.values(counts), hole: 0.48, marker: { colors: [C.red, C.blue, C.grey] } },
      ],
      { showlegend: true },
    );
    plot(
      "timing-sentiment",
      [
        {
          type: "bar",
          x: ["线性分位", "情绪得分", "换手率", "量比"],
          y: [sentiment.linear_percentile, sentiment.score, sentiment.turnover_rate, sentiment.volume_ratio].map(num),
          marker: { color: [C.grey, C.red, C.blue, C.gold] },
        },
      ],
      { showlegend: false },
    );
  }

  function timingAnnualTable(rows, benchmarkName) {
    const head = ["年度", "策略收益", benchmarkName || "基准", "超额收益", "最大回撤"];
    const body = arr(rows)
      .map(
        (r) =>
          '<tr><td>' +
          esc(r.year) +
          '</td><td>' +
          esc(r.strategy_return) +
          '</td><td>' +
          esc(r.benchmark_return) +
          '</td><td>' +
          esc(r.excess_return) +
          '</td><td>' +
          esc(r.max_drawdown) +
          '</td></tr>',
      )
      .join("");
    return (
      '<section class="table-panel optimizer-table-panel optimizer-mini-table"><div class="optimizer-table-title"><h3>年度收益明细</h3><p>与本页日频曲线同口径</p></div><div class="table-scroll"><table class="data-table optimizer-data-table"><thead><tr>' +
      head.map((x) => '<th>' + esc(x) + '</th>').join("") +
      '</tr></thead><tbody>' +
      (body || '<tr><td colspan="' + head.length + '">暂无年度收益数据</td></tr>') +
      '</tbody></table></div></section>'
    );
  }
  function optimizerMiniTable(title, note, rows, cols) {
    rows = arr(rows);
    cols = arr(cols);
    const head = cols.map((c) => '<th>' + esc(c.label || c.key || c) + '</th>').join('');
    const body = rows
      .map((row) =>
        '<tr>' +
        cols
          .map((c) => {
            const key = c.key || c,
              val = typeof c.format === 'function' ? c.format(row[key], row) : row[key];
            return '<td>' + esc(val == null ? '--' : val) + '</td>';
          })
          .join('') +
        '</tr>',
      )
      .join('');
    return (
      '<section class="table-panel optimizer-table-panel optimizer-mini-table"><div class="optimizer-table-title"><h3>' +
      esc(title) +
      '</h3><p>' +
      esc(note || String(rows.length) + ' 行') +
      '</p></div><div class="table-scroll"><table class="data-table optimizer-data-table"><thead><tr>' +
      head +
      '</tr></thead><tbody>' +
      (body || '<tr><td colspan="' + Math.max(cols.length, 1) + '">暂无表格数据</td></tr>') +
      '</tbody></table></div></section>'
    );
  }
  function timingFlow() {
    return (
      '<div class="optimizer-learning-flow optimizer-timing-flow">' +
      [
        ['因子构造', '宏观 / 量价 / 情绪 / 估值'],
        ['数据处理', '归一化 + Sigmoid + 方向校正'],
        ['指标检验', 'ICIR / t值 / 衰减 / 分层收益'],
        ['仓位信号', '进攻 + 防守 -> 五档仓位'],
        ['回测跟踪', '宽基指数日频净值与年度收益'],
      ]
        .map(
          (x, i) =>
            '<article><span>0' +
            (i + 1) +
            '</span><strong>' +
            esc(x[0]) +
            '</strong><p>' +
            esc(x[1]) +
            '</p></article>' +
            (i < 4 ? '<i class="optimizer-flow-arrow">›</i>' : ''),
        )
        .join('') +
      '</div>'
    );
  }
  function positionBackgroundShapes(dates, position) {
    dates = arr(dates);
    position = arr(position);
    const shapes = [];
    if (dates.length < 2 || !position.length) return shapes;
    const bucket = (v) => {
      const n = num(v);
      if (n == null) return 0.5;
      return Math.round(n * 4) / 4;
    };
    const color = (b) =>
      b >= 0.75
        ? 'rgba(192,0,0,.075)'
        : b >= 0.5
          ? 'rgba(255,192,0,.10)'
          : b > 0
            ? 'rgba(47,117,181,.075)'
            : 'rgba(128,128,128,.055)';
    let start = 0,
      last = bucket(position[0]);
    for (let i = 1; i < dates.length; i++) {
      const now = bucket(position[i]);
      if (now !== last || i === dates.length - 1) {
        shapes.push({ type: 'rect', xref: 'x', yref: 'paper', x0: dates[start], x1: dates[i], y0: 0, y1: 1, line: { width: 0 }, fillcolor: color(last), layer: 'below' });
        start = i;
        last = now;
      }
    }
    return shapes;
  }
  function timing(v) {
    const broad = obj(v.broad_index_timing), indices = arr(broad.indices);
    if (!indices.length) return timingLegacy(v);
    if (!state.timingIndex || !indices.some((x) => x.code === state.timingIndex)) {
      const preferred = indices.find((x) => x.code === '000905.SH') || indices[0];
      state.timingIndex = preferred.code;
    }
    const current = indices.find((x) => x.code === state.timingIndex) || indices[0],
      s = obj(current.series),
      dates = arr(s.dates),
      strategyNav = arr(s.strategy_nav),
      benchmarkNav = arr(s.benchmark_nav),
      relative = arr(s.relative_strength),
      position = arr(s.position),
      rawBucket = arr(s.raw_bucket_position),
      attackPath = arr(s.attack_score),
      defensePath = arr(s.defense_score),
      latest = obj(current.latest_signal),
      diag = obj(current.factor_diagnostics),
      familyRows = arr(diag.families),
      topFactors = arr(diag.top_factors).slice(0, 16);
    if (!state.timingFactor || !topFactors.some((x) => x.factor === state.timingFactor)) {
      state.timingFactor = topFactors.length ? topFactors[0].factor : '';
    }
    const selectedFactor = topFactors.find((x) => x.factor === state.timingFactor) || topFactors[0] || {},
      factorSeries = obj(selectedFactor.series),
      factorDates = arr(factorSeries.dates),
      factorSignal = arr(factorSeries.signal),
      rollingIc = arr(factorSeries.rolling_ic),
      cumulativeIc = arr(factorSeries.cumulative_ic),
      factorFamilyId = 'broad-timing-factor-family',
      factorTestId = 'broad-timing-factor-test',
      factorTrendId = 'broad-timing-factor-trend',
      rankicId = 'broad-timing-factor-rankic',
      navId = 'broad-timing-nav',
      posId = 'broad-timing-position',
      latestId = 'broad-timing-latest-four',
      annualId = 'broad-timing-annual',
      scatterId = 'broad-timing-frontier';
    const factorRows = topFactors.map((x) => ({
      factor: String(x.factor || '').replace(/^f_/, ''),
      family: x.family_label || x.family,
      direction: x.direction,
      ic: fixed(x.ic, 3),
      icir: fixed(x.icir, 2),
      t_value: fixed(x.t_value, 2),
      decay: fixed(x.ic_decay, 2),
      long_short: pct(x.long_short_return, 2),
      quality: fixed(x.quality, 3),
      admitted: x.admitted ? '通过' : '观察',
    }));
    conclusion('宽基择时按因子构造-数据处理-指标检验-仓位信号-回测跟踪组织：宏观、量价、情绪、估值四维先做有效性筛选，再合成进攻/防守信号并映射五档仓位。', false);
    root().innerHTML =
      '<div class="optimizer-shell">' +
      '<section class="optimizer-section"><header class="optimizer-section-head"><div><h2>查看设置</h2><p>宽基指数与单因子检验联动</p></div></header><div class="optimizer-control-grid"><label class="optimizer-control"><span>宽基指数</span><div><select id="broad-timing-index">' +
      indices.map((x) => '<option value="' + esc(x.code) + '">' + esc(x.index) + '</option>').join('') +
      '</select></div></label><label class="optimizer-control"><span>单因子</span><div><select id="broad-timing-factor">' +
      topFactors.map((x) => '<option value="' + esc(x.factor) + '">' + esc((x.family_label || x.family || '') + ' · ' + String(x.factor || '').replace(/^f_/, '')) + '</option>').join('') +
      '</select></div></label><label class="optimizer-control"><span>当前模型</span><div><input value="' + esc(current.model || '--') + '" disabled></div></label><label class="optimizer-control"><span>回测区间</span><div><input value="' + esc(dateText(current.start)) + ' - ' + esc(dateText(current.end)) + '" disabled></div></label></div></section>' +
      evidence([
        ['年化收益', pct(current.strategy_ann), '策略', 'is-primary'],
        ['年化超额', pct(current.excess_ann), '相对基准'],
        ['月度胜率', pct(current.monthly_excess_win_rate), '超额为正'],
        ['夏普', fixed(current.strategy_sharpe, 3), '基准 ' + fixed(current.benchmark_sharpe, 3)],
        ['最大回撤', pct(current.strategy_mdd), '基准 ' + pct(current.benchmark_mdd)],
        ['五档仓位', fixed(latest.bucket_position, 2), '0/0.25/0.5/0.75/1'],
      ]) +
      section('原理图', '因子构造-数据处理-指标检验-仓位信号-回测跟踪', timingFlow()) +
      section(
        '因子表格与检验',
        '方向性、单因子ICIR、t值、IC衰减和分层收益',
        charts([card('四维因子族检验', factorFamilyId, '通过因子 / 质量 / ICIR'), card('单因子质量排序', factorTestId, 'Top因子')]) +
          optimizerMiniTable('因子检验表', '单因子选择控件与该表同源', factorRows, [
            { key: 'factor', label: '因子' },
            { key: 'family', label: '类别' },
            { key: 'direction', label: '方向' },
            { key: 'ic', label: 'IC' },
            { key: 'icir', label: 'ICIR' },
            { key: 't_value', label: 't值' },
            { key: 'decay', label: 'IC衰减' },
            { key: 'long_short', label: '多空收益' },
            { key: 'quality', label: '质量分' },
            { key: 'admitted', label: '状态' },
          ]),
      ) +
      section('单因子趋势', '因子路径、指数走势、滚动RankIC与累计RankIC', charts([card('因子与指数趋势', factorTrendId, '选定单因子'), card('RankIC与累计', rankicId, '点时可得滞后')])) +
      section('仓位信号', '指数净值叠加仓位背景，四维趋势叠加进攻/防守强度', charts([card('指数趋势与仓位背景', navId, '橙=基准 灰=策略 红=相对强度'), card('仓位路径', posId, 'T+1执行仓位'), card('四维趋势与攻防', latestId, '宏观 / 量价 / 情绪 / 估值')])) +
      section('回测两图表', '年度收益表与趋势折线图同口径', charts([card('年度收益', annualId, '策略 / 基准 / 超额'), card('宽基模型比较', scatterId, '超额×夏普×月胜率')]) + timingAnnualTable(current.annual_rows, current.index)) +
      '</div>';
    const picker = document.getElementById('broad-timing-index');
    if (picker) {
      picker.value = state.timingIndex;
      picker.onchange = (e) => {
        state.timingIndex = e.target.value;
        timing(v);
      };
    }
    const factorPicker = document.getElementById('broad-timing-factor');
    if (factorPicker) {
      factorPicker.value = state.timingFactor;
      factorPicker.onchange = (e) => {
        state.timingFactor = e.target.value;
        timing(v);
      };
    }
    plot(
      factorFamilyId,
      [
        { type: 'bar', name: '通过因子', x: familyRows.map((x) => x.family_label), y: familyRows.map((x) => num(x.admitted_count)), marker: { color: C.red } },
        { type: 'scatter', mode: 'lines+markers', name: '平均质量', x: familyRows.map((x) => x.family_label), y: familyRows.map((x) => num(x.avg_quality)), yaxis: 'y2', line: { color: C.gold, width: 2.4 } },
        { type: 'scatter', mode: 'lines+markers', name: '平均ICIR', x: familyRows.map((x) => x.family_label), y: familyRows.map((x) => num(x.avg_icir)), yaxis: 'y3', line: { color: C.blue, width: 2.2 } },
      ],
      { yaxis: { title: '数量' }, yaxis2: { title: '质量', overlaying: 'y', side: 'right', range: [0, 1.05], showgrid: false }, yaxis3: { visible: false, overlaying: 'y' }, legend: { orientation: 'h', y: -0.22 } },
    );
    plot(
      factorTestId,
      [
        {
          type: 'bar',
          orientation: 'h',
          x: topFactors.slice(0, 12).map((x) => num(x.quality)),
          y: topFactors.slice(0, 12).map((x) => String(x.factor || '').replace(/^f_/, '')),
          text: topFactors.slice(0, 12).map((x) => fixed(x.icir, 2) + ' / ' + fixed(x.t_value, 1)),
          textposition: 'auto',
          marker: { color: topFactors.slice(0, 12).map((x) => (x.admitted ? C.red : C.grey)) },
        },
      ],
      { margin: { l: 132, r: 24, t: 12, b: 36 }, xaxis: { title: '质量分', range: [0, 1], gridcolor: C.grid }, showlegend: false },
    );
    const bmByDate = new Map(dates.map((d, i) => [d, benchmarkNav[i]])),
      bmOnFactor = factorDates.map((d) => num(bmByDate.get(d))),
      firstBm = bmOnFactor.find((x) => x != null) || 1;
    plot(
      factorTrendId,
      [
        { type: 'scatter', mode: 'lines', name: String(selectedFactor.factor || '').replace(/^f_/, '') + '得分', x: factorDates.length ? factorDates : dates, y: factorSignal.length ? factorSignal : attackPath, line: { color: C.red, width: 2.4 } },
        { type: 'scatter', mode: 'lines', name: current.index, x: factorDates.length ? factorDates : dates, y: (factorDates.length ? bmOnFactor : benchmarkNav).map((x) => (num(x) == null ? null : num(x) / firstBm)), yaxis: 'y2', line: { color: C.gold, width: 2.1 } },
      ],
      { hovermode: 'x unified', yaxis: { title: '因子得分', range: [0, 1.05] }, yaxis2: { title: '指数归一', overlaying: 'y', side: 'right', showgrid: false }, legend: { orientation: 'h', y: -0.22 } },
    );
    const h = obj(selectedFactor.horizon_ic), hasRic = rollingIc.some((x) => num(x) != null);
    plot(
      rankicId,
      hasRic
        ? [
            { type: 'scatter', mode: 'lines', name: '滚动RankIC', x: factorDates, y: rollingIc, line: { color: C.red, width: 2.2 } },
            { type: 'scatter', mode: 'lines', name: '累计RankIC', x: factorDates, y: cumulativeIc, yaxis: 'y2', line: { color: C.blue, width: 2.2 } },
          ]
        : [{ type: 'bar', x: ['5日', '20日', '60日'], y: [h['5'], h['20'], h['60']].map(num), marker: { color: [C.grey, C.red, C.blue] } }],
      hasRic ? { hovermode: 'x unified', yaxis: { title: '滚动RankIC' }, yaxis2: { title: '累计', overlaying: 'y', side: 'right', showgrid: false }, legend: { orientation: 'h', y: -0.22 } } : { yaxis: { title: 'RankIC' }, showlegend: false },
    );
    plot(
      navId,
      [
        { type: 'scatter', mode: 'lines', name: current.index, x: dates, y: benchmarkNav, line: { color: C.gold, width: 2.4 } },
        { type: 'scatter', mode: 'lines', name: '择时策略', x: dates, y: strategyNav, line: { color: '#bfbfbf', width: 2.4 } },
        { type: 'scatter', mode: 'lines', name: '相对强度（右轴）', x: dates, y: relative, yaxis: 'y2', line: { color: C.red, width: 2.8 } },
      ],
      { hovermode: 'x unified', xaxis: { type: 'date', showgrid: false }, yaxis: { title: '净值', gridcolor: C.grid }, yaxis2: { title: '相对强度', overlaying: 'y', side: 'right', showgrid: false }, shapes: positionBackgroundShapes(dates, rawBucket.length ? rawBucket : position), legend: { orientation: 'h', y: -0.22 } },
    );
    const posMin = position.length ? Math.min.apply(null, position.map((x) => (num(x) == null ? 0 : num(x)))) : 0;
    const posMax = position.length ? Math.max.apply(null, position.map((x) => (num(x) == null ? 1 : num(x)))) : 1;
    plot(
      posId,
      [{ type: 'scatter', mode: 'lines', name: '仓位', x: dates, y: position, fill: 'tozeroy', line: { color: C.red, width: 2.4 } }],
      { hovermode: 'x unified', xaxis: { type: 'date', showgrid: false }, yaxis: { title: '仓位', range: [Math.min(-0.12, posMin - 0.03), Math.max(1.18, posMax + 0.03)], gridcolor: C.grid }, showlegend: false },
    );
    plot(
      latestId,
      [
        { type: 'scatter', mode: 'lines', name: '宏观', x: dates, y: arr(s.macro_score).length ? arr(s.macro_score) : dates.map(() => latest.macro_score), line: { color: C.blue, width: 2 } },
        { type: 'scatter', mode: 'lines', name: '量价', x: dates, y: arr(s.price_volume_score).length ? arr(s.price_volume_score) : dates.map(() => latest.price_volume_score), line: { color: C.red, width: 2 } },
        { type: 'scatter', mode: 'lines', name: '情绪', x: dates, y: arr(s.sentiment_score).length ? arr(s.sentiment_score) : dates.map(() => latest.sentiment_score), line: { color: C.gold, width: 2 } },
        { type: 'scatter', mode: 'lines', name: '估值', x: dates, y: arr(s.valuation_score).length ? arr(s.valuation_score) : dates.map(() => latest.valuation_score), line: { color: C.green, width: 2 } },
        { type: 'scatter', mode: 'lines', name: '进攻', x: dates, y: attackPath, yaxis: 'y2', line: { color: '#8b0000', width: 2.4, dash: 'dot' } },
        { type: 'scatter', mode: 'lines', name: '防守', x: dates, y: defensePath, yaxis: 'y2', line: { color: '#2f75b5', width: 2.4, dash: 'dot' } },
      ],
      { hovermode: 'x unified', yaxis: { title: '四维因子', range: [0, 1.05] }, yaxis2: { title: '攻防', overlaying: 'y', side: 'right', range: [0, 1.05], showgrid: false }, legend: { orientation: 'h', y: -0.25 } },
    );
    const years = arr(current.annual_rows).filter((x) => String(x.year).indexOf('区间') < 0),
      pctNumber = (value) => Number(String(value || '').replace('%', ''));
    plot(
      annualId,
      [
        { type: 'bar', name: '策略', x: years.map((x) => x.year), y: years.map((x) => pctNumber(x.strategy_return)), marker: { color: '#bfbfbf' } },
        { type: 'bar', name: '基准', x: years.map((x) => x.year), y: years.map((x) => pctNumber(x.benchmark_return)), marker: { color: C.gold } },
        { type: 'scatter', mode: 'lines+markers', name: '超额', x: years.map((x) => x.year), y: years.map((x) => pctNumber(x.excess_return)), yaxis: 'y2', line: { color: C.red, width: 2.4 } },
      ],
      { barmode: 'group', yaxis: { title: '收益(%)', gridcolor: C.grid }, yaxis2: { title: '超额(%)', overlaying: 'y', side: 'right', showgrid: false }, legend: { orientation: 'h', y: -0.22 } },
    );
    plot(
      scatterId,
      [{ type: 'scatter', mode: 'markers+text', x: indices.map((x) => num(x.excess_ann) * 100), y: indices.map((x) => num(x.strategy_sharpe)), text: indices.map((x) => x.index), textposition: 'top center', marker: { size: indices.map((x) => Math.max(10, Math.abs(num(x.strategy_mdd) || 0) * 70)), color: indices.map((x) => num(x.monthly_excess_win_rate) * 100), colorscale: 'Portland', showscale: true, colorbar: { title: '月胜率%' } } }],
      { xaxis: { title: '年化超额(%)', zeroline: false }, yaxis: { title: '夏普', zeroline: false }, showlegend: false },
    );
  }

  function home(v) {
    const o = metric(v, "constrained_optimizer"),
      sc = scope(v);
    conclusion(
      v.diagnostic
        ? publicationMessage(v)
        : v.formal
        ? "当前公开模型通过完整窗口、精确求解和全部约束审计。"
        : "当前公开模型已通过精确求解与约束审计；绩效仅为最长连续段诊断。",
      !v.formal,
    );
    root().innerHTML =
      '<div class="optimizer-shell">' +
      strip(
        [
          ["任务", v.run.id],
          ["模型", "HiGHS + Clarabel"],
          ["支持集", "50只"],
          ["认证", solver(v.result).cert ? "通过" : "未通过"],
        ],
        v.diagnostic ? "is-blocked" : "is-ready",
      ) +
      evidence([
        ["资产池", "500只", "中证500"],
        ["目标持仓", "50只", "精确整数支持集"],
        ["年化收益", pct(o.annual_return), sc, "is-primary"],
        ["夏普", fixed(o.sharpe), sc],
        ["跟踪误差", pct(o.tracking_error), "年化"],
        ["信息比率", fixed(o.information_ratio), sc],
      ]) +
      section(
        "策略结果",
        sc,
        charts([
          card("净值曲线", "home-nav", sc),
          card("累计超额", "home-excess", sc),
          card("收益、波动与跟踪误差", "home-metric", sc),
          card("夏普与信息比率", "home-ratio", sc),
        ]),
      ) +
      section(
        "优化效果",
        "精确选股、连续权重与约束边界",
        charts([
          card("支持集求解流程", "home-funnel", "500只到50只"),
          card("约束利用率", "home-constraint", "100%为约束边界"),
        ]),
      ) +
      "</div>";
    performance("home", v);
    funnel("home-funnel", v.result);
    constraints("home-constraint", v.result);
    frameworkCharts("home", v);
  }
  function universe() {
    const s = obj(state.snapshot),
      a = arr(s.assets),
      ind = arr(s.industry_summary),
      sel = a.filter((x) => x.selected),
      q = obj(s.data_quality);
    conclusion(
      "资产池画像完全来自当前中证500截面：得分、行业、风格、估值、成交与50只优化结果同源对齐。",
    );
    root().innerHTML =
      '<div class="optimizer-shell">' +
      evidence([
        ["成分股", a.length + "只", "固定截面", "is-primary"],
        ["入选", sel.length + "只", "优化器支持集"],
        ["行业", ind.length + "个", "申万一级"],
        ["可交易", (q.tradable_count || "--") + "只", "交易过滤"],
        ["估值覆盖", (q.valuation_complete || "--") + "/500", "总市值"],
        ["行情覆盖", (q.market_complete || "--") + "/500", "成交额"],
      ]) +
      section(
        "得分与选择",
        "全资产池分布、入选位置与市值关系",
        charts([
          card("得分分布", "pool-score", "全资产池与入选"),
          card("市值与得分", "pool-cap", "红色为入选"),
          card("基准权重集中度", "pool-cum", "累计权重"),
        ]),
      ) +
      section(
        "行业画像",
        "基准、组合、数量与平均得分",
        charts([
          card("行业权重", "pool-ind-w", "组合与基准"),
          card("行业股票数", "pool-ind-n", "资产池与入选"),
          card("行业平均得分", "pool-ind-s", "当期截面"),
        ]),
      ) +
      section(
        "风格与估值",
        "资产池和入选组合的分布",
        charts([
          card("风格分布", "pool-style", "箱线图"),
          card("估值分布", "pool-value", "PE与PB"),
        ]),
      ) +
      "</div>";
    plot(
      "pool-score",
      [
        {
          type: "histogram",
          x: a.map((x) => num(x.score)),
          nbinsx: 28,
          opacity: 0.55,
          name: "资产池",
          marker: { color: C.grey },
        },
        {
          type: "histogram",
          x: sel.map((x) => num(x.score)),
          nbinsx: 18,
          opacity: 0.75,
          name: "入选",
          marker: { color: C.red },
        },
      ],
      { barmode: "overlay" },
    );
    plot(
      "pool-cap",
      [
        {
          type: "scatter",
          mode: "markers",
          x: a.map((x) => num(get(x, "valuation.total_mv"))),
          y: a.map((x) => num(x.score)),
          text: a.map((x) => x.name),
          marker: {
            size: a.map((x) => (x.selected ? 9 : 5)),
            color: a.map((x) => (x.selected ? C.red : C.grey)),
            opacity: 0.6,
          },
        },
      ],
      {
        xaxis: { type: "log", title: "总市值（对数）" },
        yaxis: { title: "得分" },
        showlegend: false,
      },
    );
    const z = a
      .slice()
      .sort(
        (x, y) =>
          (num(y.benchmark_weight) || 0) - (num(x.benchmark_weight) || 0),
      );
    let sum = 0;
    plot(
      "pool-cum",
      [
        {
          type: "scatter",
          mode: "lines",
          x: z.map((_, i) => i + 1),
          y: z.map((x) => (sum += num(x.benchmark_weight) || 0)),
          line: { color: C.red, width: 3 },
          fill: "tozeroy",
        },
      ],
      {
        xaxis: { title: "股票数" },
        yaxis: { tickformat: ".0%" },
        showlegend: false,
      },
    );
    plot(
      "pool-ind-w",
      [
        {
          type: "bar",
          x: ind.map((x) => x.industry),
          y: ind.map((x) => num(x.benchmark_weight)),
          name: "基准",
          marker: { color: C.grey },
        },
        {
          type: "bar",
          x: ind.map((x) => x.industry),
          y: ind.map((x) => num(x.portfolio_weight)),
          name: "组合",
          marker: { color: C.red },
        },
      ],
      { barmode: "group", yaxis: { tickformat: ".1%" } },
    );
    plot(
      "pool-ind-n",
      [
        {
          type: "bar",
          x: ind.map((x) => x.industry),
          y: ind.map((x) => num(x.asset_count)),
          name: "资产池",
          marker: { color: C.grey },
        },
        {
          type: "bar",
          x: ind.map((x) => x.industry),
          y: ind.map((x) => num(x.selected_count)),
          name: "入选",
          marker: { color: C.red },
        },
      ],
      { barmode: "group" },
    );
    plot(
      "pool-ind-s",
      [
        {
          type: "bar",
          x: ind.map((x) => x.industry),
          y: ind.map((x) => num(x.average_score)),
          marker: {
            color: ind.map((x) =>
              (num(x.average_score) || 0) >= 0 ? C.red : C.blue,
            ),
          },
        },
      ],
      { showlegend: false },
    );
    const ks = ["size", "value", "momentum", "liquidity"];
    plot(
      "pool-style",
      ks.flatMap((k) => [
        {
          type: "box",
          y: a.map((x) => num(get(x, "style." + k))),
          name: STYLE[k] + "·资产池",
          marker: { color: C.grey },
          boxpoints: false,
        },
        {
          type: "box",
          y: sel.map((x) => num(get(x, "style." + k))),
          name: STYLE[k] + "·入选",
          marker: { color: C.red },
          boxpoints: false,
        },
      ]),
      {},
    );
    plot(
      "pool-value",
      [
        {
          type: "box",
          y: a
            .map((x) => num(get(x, "valuation.pe_ttm")))
            .filter((x) => x > 0 && x < 200),
          name: "PE_TTM",
          marker: { color: C.red },
        },
        {
          type: "box",
          y: a
            .map((x) => num(get(x, "valuation.pb")))
            .filter((x) => x > 0 && x < 30),
          name: "PB",
          marker: { color: C.blue },
        },
      ],
      {},
    );
  }
  function alpha() {
    const s = obj(state.snapshot),
      a = arr(s.assets),
      sc = obj(s.score),
      w = obj(sc.factor_weights),
      h = arr(s.factor_weight_history);
    conclusion(
      "Alpha得分保留走样IC冠军信号；行业与四类风格暴露由组合优化器硬约束控制，不在得分层预先残差化。",
    );
    root().innerHTML =
      '<div class="optimizer-shell">' +
      evidence([
        ["得分日期", dateText(sc.signal_date), "点时截面", "is-primary"],
        ["资产数", a.length + "只", "中证500"],
        ["模型数", Object.keys(w).length + "个", "非负走样IC权重"],
        ["历史期数", h.length + "期", "固定月频"],
        ["得分处理", "保留原始Alpha", "优化器控制暴露"],
        [
          "未来数据",
          "未使用",
          "成熟期早于信号期",
        ],
      ]) +
      section(
        "Alpha融合",
        "当前权重、历史权重与原始得分关系",
        charts([
          card("当前模型权重", "alpha-w", "走样IC权重"),
          card("模型权重历史", "alpha-h", "月频"),
          card("原始得分与优化器输入", "alpha-r", "500只"),
        ]),
      ) +
      section(
        "暴露审计",
        "行业与风格暴露由组合约束控制",
        charts([
          card("风格相关性", "alpha-style", "得分与四类风格"),
          card("行业Alpha均值", "alpha-ind", "绝对值最高行业"),
          card("得分分布", "alpha-dist", "原始与优化器输入"),
        ]),
      ) +
      "</div>";
    const names = Object.keys(w);
    plot(
      "alpha-w",
      [
        {
          type: "bar",
          orientation: "h",
          y: names.map((k) => FACTOR[k] || k),
          x: names.map((k) => num(w[k])),
          marker: {
            color: names.map((k) => ((num(w[k]) || 0) >= 0 ? C.red : C.blue)),
          },
        },
      ],
      { margin: { l: 130, r: 18, t: 16, b: 42 }, showlegend: false },
    );
    const all = Array.from(
        new Set(h.flatMap((x) => Object.keys(obj(x.weights)))),
      ),
      colors = [
        C.red,
        C.blue,
        C.gold,
        C.green,
        C.grey,
        "#e05a4f",
        "#7030a0",
        "#00a6a6",
      ];
    plot(
      "alpha-h",
      all.map((k, i) => ({
        type: "scatter",
        mode: "lines",
        x: h.map((x) => dateText(x.signal_date)),
        y: h.map((x) => num(obj(x.weights)[k])),
        name: FACTOR[k] || k,
        line: { color: colors[i % 8] },
      })),
      { hovermode: "x unified" },
    );
    plot(
      "alpha-r",
      [
        {
          type: "scatter",
          mode: "markers",
          x: a.map((x) => num(x.raw_score)),
          y: a.map((x) => num(x.score)),
          text: a.map((x) => x.name),
          marker: {
            size: a.map((x) => (x.selected ? 9 : 5)),
            color: a.map((x) => (x.selected ? C.red : C.grey)),
            opacity: 0.65,
          },
        },
      ],
      {
        xaxis: { title: "原始得分" },
        yaxis: { title: "优化器输入得分" },
        showlegend: false,
      },
    );
    const st = ["size", "value", "momentum", "liquidity"];
    plot(
      "alpha-style",
      [
        {
          type: "bar",
          x: st.map((k) => STYLE[k] || k),
          y: st.map((k) =>
            corr(
              a.map((x) => num(get(x, "style." + k))),
              a.map((x) => num(x.raw_score)),
            ),
          ),
          name: "原始得分",
          marker: { color: C.grey },
        },
        {
          type: "bar",
          x: st.map((k) => STYLE[k] || k),
          y: st.map((k) =>
            corr(
              a.map((x) => num(get(x, "style." + k))),
              a.map((x) => num(x.score)),
            ),
          ),
          name: "优化器输入",
          marker: { color: C.red },
        },
      ],
      { barmode: "group" },
    );
    const industryAlpha = Object.values(
      a.reduce((out, row) => {
        const key = row.industry || "未知";
        if (!out[key]) out[key] = { industry: key, raw: [], optimized: [] };
        out[key].raw.push(num(row.raw_score));
        out[key].optimized.push(num(row.score));
        return out;
      }, {}),
    )
      .map((row) => ({
        industry: row.industry,
        raw: mean(row.raw),
        optimized: mean(row.optimized),
      }))
      .sort((x, y) => Math.abs(y.raw || 0) - Math.abs(x.raw || 0))
      .slice(0, 16);
    plot(
      "alpha-ind",
      [
        {
          type: "bar",
          x: industryAlpha.map((x) => x.industry),
          y: industryAlpha.map((x) => x.raw),
          name: "原始得分",
          marker: { color: C.grey },
        },
        {
          type: "bar",
          x: industryAlpha.map((x) => x.industry),
          y: industryAlpha.map((x) => x.optimized),
          name: "优化器输入",
          marker: { color: C.red },
        },
      ],
      { barmode: "group" },
    );
    plot(
      "alpha-dist",
      [
        {
          type: "histogram",
          x: a.map((x) => num(x.raw_score)),
          opacity: 0.55,
          name: "原始",
          marker: { color: C.grey },
        },
        {
          type: "histogram",
          x: a.map((x) => num(x.score)),
          opacity: 0.65,
          name: "优化器输入",
          marker: { color: C.red },
        },
      ],
      { barmode: "overlay" },
    );
  }
  function beta() {
    const a = arr(obj(state.snapshot).assets),
      s = a.filter((x) => x.selected),
      ks = ["size", "value", "momentum", "liquidity"],
      bm = ks.map((k) => wmean(a, "style." + k, "benchmark_weight")),
      pf = ks.map((k) => wmean(s, "style." + k, "portfolio_weight")),
      co = ks.map((k) =>
        corr(
          a.map((x) => get(x, "style." + k)),
          a.map((x) => x.score),
        ),
      );
    conclusion(
      "SmartBeta使用规模、价值、动量、流动性四类可控暴露；进入Alpha解释与组合硬约束，但不预先修改Alpha分数，也不虚构独立净值。",
    );
    root().innerHTML =
      '<div class="optimizer-shell">' +
      evidence(
        ks.map((k, i) => [
          STYLE[k],
          fixed(pf[i] - bm[i], 3),
          "相对基准主动暴露",
          i === 0 ? "is-primary" : "",
        ]),
      ) +
      section(
        "风格分布",
        "资产池、入选股票与组合加权暴露",
        charts(
          ks.map((k) => card(STYLE[k] + "分布", "beta-" + k, "资产池与入选")),
        ),
      ) +
      section(
        "风格控制",
        "组合、基准、得分相关性与约束边界",
        charts([
          card("组合与基准暴露", "beta-exp", "加权均值"),
          card("风格与Alpha相关性", "beta-cor", "当期截面"),
          card("风格约束利用率", "beta-use", "相对上限"),
        ]),
      ) +
      "</div>";
    ks.forEach((k) =>
      plot(
        "beta-" + k,
        [
          {
            type: "violin",
            y: a.map((x) => num(get(x, "style." + k))),
            name: "资产池",
            box: { visible: true },
            line: { color: C.grey },
          },
          {
            type: "violin",
            y: s.map((x) => num(get(x, "style." + k))),
            name: "入选",
            box: { visible: true },
            line: { color: C.red },
          },
        ],
        { violinmode: "group" },
      ),
    );
    plot(
      "beta-exp",
      [
        {
          type: "bar",
          x: ks.map((k) => STYLE[k]),
          y: bm,
          name: "基准",
          marker: { color: C.grey },
        },
        {
          type: "bar",
          x: ks.map((k) => STYLE[k]),
          y: pf,
          name: "组合",
          marker: { color: C.red },
        },
      ],
      { barmode: "group" },
    );
    plot(
      "beta-cor",
      [
        {
          type: "bar",
          x: ks.map((k) => STYLE[k]),
          y: co,
          marker: { color: co.map((x) => ((x || 0) >= 0 ? C.red : C.blue)) },
        },
      ],
      { yaxis: { range: [-1, 1] }, showlegend: false },
    );
    const active = ks.map((_, i) => pf[i] - bm[i]),
      lim = state.config.style.max_abs_exposure;
    plot(
      "beta-use",
      [
        {
          type: "bar",
          x: ks.map((k) => STYLE[k]),
          y: active.map((x) => Math.abs(x) / lim),
          marker: {
            color: active.map((x) =>
              Math.abs(x) / lim > 0.9 ? C.red : C.green,
            ),
          },
        },
      ],
      {
        yaxis: { tickformat: ".0%", range: [0, 1.15] },
        shapes: [
          {
            type: "line",
            x0: -0.5,
            x1: 3.5,
            y0: 1,
            y1: 1,
            line: { color: C.red, dash: "dot" },
          },
        ],
        showlegend: false,
      },
    );
  }
  function risk(v) {
    const o = metric(v, "constrained_optimizer"),
      sc = scope(v),
      ws = weights(v.result).map(named);
    conclusion(
      v.diagnostic
        ? publicationMessage(v)
        : "风险模型把跟踪误差、单股主动权重、行业偏离、四类风格和换手同时纳入求解，并做独立残差复核。",
    );
    root().innerHTML =
      '<div class="optimizer-shell">' +
      evidence([
        ["跟踪误差", pct(o.tracking_error), "年化", "is-primary"],
        ["最大回撤", pct(o.max_drawdown), sc],
        ["年化波动", pct(o.volatility), sc],
        [
          "最大主动权重",
          pct(
            Math.max(0, ...ws.map((x) => Math.abs(num(x.active_weight) || 0))),
          ),
          "单股",
        ],
        ["平均换手", pct(o.turnover), sc],
        ["交易成本", pct(o.cost), sc],
      ]) +
      section(
        "风险与回撤",
        sc,
        charts([
          card("风险收益", "risk-return", "四种策略"),
          card("回撤路径", "risk-dd", sc),
          card("约束利用率", "risk-use", "跟踪、换手、行业与风格"),
        ]),
      ) +
      section(
        "暴露与数值认证",
        "行业、风格、主动权重与残差",
        charts([
          card("行业主动暴露", "risk-industry", "相对基准"),
          card("风格主动暴露", "risk-style", "四类风格"),
          card("主动权重分布", "risk-active", "50只"),
          card("独立约束残差", "risk-residual", "对数坐标"),
        ]),
      ) +
      "</div>";
    plot(
      "risk-return",
      [
        {
          type: "scatter",
          mode: "markers+text",
          x: v.metrics.map((x) => num(x.volatility)),
          y: v.metrics.map((x) => num(x.annual_return)),
          text: v.metrics.map((x) => LABEL[x.key]),
          textposition: "top center",
          marker: {
            size: v.metrics.map((x) =>
              x.key === "constrained_optimizer" ? 16 : 11,
            ),
            color: v.metrics.map((x) =>
              x.key === "constrained_optimizer" ? C.red : C.grey,
            ),
          },
        },
      ],
      {
        xaxis: { tickformat: ".1%", title: "波动" },
        yaxis: { tickformat: ".1%", title: "收益" },
        showlegend: false,
      },
    );
    const color = {
      benchmark: C.grey,
      direct_score_top50: C.blue,
      same_support_score_weighted: C.gold,
      constrained_optimizer: C.red,
    };
    plot(
      "risk-dd",
      Object.keys(LABEL).map((k) =>
        line(
          dd(v.nav[k] || []),
          LABEL[k],
          color[k],
          k === "constrained_optimizer" ? 3 : 1.8,
        ),
      ),
      { yaxis: { tickformat: ".1%" }, hovermode: "x unified" },
    );
    constraints("risk-use", v.result, 14);
    portfolio("risk", v.result, ws);
    const i = solver(v.result),
      a = Math.max(
        Math.abs(num(i.p1.max_linear_constraint_violation) || 1e-18),
        1e-18,
      ),
      b = Math.max(Math.abs(i.res || 1e-18), 1e-18);
    plot(
      "risk-residual",
      [
        {
          type: "bar",
          x: ["Phase-I", "Phase-II"],
          y: [a, b],
          text: [a.toExponential(2), b.toExponential(2)],
          textposition: "outside",
          marker: { color: [C.blue, C.red] },
        },
      ],
      { yaxis: { type: "log" }, showlegend: false },
    );
  }
  function tracking(v) {
    const o = metric(v, "constrained_optimizer"),
      a = obj(v.result.backtest_audit),
      c = obj(a.continuity),
      l = obj(a.longest_contiguous_segment),
      sc = scope(v);
    conclusion(
      v.diagnostic
        ? publicationMessage(v)
        : v.formal
        ? "组合跟踪页展示完整连续窗口正式结果。"
        : "当前只有" +
            (l.periods || "--") +
            "个连续诊断期；收益、夏普和信息比率不得视为正式业绩。",
      !v.formal,
    );
    root().innerHTML =
      '<div class="optimizer-shell">' +
      strip(
        [
          ["绩效口径", v.formal ? "正式" : "诊断"],
          ["完整收益期", String(c.complete_return_periods || "--")],
          ["请求月份", String(c.requested_calendar_months || "--")],
          ["最长连续段", (l.periods || "--") + "期"],
          ["阻断调仓", String(a.rebalance_blocked_periods || 0)],
        ],
        v.formal ? "is-ready" : "is-blocked",
      ) +
      evidence([
        ["年化收益", pct(o.annual_return), sc, "is-primary"],
        ["夏普", fixed(o.sharpe), sc],
        ["信息比率", fixed(o.information_ratio), sc],
        ["跟踪误差", pct(o.tracking_error), "年化"],
        ["换手", pct(o.turnover), sc],
        ["最大回撤", pct(o.max_drawdown), sc],
      ]) +
      section(
        "组合跟踪",
        sc,
        charts([
          card("净值曲线", "track-nav", sc),
          card("累计超额", "track-excess", sc),
          card("回撤", "track-drawdown", sc),
          card("滚动信息比率", "track-ir", sc),
          card("收益、波动与跟踪误差", "track-metric", sc),
          card("夏普与信息比率", "track-ratio", sc),
        ]),
      ) +
      section(
        "调仓与覆盖",
        "交易方向、认证期、阻断与延续",
        charts([
          card("主要调仓", "track-trades", "买入与卖出"),
          card("回测覆盖", "track-coverage", "期数审计"),
        ]),
      ) +
      "</div>";
    performance("track", v);
    tradeAudit("track", v.result, trades(v.result));
  }
  function indexFlow() {
    const steps = [
      ['默认参数', '中证500 / 50只 / 周度求解 / 月频得分'],
      ['Beta', '行业轮动六维信号进入行业预算'],
      ['Alpha', '因子实验室champion综合得分'],
      ['约束加强', '权重 / 行业 / 风格 / TE / 换手 / 名单'],
      ['两阶段求解', 'HiGHS支持集 + Clarabel权重'],
      ['回测跟踪', '净值 / 超额 / 年度 / 约束审计'],
    ];
    return '<div class="optimizer-index-flow">' + steps.map((x, i) => '<article><span>0' + (i + 1) + '</span><strong>' + esc(x[0]) + '</strong><p>' + esc(x[1]) + '</p></article>').join('<i class="optimizer-flow-arrow">›</i>') + '</div>';
  }
  function indexFormula() {
    const blocks = [
      ['目标函数', 'max_w  λα αᵀw + λβ βᵀw − λr ‖Lᵀ(w−b)‖₂² − λs ‖w−s‖₂² − λt ‖w−p‖₁ − λc cᵀ|w−p|', 'Alpha负责个股超额，Beta负责行业轮动预算，风险/贴近/换手/成本共同约束。'],
      ['整数支持集', 'yᵢ∈{0,1}，Σyᵢ=50，ℓᵢyᵢ≤wᵢ≤uᵢyᵢ，1ᵀw=1，w≥0', 'HiGHS先在全资产池里选出满足硬约束的50只股票。'],
      ['行业Beta约束', '|Gᵀ(w−b)−β_ind|≤δ_ind', '行业轮动信号只改变行业主动预算，不直接生成股票权重。'],
      ['风格与跟踪', '|Fᵀ(w−b)|≤δ_style，‖Lᵀ(w−b)‖₂≤TE_max', '风格暴露保持可控，跟踪误差用二阶锥约束认证。'],
      ['交易与名单', '0.5‖w−p‖₁≤τ_max，wᵢ≤ρ·ADVᵢ/AUM，White/Black List', '换手、流动性和人工名单共同进入最终可行域。'],
    ];
    return '<div class="optimizer-formula-grid">' + blocks.map((x) => '<article class="optimizer-equation-card"><h3>' + esc(x[0]) + '</h3><div class="optimizer-equation">' + esc(x[1]) + '</div><p>' + esc(x[2]) + '</p></article>').join('') + '</div>';
  }
  function indexAnnualRows(v) {
    const s = arr(v.nav.constrained_optimizer), b = arr(v.nav.benchmark), bmap = new Map(b.map((r) => [dateText(r.date).slice(0, 10), r.nav]));
    const groups = {};
    s.forEach((row) => {
      const d = dateText(row.date).slice(0, 10), y = d.slice(0, 4), bv = bmap.get(d);
      if (!groups[y]) groups[y] = [];
      if (num(row.nav) != null && num(bv) != null) groups[y].push({ date: d, strategy: num(row.nav), benchmark: num(bv) });
    });
    const calc = (rows, key) => rows.length > 1 ? rows[rows.length - 1][key] / rows[0][key] - 1 : null;
    const mdd = (rows) => {
      let peak = -Infinity, worst = 0;
      rows.forEach((r) => { peak = Math.max(peak, r.strategy); if (peak > 0) worst = Math.min(worst, r.strategy / peak - 1); });
      return worst;
    };
    const years = Object.keys(groups).sort();
    const out = years.map((y) => {
      const rows = groups[y], sr = calc(rows, 'strategy'), br = calc(rows, 'benchmark');
      return { year: y, strategy_return: pct(sr, 1), benchmark_return: pct(br, 1), excess_return: pct(sr == null || br == null ? null : sr - br, 1), max_drawdown: pct(mdd(rows), 1) };
    });
    const all = years.flatMap((y) => groups[y]), ar = calc(all, 'strategy'), ab = calc(all, 'benchmark');
    if (all.length) out.push({ year: '区间年化', strategy_return: pct(metric(v, 'constrained_optimizer').annual_return, 1), benchmark_return: pct(metric(v, 'benchmark').annual_return, 1), excess_return: pct(metric(v, 'constrained_optimizer').annual_excess_return, 1), max_drawdown: pct(metric(v, 'constrained_optimizer').max_drawdown, 1) });
    return out;
  }
  function indexSignalRows(monthly) {
    return arr(monthly.holdings).slice(-8).reverse().map((x) => ({
      signal_date: dateText(x.signal_date),
      execution_date: dateText(x.execution_date),
      top1: arr(x.names)[0] || '--',
      top2: arr(x.names)[1] || '--',
      top3: arr(x.names)[2] || '--',
      top4: arr(x.names)[3] || '--',
      top5: arr(x.names)[4] || '--',
      turnover: pct(x.turnover, 1),
    }));
  }
  function plotIndexBacktest(prefix, v) {
    const bm = arr(v.nav.benchmark), opt = arr(v.nav.constrained_optimizer), rel = excess(opt, bm).map((x) => ({ date: x.date, value: 1 + x.value }));
    plot(
      prefix + '-line',
      [
        line(bm, '中证500', C.gold, 2.4),
        line(opt, '指数增强', '#bfbfbf', 2.4),
        line(rel, '相对强度（右轴）', C.red, 2.8),
      ].map((trace) => trace.name.indexOf('相对') >= 0 ? Object.assign(trace, { yaxis: 'y2' }) : trace),
      { hovermode: 'x unified', xaxis: { type: 'date', showgrid: false }, yaxis: { title: '净值' }, yaxis2: { title: '相对强度', overlaying: 'y', side: 'right', showgrid: false }, legend: { orientation: 'h', y: -0.22 } },
    );
    const rows = indexAnnualRows(v).filter((x) => String(x.year).indexOf('区间') < 0), pctNumber = (value) => Number(String(value || '').replace('%', ''));
    plot(
      prefix + '-annual',
      [
        { type: 'bar', name: '策略', x: rows.map((x) => x.year), y: rows.map((x) => pctNumber(x.strategy_return)), marker: { color: '#bfbfbf' } },
        { type: 'bar', name: '中证500', x: rows.map((x) => x.year), y: rows.map((x) => pctNumber(x.benchmark_return)), marker: { color: C.gold } },
        { type: 'scatter', mode: 'lines+markers', name: '超额', x: rows.map((x) => x.year), y: rows.map((x) => pctNumber(x.excess_return)), yaxis: 'y2', line: { color: C.red, width: 2.4 } },
      ],
      { barmode: 'group', yaxis: { title: '收益(%)' }, yaxis2: { title: '超额(%)', overlaying: 'y', side: 'right', showgrid: false }, legend: { orientation: 'h', y: -0.22 } },
    );
  }
  async function indexPage(v) {
    let rotation = {}, rotationError = '';
    try {
      rotation = await loadRotation(false);
    } catch (e) {
      rotationError = e.message;
    }
    const result = obj(v.result), selectedWeights = weights(result).map(named), o = metric(v, 'constrained_optimizer'), sc = obj(state.snapshot), score = obj(sc.score), monthly = obj(obj(obj(rotation.industry).frequencies).monthly), ranking = arr(monthly.ranking), research = obj(monthly.research_result), rotMetrics = obj(monthly.metrics), signalRows = indexSignalRows(monthly), industryRows = arr(sc.industry_summary).slice().sort((a, b) => Math.abs(num(b.active_weight) || 0) - Math.abs(num(a.active_weight) || 0)).slice(0, 16);
    conclusion('指数增强使用中证500资产池、行业轮动Beta预算、因子实验室Alpha得分和组合优化器两阶段求解；本页统一展示信号、持仓、公式和回测。', false);
    root().innerHTML =
      '<div class="optimizer-shell optimizer-index-shell">' +
      evidence([
        ['基准', '中证500', '000905.SH', 'is-primary'],
        ['资产池', String(arr(sc.assets).length || 500) + '只', '精确成分股'],
        ['目标持仓', String(selectedWeights.length || 50) + '只', 'HiGHS支持集'],
        ['Alpha日期', dateText(score.signal_date), score.score_name || '因子实验室'],
        ['年化超额', pct(o.annual_excess_return), '当前认证组合'],
        ['信息比率', fixed(o.information_ratio, 3), '相对中证500'],
      ]) +
      section('原理图', '默认参数-beta-alpha-约束加强-两阶段求解-回测跟踪', indexFlow()) +
      section('行业轮动历史信号表+回测', rotationError ? '行业轮动快照读取失败：' + rotationError : '月频Beta信号只进入行业主动预算', charts([card('行业轮动回测', 'idx-ind-nav', '策略 / 等权 / 相对强度'), card('最新行业排序', 'idx-ind-rank', '行业轮动Beta')]) + optimizerMiniTable('历史信号表', '最近8期月频行业信号', signalRows, [{ key: 'signal_date', label: '信号日期' }, { key: 'execution_date', label: '执行日期' }, { key: 'top1', label: 'Top1' }, { key: 'top2', label: 'Top2' }, { key: 'top3', label: 'Top3' }, { key: 'top4', label: 'Top4' }, { key: 'top5', label: 'Top5' }, { key: 'turnover', label: '换手' }])) +
      section('个股打分回测', '因子实验室champion得分、同支持集得分加权与约束优化结果对比', charts([card('净值曲线', 'idx-score-nav', '四组策略'), card('累计超额', 'idx-score-excess', '相对基准'), card('收益/波动/TE', 'idx-score-metric', '指标矩阵'), card('夏普/IR', 'idx-score-ratio', '指标矩阵')])) +
      section('最新建议行业持仓', '组合行业主动偏离与行业轮动Beta同时展示', charts([card('行业主动权重', 'idx-ind-active', '组合-基准'), card('行业轮动当前Top', 'idx-ind-current', '六维Beta排序')])) +
      section('最新建议个股持仓', '50只股票的目标权重、主动权重和约束暴露', charts([card('个股权重', 'idx-stock-weight', '组合/基准/主动'), card('主动权重分布', 'idx-stock-active', '50只'), card('行业主动暴露', 'idx-stock-industry', '相对基准'), card('风格主动暴露', 'idx-stock-style', '相对基准')]) + optimizerMiniTable('最新个股持仓', '按目标权重排序前20', selectedWeights.slice().sort((a, b) => (num(b.weight) || 0) - (num(a.weight) || 0)).slice(0, 20).map((x) => ({ code: x.code, name: x.name, weight: pct(x.weight, 2), benchmark_weight: pct(x.benchmark_weight, 2), active_weight: pct(x.active_weight, 2) })), [{ key: 'code', label: '代码' }, { key: 'name', label: '名称' }, { key: 'weight', label: '目标权重' }, { key: 'benchmark_weight', label: '基准权重' }, { key: 'active_weight', label: '主动权重' }])) +
      section('目标问题+约束求解公式', 'Beta+Alpha+约束的同一个两阶段求解问题', indexFormula() + charts([card('约束利用率', 'idx-constraint', '硬约束边界'), card('两阶段求解', 'idx-solver', 'HiGHS / Clarabel'), card('独立残差', 'idx-residual', '对数坐标')])) +
      section('回测两图表', '年度收益表与趋势折线图', charts([card('趋势折线图', 'idx-backtest-line', '中证500 / 指数增强 / 相对强度'), card('年度收益图', 'idx-backtest-annual', '策略 / 基准 / 超额')]) + timingAnnualTable(indexAnnualRows(v), '中证500')) +
      '</div>';
    const rotNav = arr(monthly.nav || research.nav);
    plot(
      'idx-ind-nav',
      [
        { type: 'scatter', mode: 'lines', name: '行业轮动', x: rotNav.map((x) => dateText(x.date)), y: rotNav.map((x) => num(x.strategy)), line: { color: '#bfbfbf', width: 2.4 } },
        { type: 'scatter', mode: 'lines', name: '行业等权', x: rotNav.map((x) => dateText(x.date)), y: rotNav.map((x) => num(x.benchmark)), line: { color: C.gold, width: 2.2 } },
        { type: 'scatter', mode: 'lines', name: '相对强度（右轴）', x: rotNav.map((x) => dateText(x.date)), y: rotNav.map((x) => num(x.excess)), yaxis: 'y2', line: { color: C.red, width: 2.6 } },
      ],
      { hovermode: 'x unified', yaxis: { title: '净值' }, yaxis2: { title: '相对强度', overlaying: 'y', side: 'right', showgrid: false }, legend: { orientation: 'h', y: -0.22 } },
    );
    plot('idx-ind-rank', [{ type: 'bar', orientation: 'h', y: ranking.slice(0, 12).reverse().map((x) => x.name), x: ranking.slice(0, 12).reverse().map((x) => num(x.score)), marker: { color: ranking.slice(0, 12).reverse().map((x) => x.selected ? C.red : C.grey) } }], { xaxis: { range: [0, 1.05], title: '行业得分' }, margin: { l: 98, r: 18, t: 16, b: 42 }, showlegend: false });
    performance('idx-score', v);
    plot('idx-ind-active', [{ type: 'bar', x: industryRows.map((x) => x.industry), y: industryRows.map((x) => num(x.active_weight)), marker: { color: industryRows.map((x) => (num(x.active_weight) || 0) >= 0 ? C.red : C.blue) } }], { yaxis: { tickformat: '.1%' }, showlegend: false });
    plot('idx-ind-current', [{ type: 'bar', x: ranking.slice(0, 10).map((x) => x.name), y: ranking.slice(0, 10).map((x) => num(x.weight || x.score)), marker: { color: ranking.slice(0, 10).map((x) => x.selected ? C.red : C.grey) } }], { yaxis: { title: '权重/得分' }, showlegend: false });
    portfolio('idx-stock', result, selectedWeights);
    constraints('idx-constraint', result, 14);
    solverCharts('idx', result);
    plotIndexBacktest('idx-backtest', v);
  }
  async function renderStrategy(page) {
    clear();
    try {
      await load(false);
    } catch (e) {
      root().innerHTML = block("模块加载失败", e.message);
      return;
    }
    const p = problems();
    if (p.length) {
      conclusion("真实资产池不完整，未使用旧快照或模拟数据降级。", true);
      root().innerHTML = block("模块已阻断", p.join("；"));
      return;
    }
    const v = vm();
    if (["home", "risk", "tracking", "index"].includes(page) && !v) {
      root().innerHTML = block(
        "暂无认证组合",
        "请先完成LLM约束确认和精确求解。",
      );
      return;
    }
    if (page === "home") return home(v);
    if (page === "index") return await indexPage(v);
    if (page === "universe") return universe();
    if (page === "alpha") return alpha();
    if (page === "smartbeta") return beta();
    if (page === "timing") return timing(state.snapshot);
    if (page === "risk") return risk(v);
    if (page === "tracking") return tracking(v);
    return home(v);
  }
  function staticAsset(file) {
    const v =
      (window.APP_BOOT && (window.APP_BOOT.assetVersion || window.APP_BOOT.version)) ||
      "r34_optimizer_learning";
    return (
      BASE +
      "/static/optimizer_figures/" +
      encodeURIComponent(file) +
      "?v=" +
      encodeURIComponent(v)
    );
  }
  function staticFigure(title, file, note) {
    return (
      '<article class="optimizer-static-figure"><header><h3>' +
      esc(title) +
      "</h3>" +
      (note ? "<span>" + esc(note) + "</span>" : "") +
      '</header><img src="' +
      staticAsset(file) +
      '" alt="' +
      esc(title) +
      '" loading="eager" decoding="async"></article>'
    );
  }
  function learningFlow() {
    const steps = [
      ["默认参数", "中证500、50只、综合得分、因子暴露、历史持仓"],
      ["约束解释", "自然语言输入，LLM生成1–3套完整方程流程"],
      ["人工确认", "选择方案、修改草案、校验可行性、锁定哈希"],
      ["HiGHS选股", "branch-and-cut整数规划选出50只支持集"],
      ["Clarabel求权", "原始-对偶内点法求解二阶锥凸优化权重"],
    ];
    return (
      '<div class="optimizer-learning-flow">' +
      steps
        .map(
          (x, i) =>
            '<article><span>0' +
            (i + 1) +
            '</span><strong>' +
            esc(x[0]) +
            '</strong><p>' +
            esc(x[1]) +
            "</p></article>" +
            (i < steps.length - 1 ? '<i class="optimizer-flow-arrow">›</i>' : ""),
        )
        .join("") +
      "</div>"
    );
  }
  function learningParams() {
    const c = state.config;
    const rows = [
      ["基准与资产池", c.universe.name || "中证500", c.universe.code || "000905.SH"],
      ["固定持仓数量", String(c.universe.holdings || 50) + "只", "HiGHS硬约束"],
      ["权重下限", pct(get(c, "holdings.min_weight")), "入选股票"],
      ["权重上限", pct(get(c, "holdings.max_weight")), "单票总权重"],
      ["单票主动权重", pct(get(c, "active_risk.max_active_weight")), "相对基准"],
      ["行业偏离", pct(get(c, "industry.max_active_deviation")), get(c, "industry.classification") || "SW_L1"],
      ["风格暴露", "±" + pct(get(c, "style.max_abs_exposure")), "规模/价值/动量/流动性"],
      ["跟踪误差", pct(get(c, "active_risk.tracking_error_limit")), get(c, "active_risk.covariance_model") || "factor"],
      ["单期换手", pct(get(c, "trading.turnover_limit")), "相对上期持仓"],
      ["白名单/黑名单", "支持", "强制纳入/剔除"],
    ];
    return (
      '<div class="optimizer-param-grid">' +
      rows
        .map(
          (x, i) =>
            '<article class="optimizer-param-tile ' +
            (i < 2 ? "is-primary" : "") +
            '"><span>' +
            esc(x[0]) +
            '</span><strong>' +
            esc(x[1]) +
            '</strong><small>' +
            esc(x[2]) +
            "</small></article>",
        )
        .join("") +
      "</div>"
    );
  }
  function learningFormula() {
    const blocks = [
      [
        "目标函数",
        "max_w  λα αᵀw − λr ‖R(w−b)‖₂² − λs ‖w−s‖₂² − λt ‖w−p‖₁ − λc cᵀ|w−p|",
        "在Alpha得分、主动风险、贴近基准/目标组合、换手和交易成本之间做多目标权衡。",
      ],
      [
        "持仓与权重",
        "1ᵀw = 1，w ≥ 0，yᵢ∈{0,1}，Σyᵢ = 50，ℓᵢyᵢ ≤ wᵢ ≤ uᵢyᵢ",
        "先用0/1变量锁定50只股票，再让入选股票满足权重上下限和满仓约束。",
      ],
      [
        "行业与风格",
        "|Gᵀ(w−b)| ≤ δind，|Fᵀ(w−b)| ≤ δstyle",
        "控制申万一级行业偏离和规模、价值、动量、流动性等风格主动暴露。",
      ],
      [
        "跟踪误差",
        "‖Lᵀ(w−b)‖₂ ≤ TEmax，Σ = LLᵀ",
        "用风险矩阵的Cholesky/因子分解把主动风险写成二阶锥约束。",
      ],
      [
        "换手与流动性",
        "0.5‖w−p‖₁ ≤ τmax，wᵢ ≤ ρ·ADVᵢ/AUM",
        "限制单期单边换手，并约束成交占日均成交额比例，避免纸面收益。",
      ],
      [
        "名单约束",
        "i∈White ⇒ yᵢ=1，i∈Black ⇒ yᵢ=0",
        "支持研究员把必须持有或禁止买入股票直接写入可行域。",
      ],
    ];
    return (
      '<div class="optimizer-formula-grid">' +
      blocks
        .map(
          (x) =>
            '<article class="optimizer-equation-card"><h3>' +
            esc(x[0]) +
            '</h3><div class="optimizer-equation">' +
            esc(x[1]) +
            '</div><p>' +
            esc(x[2]) +
            "</p></article>",
        )
        .join("") +
      "</div>"
    );
  }
  function workflowPhase(v) {
    if (v.confirmed) return "已确认，可提交";
    if (v.checked) return "等待人工确认";
    if (state.draft.length) return "等待校验";
    if (arr(state.planOptions).length) return "等待按方案生成草案";
    return "等待生成方案";
  }
  function workflowBody() {
    const v = valid(),
      kb = obj(boot().knowledge_base),
      issues = arr(v.p.errors).concat(arr(v.p.conflicts)),
      plan = activePlan(),
      phase = workflowPhase(v);
    return (
      strip(
        [
          ["工作流", "输入 → 方案 → 草案 → 校验 → 确认 → 求解"],
          ["当前状态", phase],
          ["方案数量", String(arr(state.planOptions).length || "--")],
          ["草案约束", String(state.draft.length)],
          ["权重权限", "LLM不得输出权重"],
        ],
        v.confirmed ? "is-ready" : "",
      ) +
      '<div class="optimizer-llm-grid optimizer-learning-llm"><section class="optimizer-prompt"><header><h2>文本输入要求</h2><p>输入新增约束后，LLM只生成可解释方程方案；真实选股和权重仍由求解器执行。</p></header><textarea id="instruction" rows="6" placeholder="例如：中证500内持有50只；行业偏离不超过2%；风格暴露不超过0.10；跟踪误差不超过6%；单期换手不超过100%；希望新增低换手或更严格行业约束。">' +
      esc(state.instruction) +
      '</textarea><div class="optimizer-action-row"><span>先返回1–3套完整方程流程，人工选择后再编译。</span><button class="action-button" id="generate-plans">生成方程方案</button></div></section><aside class="optimizer-kb"><h3>约束知识库</h3><div class="optimizer-kb-chips"><span>整数选股</span><span>行业偏离</span><span>风格中性</span><span>跟踪误差SOCP</span><span>换手成本</span><span>名单约束</span></div><dl><div><dt>版本</dt><dd>' +
      esc(kb.version || boot().knowledge_base_version || "--") +
      "</dd></div><div><dt>来源</dt><dd>" +
      esc(kb.source_count || "--") +
      "</dd></div><div><dt>方案生成</dt><dd>中转站LLM</dd></div><div><dt>权重生成</dt><dd>禁止</dd></div></dl></aside></div>" +
      (arr(state.planOptions).length
        ? section(
            "返回修改后求解公式",
            "每张卡片是一套可选择、可编辑的完整方程流程。",
            '<div class="optimizer-plan-grid">' + arr(state.planOptions).map(planCard).join("") + "</div>",
          )
        : "") +
      (plan && !state.draft.length
        ? section(
            "人工选择与修改",
            "确认采用哪套方程流程；也可以直接改写后再进入严格编译。",
            '<div class="optimizer-selected-plan"><textarea id="selected-plan-request" rows="7">' +
              esc(plan.mandate_request || state.instruction) +
              '</textarea><div class="optimizer-action-row"><span>该文本会进入 OptimizationMandate/v1 编译器。</span><button class="action-button" id="interpret">按选中方案生成约束草案</button></div></div>',
          )
        : "") +
      (state.draft.length
        ? section(
            "确认/修改提交",
            "约束按类别展示；修改后必须重新校验并人工确认。",
            '<div class="optimizer-constraint-toolbar"><span>' +
              state.draft.length +
              '条硬约束草案</span><button class="ghost-button" id="add">新增约束</button></div><div class="optimizer-constraint-list">' +
              state.draft.map(constraintCard).join("") +
              "</div>",
          )
        : "") +
      (state.draft.length
        ? '<section class="optimizer-workflow"><div class="optimizer-workflow-state"><strong>' +
          esc(phase) +
          "</strong><span>" +
          esc(
            issues.length
              ? issues.join("；")
              : v.confirmed
                ? "确认哈希已锁定；修改任何约束都必须重新校验和确认。"
                : v.checked
                  ? "后端可行性校验通过，等待人工确认当前哈希。"
                  : "先执行结构、语义、可行性和求解器能力校验。",
          ) +
          '</span></div><div class="optimizer-actions"><button class="ghost-button" id="validate">校验</button><button class="ghost-button" id="confirm" ' +
          (v.checked && !v.confirmed ? "" : "disabled") +
          '>人工确认</button><button class="action-button" id="submit" ' +
          (v.confirmed ? "" : "disabled") +
          ">提交求解</button></div></section>"
        : "")
    );
  }
  function bindWorkflowControls() {
    const instruction = root().querySelector("#instruction");
    if (instruction)
      instruction.oninput = (e) => {
        state.instruction = e.target.value;
        state.validation = null;
      };
    const gen = root().querySelector("#generate-plans");
    if (gen) gen.onclick = generatePlans;
    bindPlanOptions();
    const plan = activePlan(),
      selected = root().querySelector("#selected-plan-request");
    if (selected && plan) {
      selected.oninput = (e) => {
        plan.mandate_request = e.target.value;
        plan.compile_instruction = e.target.value;
        state.validation = null;
      };
    }
    const interpretButton = root().querySelector("#interpret");
    if (interpretButton) interpretButton.onclick = interpret;
    if (!state.draft.length) return;
    bindDraft();
    const add = root().querySelector("#add");
    if (add)
      add.onclick = () => {
        state.draft.push({
          name: "新增约束",
          category: "active_risk",
          operator: "<=",
          upper: "",
          unit: "",
          hard: true,
          formula: "",
          reference: "",
        });
        state.validation = null;
        renderWorkflow();
      };
    const validateButton = root().querySelector("#validate"),
      confirmButton = root().querySelector("#confirm"),
      submitButton = root().querySelector("#submit");
    if (validateButton) validateButton.onclick = validate;
    if (confirmButton) confirmButton.onclick = confirm;
    if (submitButton) submitButton.onclick = submit;
  }
  function researchSnapshot() {
    return obj(obj(state.snapshot).optimizer_research_snapshot);
  }
  function percentNumber(value) {
    const n = Number(String(value == null ? "" : value).replace("%", ""));
    return Number.isFinite(n) ? n : null;
  }
  function solverFrontierRows() {
    const research = researchSnapshot(),
      frontier = arr(obj(research.optimization).efficient_frontier)
        .map((row) => ({
          lambda: num(row.risk_aversion != null ? row.risk_aversion : row.lambda),
          expected_return: num(row.expected_return != null ? row.expected_return : row.annual_return),
          volatility: num(row.volatility != null ? row.volatility : row.annual_volatility),
          expected_sharpe: num(row.expected_sharpe != null ? row.expected_sharpe : row.sharpe),
        }))
        .filter((row) => row.lambda != null);
    if (frontier.length) return frontier.sort((a, b) => a.lambda - b.lambda);
    return arr(obj(research.optimization).leaderboard)
      .map((row) => ({
        lambda: num(row.risk_aversion),
        expected_return: num(row.validation_annual_return != null ? row.validation_annual_return : row.train_annual_return),
        volatility: num(row.validation_annual_volatility != null ? row.validation_annual_volatility : row.train_annual_volatility),
        expected_sharpe: num(row.validation_sharpe != null ? row.validation_sharpe : row.train_sharpe),
      }))
      .filter((row) => row.lambda != null)
      .sort((a, b) => a.lambda - b.lambda);
  }
  function emptyPlot(id, text) {
    plot(id, [], {
      annotations: [
        {
          text: text || "暂无可绘制数据",
          x: 0.5,
          y: 0.5,
          xref: "paper",
          yref: "paper",
          showarrow: false,
          font: { color: "#7a8491", size: 13 },
        },
      ],
      xaxis: { visible: false },
      yaxis: { visible: false },
    });
  }
  function plotSolverSensitivity() {
    const rows = solverFrontierRows();
    if (!rows.length) {
      emptyPlot("solver-sensitivity", "暂无风险厌恶系数敏感性数据");
      return;
    }
    plot(
      "solver-sensitivity",
      [
        {
          type: "scatter",
          mode: "lines+markers",
          name: "预期收益",
          x: rows.map((row) => row.lambda),
          y: rows.map((row) => row.expected_return),
          line: { color: C.red, width: 2.8 },
          marker: { size: 6 },
        },
        {
          type: "scatter",
          mode: "lines+markers",
          name: "预期波动",
          x: rows.map((row) => row.lambda),
          y: rows.map((row) => row.volatility),
          line: { color: C.blue, width: 2.4 },
          marker: { size: 6 },
        },
        {
          type: "scatter",
          mode: "lines+markers",
          name: "预期夏普（右轴）",
          x: rows.map((row) => row.lambda),
          y: rows.map((row) => row.expected_sharpe),
          yaxis: "y2",
          line: { color: C.gold, width: 2.4 },
          marker: { size: 6 },
        },
      ],
      {
        hovermode: "x unified",
        xaxis: { title: "风险厌恶系数 λ", type: "log", gridcolor: C.grid },
        yaxis: { title: "收益 / 波动", tickformat: ".1%", gridcolor: C.grid },
        yaxis2: { title: "夏普", overlaying: "y", side: "right", showgrid: false },
        legend: { orientation: "h", y: -0.2 },
      },
    );
  }
  function plotSolverBacktest(v) {
    if (!v) {
      emptyPlot("solver-backtest-line", "等待认证组合回测数据");
      emptyPlot("solver-backtest-annual", "等待年度收益数据");
      return;
    }
    const benchmark = arr(v.nav.benchmark),
      optimized = arr(v.nav.constrained_optimizer),
      relative = excess(optimized, benchmark).map((row) => ({
        date: row.date,
        value: 1 + row.value,
      }));
    plot(
      "solver-backtest-line",
      [
        line(benchmark, "中证500", C.gold, 2.4),
        line(optimized, "优化组合", C.red, 2.8),
        Object.assign(line(relative, "相对强度（右轴）", C.blue, 2.6), { yaxis: "y2" }),
      ],
      {
        hovermode: "x unified",
        xaxis: { type: "date", showgrid: false },
        yaxis: { title: "净值" },
        yaxis2: { title: "相对强度", overlaying: "y", side: "right", showgrid: false },
        legend: { orientation: "h", y: -0.22 },
      },
    );
    const rows = indexAnnualRows(v).filter((row) => String(row.year).indexOf("区间") < 0);
    if (!rows.length) {
      emptyPlot("solver-backtest-annual", "暂无年度收益数据");
      return;
    }
    plot(
      "solver-backtest-annual",
      [
        {
          type: "bar",
          name: "优化组合",
          x: rows.map((row) => row.year),
          y: rows.map((row) => percentNumber(row.strategy_return)),
          marker: { color: C.red },
        },
        {
          type: "bar",
          name: "中证500",
          x: rows.map((row) => row.year),
          y: rows.map((row) => percentNumber(row.benchmark_return)),
          marker: { color: C.gold },
        },
        {
          type: "scatter",
          mode: "lines+markers",
          name: "超额收益（右轴）",
          x: rows.map((row) => row.year),
          y: rows.map((row) => percentNumber(row.excess_return)),
          yaxis: "y2",
          line: { color: C.blue, width: 2.4 },
          marker: { size: 6 },
        },
      ],
      {
        barmode: "group",
        yaxis: { title: "收益(%)" },
        yaxis2: { title: "超额(%)", overlaying: "y", side: "right", showgrid: false },
        legend: { orientation: "h", y: -0.22 },
      },
    );
  }
  function renderLearningContent() {
    const v = vm(),
      o = v ? metric(v, "constrained_optimizer") : {},
      s = obj(state.snapshot),
      q = obj(s.data_quality),
      sc = obj(s.score);
    conclusion(
      "优化求解器全流程：默认参数、LLM约束解释、人工确认、HiGHS选股、Clarabel求权、敏感性与回测图统一在组合优化板块完成。",
    );
    root().innerHTML =
      '<div class="optimizer-shell optimizer-learning-shell">' +
      evidence([
        ["资产池", String(arr(s.assets).length || 500) + "只", "中证500", "is-primary"],
        ["持仓数量", String(get(state.config, "universe.holdings") || 50) + "只", "固定支持集"],
        ["得分日期", dateText(sc.signal_date), "因子实验室champion"],
        ["可交易数", String(q.tradable_count || "--") + "只", "停牌涨跌停过滤"],
        ["年化超额", pct(o.annual_excess_return || o.excess_return), "约束优化组合"],
        ["信息比率", fixed(o.information_ratio), "相对中证500"],
      ]) +
      section("流程图", "默认参数-约束解释-人工确认-HiGHS选股-Clarabel求权", learningFlow()) +
      section("默认参数", "参数按类别收纳，避免散乱铺表；真实求解时由后端配置和数据库快照驱动。", learningParams()) +
      section("默认约束求解公式", "完整表达目标函数、硬约束和两阶段求解逻辑。", learningFormula()) +
      section("约束解释与人工确认", "文本输入、返回方程方案、人工选择修改、校验确认、提交求解。", workflowBody()) +
      section(
        "敏感性分析",
        "风险厌恶系数 λ 序列，直接读取当前研究快照的有效前沿。",
        chartsSingle([card("厌恶系数敏感性", "solver-sensitivity", "预期收益 / 波动 / 夏普")]),
      ) +
      section(
        "回测图表",
        "年度收益表与趋势折线图，直接使用当前最优默认求解器结果。",
        charts([card("趋势折线图", "solver-backtest-line", "中证500 / 优化组合 / 相对强度"), card("年度收益图", "solver-backtest-annual", "策略 / 基准 / 超额")]) +
          timingAnnualTable(v ? indexAnnualRows(v) : [], "中证500"),
      ) +
      "</div>";
    bindWorkflowControls();
    plotSolverSensitivity();
    plotSolverBacktest(v);
  }
  async function learningPage() {
    state.currentPage = "learning";
    const token = (state.learningToken || 0) + 1;
    state.learningToken = token;
    try {
      await loadBootstrap(false);
    } catch (e) {
      root().innerHTML = block("优化求解器加载失败", e.message);
      return;
    }
    const b = boot();
    if (b.data_ready === false) {
      conclusion("真实资产池未就绪，已阻断降级展示。", true);
      root().innerHTML = block("模块已阻断", b.block_reason || "研究数据未就绪");
      return;
    }
    renderLearningContent();
    loadSnapshot(false)
      .then(() => {
        const active = document.querySelector(".nav-item.is-active")?.dataset.target;
        if (state.currentPage === "learning" && state.learningToken === token && active === "portfolio:solve") renderLearningContent();
      })
      .catch((e) => conclusion("策略快照后台加载失败：" + e.message, true));
  }  function go(page) {
    const b = document.querySelector('[data-workspace-section="' + page + '"]');
    if (b) b.click();
    else render(page);
  }
  async function render(page) {
    clear();
    if (page === "learning") return learningPage();
    if (page === "llm") return llmPage();
    if (page === "results") return resultsPage();
    state.currentPage = "basic";
    return basicPage();
  }
  function setStrategyMode() {}
  window.PortfolioOptimizer = {
    render,
    renderStrategy,
    setStrategyMode,
    state,
  };
})();
