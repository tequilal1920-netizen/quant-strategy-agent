(() => {
  "use strict";

  const chartId = "level1-chart";
  const stateKey = "__techDiffusionAxisPatchV2";
  let domRoot = document;

  function finiteValues(chart) {
    return (chart.data || []).flatMap((trace) => {
      if (trace.visible === "legendonly" || !Array.isArray(trace.y)) return [];
      return trace.y
        .filter((value) => value !== null && value !== "")
        .map(Number)
        .filter(Number.isFinite);
    });
  }

  function desiredRange(chart) {
    const values = finiteValues(chart);
    if (!values.length) return null;
    const minimum = Math.min(...values);
    const maximum = Math.max(...values);
    const span = Math.max(maximum - minimum, 1);
    const padding = Math.max(2, span * 0.08);
    return [
      Math.max(0, Math.floor(minimum - padding)),
      Math.min(100, Math.ceil(maximum + padding)),
    ];
  }

  function rangeIsCurrent(chart, target) {
    const current = chart.layout?.yaxis?.range;
    return Array.isArray(current)
      && current.length === 2
      && Math.abs(Number(current[0]) - target[0]) < 0.05
      && Math.abs(Number(current[1]) - target[1]) < 0.05;
  }

  async function enforce(chart) {
    const state = chart[stateKey];
    if (!state || state.busy || !window.Plotly) return;
    const range = desiredRange(chart);
    if (!range || rangeIsCurrent(chart, range)) return;

    state.busy = true;
    try {
      await Plotly.relayout(chart, {
        "yaxis.autorange": false,
        "yaxis.range": range,
        "yaxis.tickmode": "auto",
        "yaxis.nticks": 6,
      });
    } finally {
      state.busy = false;
    }
  }

  function bind() {
    const chart = domRoot.getElementById(chartId);
    if (!chart || typeof chart.on !== "function") return false;
    if (chart[stateKey]) return true;

    chart[stateKey] = { busy: false };
    chart.on("plotly_afterplot", () => {
      window.requestAnimationFrame(() => enforce(chart));
    });
    chart.on("plotly_legendclick", () => {
      window.setTimeout(() => enforce(chart), 0);
    });
    window.setTimeout(() => enforce(chart), 0);
    return true;
  }

  function mount(root) {
    domRoot = root;
    let attempts = 0;
    const timer = window.setInterval(() => {
      attempts += 1;
      if (bind() || attempts >= 120) window.clearInterval(timer);
    }, 100);
  }

  window.AIMonitorAxis = { mount };
})();
