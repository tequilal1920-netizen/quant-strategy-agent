(() => {
  "use strict";

  const STYLE_FILES = [
    "app_r8.css",
    "app_r8_overrides.css",
    "app_r8_quant_sync.css",
    "app_r8_weights.css",
    "app_r8_weights_compact.css",
    "app_r8_weights_compact_v2.css",
    "native.css",
  ];

  let shadowRoot = null;

  const escapeAttribute = (value) => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;").replaceAll(">", "&gt;");

  function shellMarkup() {
    const boot = window.APP_BOOT || {};
    const basePath = String(boot.basePath || "").replace(/\/$/, "");
    const version = escapeAttribute(boot.version || "dev");
    const cssBase = `${basePath}/static/ai_monitor/css`;
    const styles = STYLE_FILES.map((file) => `<link rel="stylesheet" href="${cssBase}/${file}?v=${version}">`).join("");

    return `${styles}
      <div class="ai-monitor-native">
        <section class="ai-monitor-status" aria-label="AI监控数据状态">
          <div><span class="status-dot ok" aria-hidden="true"></span><strong>科技扩散数据更新正常</strong></div>
          <span>最新交易日 <b id="latest-date">载入中</b></span>
          <span>五维扩散 · 申万三级行业 · 个股归因</span>
        </section>

        <section class="primary-controls" aria-label="行业与时序设置">
          <label class="control-field"><span>一级行业</span><select id="level1-select"></select></label>
          <label class="control-field"><span>三级行业</span><select id="industry-select"></select></label>
          <label class="control-field metric-field"><span>时序指标</span><select id="metric-select"></select></label>
          <div class="control-field"><span>时间窗口</span><div id="window-control" class="segmented-control">
            <button type="button" data-window="60">60日</button><button type="button" data-window="120">120日</button><button type="button" data-window="all" class="is-active">2024+</button>
          </div></div>
          <div class="control-field"><span>平滑</span><div id="smooth-control" class="segmented-control">
            <button type="button" data-smooth="raw">原值</button><button type="button" data-smooth="5" class="is-active">5日</button><button type="button" data-smooth="20">20日</button>
          </div></div>
          <label class="toggle-field"><input id="reliable-only" type="checkbox"><span>仅可靠样本</span></label>
        </section>

        <section id="overview" class="dashboard-section" data-ai-section="overview">
          <header class="section-header"><span class="section-index">01</span><h2>综合总览</h2></header>
          <div class="panel-grid overview-grid">
            <article class="chart-panel span-2"><header class="panel-header"><h3>科技扩散指数时序</h3><strong id="market-last"></strong></header><div id="market-chart" class="plot-frame plot-large"></div></article>
            <article class="chart-panel"><header class="panel-header"><h3>一级行业对标</h3></header><div id="level1-chart" class="plot-frame plot-large"></div></article>
            <article class="chart-panel rank-panel"><header class="panel-header"><h3>全部三级行业排名</h3></header><div id="all-rank-chart" class="plot-frame plot-rank-all"></div></article>
            <article class="chart-panel rank-panel"><header class="panel-header"><h3 id="level1-rank-title">一级行业内三级行业排名</h3></header><div id="level1-rank-chart" class="plot-frame plot-rank"></div></article>
          </div>
        </section>

        <section id="industry-map" class="dashboard-section" data-ai-section="industry-map">
          <header class="section-header"><span class="section-index">02</span><h2>三级行业图谱</h2><div id="map-summary" class="section-summary"></div></header>
          <div id="level1-tabs" class="level1-tabs" role="tablist"></div>
          <div id="industry-board" class="industry-board"></div>
        </section>

        <section id="industry-series" class="dashboard-section" data-ai-section="industry-series">
          <header class="section-header"><span class="section-index">03</span><h2>行业时序</h2><div id="industry-badges" class="section-summary"></div></header>
          <div id="dimension-strip" class="dimension-strip"></div>
          <div class="panel-grid industry-grid">
            <article class="chart-panel span-2"><header class="panel-header"><h3 id="industry-title">综合扩散指数</h3></header><div id="industry-score-chart" class="plot-frame plot-large"></div></article>
            <article class="chart-panel span-2"><header class="panel-header"><h3 id="metric-chart-title">指标时序</h3></header><div id="industry-metric-chart" class="plot-frame plot-large"></div></article>
            <article class="chart-panel"><header class="panel-header"><h3>上涨家数的市值构成</h3></header><div id="size-area-chart" class="plot-frame"></div></article>
            <article class="chart-panel"><header class="panel-header"><h3>扩散得分驱动构成</h3></header><div id="driver-area-chart" class="plot-frame"></div></article>
          </div>
        </section>

        <section id="stock-attribution" class="dashboard-section" data-ai-section="stock-attribution">
          <header class="section-header"><span class="section-index">04</span><h2>个股归因</h2><label class="search-field"><span>搜索</span><input id="stock-search" type="search" placeholder="名称 / 代码"></label></header>
          <div class="panel-grid stock-grid">
            <article class="chart-panel"><header class="panel-header"><h3>正负贡献拆解</h3></header><div id="contribution-chart" class="plot-frame plot-tall"></div></article>
            <article class="chart-panel"><header class="panel-header"><h3>个股形态分布</h3></header><div id="stock-map-chart" class="plot-frame plot-tall"></div></article>
          </div>
          <article class="table-panel"><div class="table-scroll"><table class="data-table"><thead><tr><th>股票</th><th>形态</th><th>日涨跌</th><th>20日</th><th>贡献</th><th>趋势</th><th>相对强弱</th><th>预期</th><th>调研</th><th>资金</th><th>综合</th></tr></thead><tbody id="stock-table-body"></tbody></table></div></article>
          <div id="stock-detail" class="stock-detail" hidden>
            <header class="stock-detail-header"><h3 id="stock-title">个股详情</h3><div id="stock-badges" class="section-summary"></div></header>
            <div id="stock-score-strip" class="dimension-strip"></div>
            <div class="panel-grid">
              <article class="chart-panel span-2"><header class="panel-header"><h3>复权净值与均线</h3></header><div id="stock-price-chart" class="plot-frame plot-large"></div></article>
              <article class="chart-panel"><header class="panel-header"><h3>个股多维评分时序</h3></header><div id="stock-score-chart" class="plot-frame"></div></article>
              <article class="chart-panel"><header class="panel-header"><h3>贡献与资金时序</h3></header><div id="stock-event-chart" class="plot-frame"></div></article>
            </div>
          </div>
        </section>
        <div id="toast" class="toast" role="status" aria-live="polite"></div>
      </div>`;
  }

  async function mount(host) {
    if (!host) throw new Error("AI监控挂载节点不存在");
    shadowRoot = host.shadowRoot || host.attachShadow({ mode: "open" });
    shadowRoot.innerHTML = shellMarkup();
    const appBase = String((window.APP_BOOT || {}).basePath || "").replace(/\/$/, "");
    const options = { basePath: `${appBase}/api/ai-monitor` };
    await window.AIMonitorCore.mount(shadowRoot, options);
    await window.AIMonitorFeatures.mount(shadowRoot, options);
    await window.AIMonitorWeights.mount(shadowRoot, options);
    window.AIMonitorBoot.mount(shadowRoot);
    window.AIMonitorAxis.mount(shadowRoot);
    return window.AIMonitorCore.state;
  }

  function scrollTo(sectionId, behavior = "smooth") {
    const section = shadowRoot?.querySelector(`#${CSS.escape(sectionId)}`);
    if (!section) return false;
    section.scrollIntoView({ behavior, block: "start" });
    return true;
  }

  function invalidate() {
    window.AIMonitorCore?.invalidate();
    window.AIMonitorFeatures?.invalidate();
    window.AIMonitorWeights?.invalidate();
  }

  window.AIMonitorUI = { mount, scrollTo, invalidate };
})();