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
    draft: [],
    validation: null,
    run: null,
    pollTimer: null,
    plotlyPromise: null,
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
  async function load(force) {
    if (state.bootstrap && state.snapshot && !force) return;
    [state.bootstrap, state.snapshot] = await Promise.all([
      api("/api/optimizer/bootstrap"),
      api("/api/optimizer/strategy-snapshot"),
    ]);
    init();
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
              renderLlm();
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
            renderLlm();
          }),
      );
  }
  function renderLlm() {
    const v = valid(),
      kb = obj(boot().knowledge_base),
      issues = arr(v.p.errors).concat(arr(v.p.conflicts));
    let phase = "等待生成";
    if (state.draft.length) phase = "等待校验";
    if (v.checked) phase = "等待人工确认";
    if (v.confirmed) phase = "已确认，可提交";
    root().innerHTML =
      '<div class="optimizer-shell">' +
      strip(
        [
          ["工作流", "生成 → 校验 → 人工确认 → 求解"],
          ["当前状态", phase],
          ["约束数量", String(state.draft.length)],
          ["权重权限", "LLM不得输出权重"],
        ],
        v.confirmed ? "is-ready" : "",
      ) +
      '<div class="optimizer-llm-grid"><section class="optimizer-prompt"><header><h2>LLM约束输入</h2><p>仅把自然语言转成可编辑约束与公式，权重仍由精确求解器生成。</p></header><textarea id="instruction" rows="6" placeholder="例如：中证500内持有50只；行业偏离不超过2%；四类风格暴露不超过0.10；跟踪误差不超过6%；单期换手不超过100%。">' +
      esc(state.instruction) +
      '</textarea><div class="optimizer-action-row"><span>草案生成后可逐条展开修改。</span><button class="action-button" id="interpret">生成约束草案</button></div></section><aside class="optimizer-kb"><h3>约束知识库</h3><div class="optimizer-kb-chips"><span>持仓与整数选择</span><span>行业主动偏离</span><span>风格中性</span><span>跟踪误差SOCP</span><span>换手与成本</span><span>流动性与名单</span></div><dl><div><dt>版本</dt><dd>' +
      esc(kb.version || boot().knowledge_base_version || "--") +
      "</dd></div><div><dt>来源</dt><dd>" +
      esc(kb.source_count || "--") +
      "</dd></div><div><dt>权重生成</dt><dd>禁止</dd></div></dl></aside></div>" +
      (state.draft.length
        ? section(
            "约束草案",
            "按类别折叠展示；默认不铺开参数表",
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
    root().querySelector("#instruction").oninput = (e) =>
      (state.instruction = e.target.value);
    root().querySelector("#interpret").onclick = interpret;
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
        renderLlm();
      };
      root().querySelector("#validate").onclick = validate;
      root().querySelector("#confirm").onclick = confirm;
      root().querySelector("#submit").onclick = submit;
    }
  }
  async function llmPage() {
    try {
      await load(false);
    } catch (e) {
      root().innerHTML = block("工作台加载失败", e.message);
      return;
    }
    conclusion(
      "LLM只负责编译约束，人工确认后HiGHS与Clarabel才可求解；整个流程不存在自动确认或权重降级。",
    );
    renderLlm();
  }
  async function interpret() {
    const b = root().querySelector("#interpret");
    b.disabled = true;
    try {
      const q = await api("/api/optimizer/constraints/interpret", {
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
        a = arr(d.constraints || d.draft || d.items);
      if (!a.length) throw new Error("接口未返回约束草案");
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
      renderLlm();
      conclusion("约束草案已生成；修改后执行校验和人工确认。");
    } catch (e) {
      b.disabled = false;
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
      renderLlm();
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
      renderLlm();
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
            ? "??????????????????"
            : "??????????????"),
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
      "?????",
      "?????????/???????500??????????????????????",
      frameworkCards() +
        charts([
          card("??????????", prefix + "-framework-flow", "????????????????"),
          card("??????", prefix + "-framework-link", "?????????????????"),
          card("???????", prefix + "-framework-state", "?????????"),
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
        ["??", arr(solver.input_contract).length],
        ["??", arr(solver.constraints).length],
        ["??", arr(solver.output_contract).length],
        ["????", arr(index.linked_modules).length],
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
      { yaxis: { range: [0, 1.2], tickvals: [0, 1], ticktext: ["???", "???"] }, showlegend: false },
    );
  }
  function timingScore(x) {
    const n = num(x);
    return n == null ? null : n;
  }
  function timing(v) {
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
    conclusion(
      "?????????????????????????????????????????????????????????????????????????",
    );
    root().innerHTML =
      '<div class="optimizer-shell">' +
      evidence([
        ["????", fixed(left, 3), "???? / ????", "is-primary"],
        ["????", fixed(right, 3), "???? / ?????"],
        ["????", latest.active_side === "right" ? "??" : latest.active_side === "left" ? "??" : "--", "????"],
        ["????", fixed(position, 3), "timing position"],
        ["????", fixed(budget, 3), "risk budget multiplier"],
        ["????", periods.length + "?", "????"],
      ]) +
      section(
        "?????",
        "??????/??????????????????????",
        charts([
          card("???/????", "timing-latest", "???????????"),
          card("??????", "timing-path", "?? / ?? / ??"),
          card("???????", "timing-budget", "?????????"),
        ]),
      ) +
      section(
        "????",
        "?????????????????????",
        charts([
          card("??????", "timing-components", "????"),
          card("?/??????", "timing-regime", "????"),
          card("???????", "timing-sentiment", "?????"),
        ]),
      ) +
      "</div>";
    plot(
      "timing-latest",
      [
        {
          type: "bar",
          x: ["??", "??", "??", "????"],
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
        { type: "scatter", mode: "lines", name: "??", x: px, y: periods.map((x) => num(x.left_score)), line: { color: C.blue, width: 2 } },
        { type: "scatter", mode: "lines", name: "??", x: px, y: periods.map((x) => num(x.right_score)), line: { color: C.red, width: 2 } },
        { type: "scatter", mode: "lines", name: "??", x: px, y: periods.map((x) => num(x.composite_score)), line: { color: C.gold, width: 2 } },
      ],
      { yaxis: { range: [0, 1] }, hovermode: "x unified" },
    );
    plot(
      "timing-budget",
      [
        { type: "scatter", mode: "lines", name: "??", x: px, y: periods.map((x) => num(x.timing_position)), line: { color: C.red, width: 2.5 } },
        { type: "scatter", mode: "lines", name: "????", x: px, y: periods.map((x) => num(x.risk_budget_multiplier)), line: { color: C.green, width: 2.5 } },
      ],
      { yaxis: { range: [0, 1.05] }, hovermode: "x unified" },
    );
    const comp = [
      ["????", valuation.score],
      ["????", macro.score],
      ["????", trend.score],
      ["?????", sentiment.score],
    ];
    plot(
      "timing-components",
      [
        { type: "bar", x: comp.map((x) => x[0]), y: comp.map((x) => num(x[1])), marker: { color: [C.blue, C.blue, C.red, C.red] }, text: comp.map((x) => fixed(x[1], 3)), textposition: "outside" },
      ],
      { yaxis: { range: [0, 1.05] }, showlegend: false },
    );
    const counts = periods.reduce((out, row) => {
      const k = row.active_side === "right" ? "??" : row.active_side === "left" ? "??" : "???";
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
          x: ["????", "?????", "??", "??"],
          y: [sentiment.linear_percentile, sentiment.score, sentiment.turnover_rate, sentiment.volume_ratio].map(num),
          marker: { color: [C.grey, C.red, C.blue, C.gold] },
        },
      ],
      { showlegend: false },
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
    if (["home", "timing", "risk", "tracking"].includes(page) && !v) {
      root().innerHTML = block(
        "暂无认证组合",
        "请先完成LLM约束确认和精确求解。",
      );
      return;
    }
    if (page === "home") return home(v);
    if (page === "universe") return universe();
    if (page === "alpha") return alpha();
    if (page === "smartbeta") return beta();
    if (page === "timing") return timing(v);
    if (page === "risk") return risk(v);
    if (page === "tracking") return tracking(v);
    return home(v);
  }
  function go(page) {
    const b = document.querySelector('[data-workspace-section="' + page + '"]');
    if (b) b.click();
    else render(page);
  }
  async function render(page) {
    clear();
    if (page === "llm") return llmPage();
    if (page === "results") return resultsPage();
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
