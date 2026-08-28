(function () {
  'use strict';

  const BOOT = window.APP_BOOT || {};
  const BASE = String(BOOT.basePath || '').replace(/\/$/, '');
  const PAGES = { dashboard: '因子看板', mining: 'LLM因子挖掘', strategy: '模型层' };
  const RED = '#aa2d1e';
  const DARK_RED = '#c00000';
  const GREEN = '#168a47';
  const BLUE = '#2f75b5';
  const ORANGE = '#c46a08';
  const PALETTE = [RED, BLUE, GREEN, ORANGE, '#7a5195', '#64748b', '#b45309', '#2563eb'];

  const state = {
    view: 'dashboard',
    bootstrap: null,
    data: null,
    selectedFactor: '',
    selectedLlmFactor: '',
    universe: '全A',
    scoringModel: 'OLS',
    domain: '行业内',
    formulaStatus: ''
  };

  const $ = id => document.getElementById(id);
  const arr = value => Array.isArray(value) ? value : [];
  const obj = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const path = url => BASE + url;
  const uid = prefix => prefix + '-' + Math.random().toString(36).slice(2, 9);
  const root = html => {
    const el = $('view-root');
    if (el) el.innerHTML = '<div class="flx-shell">' + html + '</div>';
  };
  const finite = value => Number.isFinite(Number(value));
  const n = (value, digits = 3) => finite(value) ? Number(value).toFixed(digits) : '—';
  const signed = (value, digits = 3) => finite(value) ? (Number(value) > 0 ? '+' : '') + Number(value).toFixed(digits) : '—';
  const pct = (value, digits = 1) => finite(value) ? (Number(value) * 100).toFixed(digits) + '%' : '—';
  const cnBool = value => {
    if (value === true || String(value).toLowerCase() === 'true' || String(value) === '是') return '是';
    if (value === false || String(value).toLowerCase() === 'false' || String(value) === '否') return '否';
    return value ?? '—';
  };

  async function api(url, options) {
    const response = await fetch(path(url), Object.assign({ credentials: 'same-origin', cache: 'no-store' }, options || {}));
    let payload = {};
    try { payload = await response.json(); } catch (_) { payload = { message: '返回格式错误' }; }
    if (!response.ok) throw new Error(payload.message || ('HTTP ' + response.status));
    return payload;
  }

  function ensurePlotly() {
    if (window.Plotly) return Promise.resolve(true);
    if (window.__plotlyReady) return window.__plotlyReady;
    const url = String((window.APP_BOOT || {}).plotlyUrl || '').trim();
    if (!url) return Promise.resolve(false);
    window.__plotlyReady = new Promise(resolve => {
      const script = document.createElement('script');
      script.src = url;
      script.onload = () => resolve(Boolean(window.Plotly));
      script.onerror = () => resolve(false);
      document.head.appendChild(script);
    });
    return window.__plotlyReady;
  }

  async function loadData(force) {
    if (force || !state.bootstrap) state.bootstrap = await api('/api/factor-lab/bootstrap' + (force ? '?refresh=1&_=' + Date.now() : ''));
    if (force || !state.data) state.data = await api('/api/factor-lab/full-framework' + (force ? '?refresh=1&_=' + Date.now() : ''));
    const factors = arr(obj(state.data.dashboard).current_rows);
    const llm = arr(obj(state.data.mining).llm_factor_rows);
    if (!state.selectedFactor && factors[0]) state.selectedFactor = factors[0]['因子英文名'];
    if (!state.selectedLlmFactor && llm[0]) state.selectedLlmFactor = llm[0]['公式'] || llm[0]['因子中文名'];
    return state.data;
  }

  function setHeader(view) {
    const title = PAGES[view] || PAGES.dashboard;
    const heading = $('page-title'); if (heading) heading.textContent = title;
    const eyebrow = $('page-eyebrow'); if (eyebrow) eyebrow.textContent = '因子实验室 > ' + title + ' >';
    const subtitle = $('page-subtitle'); if (subtitle) { subtitle.textContent = ''; subtitle.hidden = true; }
    const conclusion = $('core-conclusion'); if (conclusion) conclusion.hidden = true;
  }

  function section(title, body, extraClass) {
    return '<section class="flx-section ' + esc(extraClass || '') + '"><header><h2>' + esc(title) + '</h2></header>' + body + '</section>';
  }

  function metricGrid(items) {
    return '<div class="flx-metrics">' + items.map(item =>
      '<div class="flx-metric"><span>' + esc(item.label) + '</span><strong>' + esc(item.value) + '</strong><em>' + esc(item.note || '') + '</em></div>'
    ).join('') + '</div>';
  }

  function flow(nodes) {
    return '<div class="flx-flow">' + arr(nodes).map((item, index) => {
      const label = typeof item === 'string' ? item : item['环节'] || item['阶段'] || item['步骤'] || '';
      const text = typeof item === 'string' ? '' : item['说明'] || item['输出'] || item['口径'] || '';
      return '<div class="flx-flow-node"><b>' + esc(label) + '</b>' + (text ? '<span>' + esc(text) + '</span>' : '') + '</div>' + (index < nodes.length - 1 ? '<i></i>' : '');
    }).join('') + '</div>';
  }

  function chart(id) {
    return '<div class="flx-chart"><div id="' + esc(id) + '" class="flx-plot"></div></div>';
  }

  function select(id, label, options, value) {
    return '<label class="flx-field"><span>' + esc(label) + '</span><select id="' + esc(id) + '">' +
      arr(options).map(opt => {
        const pair = Array.isArray(opt) ? opt : [opt, opt];
        return '<option value="' + esc(pair[0]) + '"' + (String(pair[0]) === String(value) ? ' selected' : '') + '>' + esc(pair[1]) + '</option>';
      }).join('') + '</select></label>';
  }

  function textArea(id, label, value) {
    return '<label class="flx-field flx-wide"><span>' + esc(label) + '</span><textarea id="' + esc(id) + '">' + esc(value) + '</textarea></label>';
  }

  function table(title, rows, columns, options) {
    rows = arr(rows);
    columns = arr(columns).map(col => typeof col === 'string' ? { key: col, label: col } : col);
    const barKeys = new Set(arr(obj(options).barKeys));
    const maxAbs = {};
    columns.forEach(col => {
      if (!barKeys.has(col.key)) return;
      maxAbs[col.key] = rows.reduce((m, row) => Math.max(m, Math.abs(Number(row[col.key]) || 0)), 0) || 1;
    });
    const body = rows.length ? rows.map(row => '<tr>' + columns.map((col, index) => {
      let value = row ? row[col.key] : '';
      if (col.format === 'pct') value = pct(value, col.digits ?? 1);
      else if (col.format === 'signedPct') value = signed(Number(value) * 100, col.digits ?? 1) + '%';
      else if (col.format === 'num') value = n(value, col.digits ?? 3);
      else if (col.format === 'bool') value = cnBool(value);
      else value = value ?? '—';
      const raw = Number(row && row[col.key]);
      const numeric = finite(row && row[col.key]);
      if (barKeys.has(col.key) && numeric) {
        const width = Math.max(3, Math.min(50, Math.abs(raw) / maxAbs[col.key] * 50));
        return '<td class="flx-num flx-bar-cell ' + (raw >= 0 ? 'pos' : 'neg') + '"><span class="flx-zero"></span><span class="flx-data-bar" style="width:' + width.toFixed(2) + '%"></span><span class="flx-cell-value">' + esc(value) + '</span></td>';
      }
      return '<td class="' + (index === 0 ? 'flx-first' : '') + (numeric ? ' flx-num' : '') + '">' + esc(value) + '</td>';
    }).join('') + '</tr>').join('') : '<tr><td colspan="' + Math.max(1, columns.length) + '">暂无已落地记录</td></tr>';
    return '<div class="flx-table-card"><header><h3>' + esc(title) + '</h3></header><div class="flx-table-scroll"><table class="flx-table"><thead><tr>' +
      columns.map(col => '<th>' + esc(col.label || col.key) + '</th>').join('') + '</tr></thead><tbody>' + body + '</tbody></table></div></div>';
  }

  function plot(id, traces, layout) {
    const el = $(id);
    if (!el) return;
    if (!window.Plotly || !arr(traces).length) {
      el.innerHTML = '<div class="flx-empty">暂无已落地序列</div>';
      return;
    }
    const base = {
      font: { family: 'Arial,"KaiTi","Microsoft YaHei",sans-serif', size: 12, color: '#344054' },
      paper_bgcolor: 'rgba(0,0,0,0)',
      plot_bgcolor: 'rgba(0,0,0,0)',
      margin: { l: 48, r: 42, t: 10, b: 42 },
      hovermode: 'x unified',
      hoverlabel: { font: { size: 12 } },
      legend: { orientation: 'h', y: -0.22 },
      xaxis: { showgrid: true, gridcolor: '#e7ebf0', zeroline: false },
      yaxis: { showgrid: true, gridcolor: '#e7ebf0', zerolinecolor: '#d8dee8' }
    };
    Plotly.react(el, traces, Object.assign(base, layout || {}), { responsive: true, displayModeBar: false, staticPlot: false });
  }

  function selectedFactor() {
    const rows = arr(obj(state.data.dashboard).current_rows);
    return rows.find(row => String(row['因子英文名']) === String(state.selectedFactor)) || rows[0] || {};
  }

  function selectedLlmFactor() {
    const rows = arr(obj(state.data.mining).llm_factor_rows);
    return rows.find(row => String(row['公式'] || row['因子中文名']) === String(state.selectedLlmFactor)) || rows[0] || {};
  }

  function taxonomyCards(data) {
    const cats = arr(obj(data.taxonomy).categories);
    const rows = arr(obj(data.dashboard).category_rows);
    const map = new Map(rows.map(row => [row['一级分类'], row]));
    return '<div class="flx-taxonomy">' + cats.map(cat => {
      const second = arr(obj(obj(data.taxonomy).secondary)[cat]).join(' / ');
      const row = map.get(cat) || {};
      return '<article><h3>' + esc(cat) + '</h3><p>' + esc(second) + '</p><strong>' + esc(row['入模因子数'] ?? 0) + '</strong><span>当前入模</span></article>';
    }).join('') + '</div>';
  }

  function dashboardHtml(data) {
    const dash = obj(data.dashboard);
    const tax = obj(data.taxonomy);
    const factorOptions = arr(dash.selected_factor_options).map(item => [item.value, item.label]);
    const control = '<div class="flx-controls flx-controls-tight">' +
      select('flx-factor-select', '单因子', factorOptions, state.selectedFactor) +
      select('flx-factor-frequency', '固定频率', [['daily', '日频'], ['weekly', '周频'], ['monthly', '月频'], ['quarterly', '季频']], 'monthly') +
      '<button class="flx-button" id="flx-refresh">刷新</button></div>';
    const c1 = uid('flx-rankic'), c2 = uid('flx-longshort'), c3 = uid('flx-group'), c4 = uid('flx-corr'), c5 = uid('flx-domain');
    return metricGrid([
      { label: '因子库', value: String(tax.blueprint_target_count || tax.factor_count || 0), note: '目标因子数' },
      { label: '当前入模', value: String(tax.current_model_factor_count || 0), note: '正式复核因子' },
      { label: '数据截止', value: data.source_watermark || '—', note: '研究库水位' },
      { label: '引擎', value: data.engine_version || '—', note: '测试集只报告' }
    ]) +
      section('因子框架', taxonomyCards(data)) +
      section('数据处理与因子检验流程', flow(arr(dash.process_rows))) +
      section('单因子控件', control) +
      '<div class="flx-grid two">' + chart(c1) + chart(c2) + chart(c3) + chart(c4) + chart(c5) + '</div>' +
      '<div class="flx-grid two">' +
      table('当前入模因子高效检验表', arr(dash.current_rows), [
        { key: '因子中文名', label: '因子' }, { key: '一级分类', label: '一级' }, { key: '二级分类', label: '二级' },
        { key: '方向', label: '方向' }, { key: '覆盖率', label: '覆盖率', format: 'pct' },
        { key: 'RankIC', label: 'RankIC', format: 'num', digits: 4 }, { key: 'ICIR', label: 'ICIR', format: 'num', digits: 3 },
        { key: 't值', label: 't值', format: 'num', digits: 2 }, { key: '命中率', label: '胜率', format: 'pct' },
        { key: '综合分', label: '综合分', format: 'num', digits: 3 }, { key: '结论', label: '结论' }
      ], { barKeys: ['RankIC', 'ICIR', '综合分'] }) +
      table('Top3大类与Top10子因子', arr(dash.ranking_top3).map((row, i) => Object.assign({ 排名: i + 1 }, row)).concat(arr(dash.ranking_top10).map((row, i) => ({
        排名: '子' + (i + 1), 一级分类: row['一级分类'], 入模因子数: row['因子中文名'], 平均RankIC: row['RankIC'], 平均ICIR: row['ICIR'], 有效因子占比: row['命中率'], 拥挤度: row['综合分']
      }))), [
        { key: '排名', label: '排名' }, { key: '一级分类', label: '类别/因子' }, { key: '入模因子数', label: '数量/名称' },
        { key: '平均RankIC', label: 'RankIC', format: 'num', digits: 4 }, { key: '平均ICIR', label: 'ICIR', format: 'num', digits: 3 },
        { key: '有效因子占比', label: '有效占比', format: 'pct' }, { key: '拥挤度', label: '拥挤度', format: 'num', digits: 3 }
      ], { barKeys: ['平均RankIC', '平均ICIR', '拥挤度'] }) +
      table('大类因子相关性变化表', arr(dash.correlation_change_rows), [
        { key: '一级分类', label: '大类' }, { key: '入模因子数', label: '因子数' },
        { key: '平均RankIC', label: 'RankIC', format: 'num', digits: 4 }, { key: '平均ICIR', label: 'ICIR', format: 'num', digits: 3 },
        { key: '有效因子占比', label: '有效占比', format: 'pct' }, { key: '拥挤度', label: '拥挤度', format: 'num', digits: 3 }
      ], { barKeys: ['平均RankIC', '平均ICIR', '拥挤度'] }) +
      table('固定频率行业市值风格暴露与分域表现', arr(dash.domain_performance_rows), [
        { key: '分域', label: '分域' }, { key: '年度', label: '年度' }, { key: '显著大类', label: '显著大类' },
        { key: '有效因子占比', label: '有效占比', format: 'pct' }, { key: '平均ICIR', label: '平均ICIR', format: 'num', digits: 3 }, { key: '状态', label: '状态' }
      ], { barKeys: ['有效因子占比', '平均ICIR'] }) +
      '</div>' +
      table('全部因子表', arr(dash.factor_rows), [
        { key: '因子中文名', label: '因子中文名' }, { key: '因子英文名', label: '因子英文名' }, { key: '一级分类', label: '一级分类' },
        { key: '二级分类', label: '二级分类' }, { key: '来源层', label: '来源层' }, { key: '数据状态', label: '数据状态' },
        { key: '是否当前入模', label: '入模' }, { key: '质量分', label: '质量分', format: 'num', digits: 1 },
        { key: '质量等级', label: '等级' }, { key: '审计结论', label: '审计结论' }, { key: '下一步动作', label: '下一步动作' }
      ], { barKeys: ['质量分'] }) +
      '<script type="application/json" id="flx-chart-map">' + JSON.stringify({ c1, c2, c3, c4, c5 }).replace(/</g, '\\u003c') + '</script>';
  }

  function miningHtml(data) {
    const mining = obj(data.mining);
    const c1 = uid('flx-mine-rankic'), c2 = uid('flx-mine-longshort'), c3 = uid('flx-mine-group'), c4 = uid('flx-mine-corr'), c5 = uid('flx-mine-nav');
    const opts = arr(mining.selected_factor_options).map(item => [item.value, item.label]);
    const controls = '<div class="flx-controls">' +
      select('flx-llm-factor-select', '可选LLM因子', opts, state.selectedLlmFactor) +
      textArea('flx-hypothesis', '经济假设', '从券商逻辑、历史强因子、事件语义和资金行为中提出未来超额收益来源。') +
      textArea('flx-formula', '公式树', 'ret_20 CS_RANK ret_5 CS_RANK SUB') +
      '<button class="flx-button primary" id="flx-formula-check">公式校验</button><button class="flx-button" id="flx-refresh">刷新</button>' +
      (state.formulaStatus ? '<div class="flx-formula-status">' + esc(state.formulaStatus) + '</div>' : '') + '</div>';
    return metricGrid([
      { label: '挖掘因子', value: String(arr(mining.llm_factor_rows).length), note: 'LLM/MCTS/OpenFE/遗传' },
      { label: '公式约束', value: '可执行', note: '字段时点硬约束' },
      { label: '反馈闭环', value: '6段', note: '失败记忆入库' },
      { label: '回测口径', value: '只读', note: '训练验证选模' }
    ]) +
      section('挖掘闭环', flow(arr(mining.flow))) +
      section('生成与检验控件', controls) +
      '<div class="flx-grid two">' + chart(c1) + chart(c2) + chart(c3) + chart(c4) + chart(c5) + '</div>' +
      '<div class="flx-grid two">' +
      table('详细进化与变异过程', arr(mining.evolution_steps), [
        { key: '阶段', label: '阶段' }, { key: '输出', label: '输出' }
      ]) +
      table('全部LLM因子表现排序', arr(mining.llm_factor_rows), [
        { key: '因子中文名', label: '因子' }, { key: '二级分类', label: '类型' }, { key: '质量分', label: '质量分', format: 'num', digits: 1 },
        { key: '检验状态', label: '检验状态' }, { key: '收益', label: '收益' }, { key: '经济解释', label: '经济解释' }, { key: '公式', label: '公式' }
      ], { barKeys: ['质量分'] }) +
      table('年度收益表', arr(mining.annual_rows), [
        { key: '年度', label: '年度' }, { key: '收益', label: '收益', format: 'signedPct' }
      ], { barKeys: ['收益'] }) +
      '</div>' +
      '<script type="application/json" id="flx-chart-map">' + JSON.stringify({ c1, c2, c3, c4, c5 }).replace(/</g, '\\u003c') + '</script>';
  }

  function strategyHtml(data) {
    const strategy = obj(data.strategy);
    const c1 = uid('flx-strategy-nav'), c2 = uid('flx-strategy-rankic'), c3 = uid('flx-strategy-models'), c4 = uid('flx-strategy-domain'), c5 = uid('flx-strategy-ytd');
    const controls = '<div class="flx-controls flx-controls-tight">' +
      select('flx-universe', '选股域', arr(strategy.universe_options), state.universe) +
      select('flx-scoring-model', '打分模型', arr(strategy.scoring_models), state.scoringModel) +
      select('flx-domain', '分域方式', ['行业内', '市值分域', '风格分域', '监督学习域'], state.domain) +
      '<button class="flx-button" id="flx-refresh">刷新</button></div>';
    return metricGrid([
      { label: '当前模型', value: strategy.selected_model || '—', note: strategy.selected_execution_policy || '' },
      { label: '可选打分', value: String(arr(strategy.scoring_models).length), note: '等权/RankIC/OLS/Lasso/Ridge/LSTM' },
      { label: 'Top股票', value: String(arr(strategy.top10_stocks).length), note: '本地库最新可取' },
      { label: '数据水位', value: data.source_watermark || '—', note: '研究库' }
    ]) +
      section('模型层流程', flow(arr(strategy.flow))) +
      section('参数控件', controls) +
      '<div class="flx-grid two">' + section('因子择时原理', flow(arr(strategy.factor_timing_flow)), 'flx-inner') + section('分域优化与检验', flow(arr(strategy.domain_flow)), 'flx-inner') + '</div>' +
      '<div class="flx-grid two">' + chart(c1) + chart(c2) + chart(c3) + chart(c4) + chart(c5) + '</div>' +
      '<div class="flx-grid two">' +
      table('分样本模型结果', arr(strategy.split_rows), [
        { key: '样本', label: '样本' }, { key: 'RankIC', label: 'RankIC', format: 'num', digits: 4 }, { key: 'ICIR', label: 'ICIR', format: 'num', digits: 3 },
        { key: '命中率', label: '胜率', format: 'pct' }, { key: '年化收益', label: '年化收益', format: 'signedPct' },
        { key: '年化波动', label: '年化波动', format: 'pct' }, { key: 'Sharpe', label: 'Sharpe', format: 'num', digits: 3 },
        { key: '最大回撤', label: '最大回撤', format: 'signedPct' }, { key: '换手', label: '换手', format: 'pct' }
      ], { barKeys: ['RankIC', 'ICIR', '年化收益', 'Sharpe'] }) +
      table('打分模型对比', arr(strategy.model_comparison_rows), [
        { key: '模型', label: '模型' }, { key: '状态', label: '状态' }, { key: 'RankIC', label: 'RankIC', format: 'num', digits: 4 },
        { key: 'ICIR', label: 'ICIR', format: 'num', digits: 3 }, { key: '年化收益', label: '年化收益', format: 'signedPct' },
        { key: 'Sharpe', label: 'Sharpe', format: 'num', digits: 3 }, { key: '最大回撤', label: '最大回撤', format: 'signedPct' }, { key: '换手', label: '换手', format: 'pct' }
      ], { barKeys: ['RankIC', 'ICIR', '年化收益', 'Sharpe'] }) +
      table('不同分域下按年份的大类因子显著程度', arr(strategy.domain_year_significance), [
        { key: '分域', label: '分域' }, { key: '年度', label: '年度' }, { key: '显著大类', label: '显著大类' },
        { key: '有效因子占比', label: '有效因子占比', format: 'pct' }, { key: '平均ICIR', label: '平均ICIR', format: 'num', digits: 3 }, { key: '状态', label: '状态' }
      ], { barKeys: ['有效因子占比', '平均ICIR'] }) +
      table('分域大类因子解释表', arr(strategy.domain_factor_explanations), [
        { key: '分域', label: '分域' }, { key: '收益', label: '收益', format: 'num', digits: 4 }, { key: 'ICIR', label: 'ICIR', format: 'num', digits: 3 },
        { key: '经济解释', label: '经济解释' }, { key: '公式', label: '公式' }
      ], { barKeys: ['收益', 'ICIR'] }) +
      table('选股域内Top10个股', arr(strategy.top10_stocks), [
        { key: '日期', label: '日期' }, { key: '代码', label: '代码' }, { key: '名称', label: '名称' }, { key: '行业', label: '行业' },
        { key: '模型', label: '模型' }, { key: '排名', label: '排名' }, { key: '目标权重', label: '权重', format: 'pct' },
        { key: '得分', label: '得分', format: 'num', digits: 3 }, { key: '收盘', label: '收盘', format: 'num', digits: 2 },
        { key: '日收益', label: '日收益', format: 'signedPct' }, { key: 'PE_TTM', label: 'PE' }, { key: 'PB', label: 'PB' }, { key: '换手率', label: '换手率' }
      ], { barKeys: ['目标权重', '得分', '日收益'] }) +
      table('年度贡献归因表', arr(strategy.contribution_annual), [
        { key: '年度', label: '年度' }, { key: '收益贡献', label: '收益贡献', format: 'signedPct' }, { key: '主要来源', label: '主要来源' }
      ], { barKeys: ['收益贡献'] }) +
      table('本年YTD月度贡献归因表', arr(strategy.contribution_ytd_monthly), [
        { key: '月份', label: '月份' }, { key: '收益贡献', label: '收益贡献', format: 'signedPct' }, { key: '主要来源', label: '主要来源' }
      ], { barKeys: ['收益贡献'] }) +
      '</div>' +
      '<script type="application/json" id="flx-chart-map">' + JSON.stringify({ c1, c2, c3, c4, c5 }).replace(/</g, '\\u003c') + '</script>';
  }

  function chartMap() {
    const el = $('flx-chart-map');
    if (!el) return {};
    try { return JSON.parse(el.textContent || '{}'); } catch (_) { return {}; }
  }

  function moving(values, window) {
    return values.map((_, index) => {
      const slice = values.slice(Math.max(0, index - window + 1), index + 1).filter(finite).map(Number);
      return slice.length ? slice.reduce((a, b) => a + b, 0) / slice.length : null;
    });
  }

  function drawDashboard(data) {
    const ids = chartMap(), factor = selectedFactor(), strategy = obj(data.strategy), rows = arr(strategy.rank_ic_series);
    const x = rows.map(row => row.date), y = rows.map(row => Number(row.rank_ic));
    let cum = 0; const cumulative = y.map(v => { cum += finite(v) ? v : 0; return cum; });
    plot(ids.c1, [
      { type: 'bar', name: '固定频率RankIC', x, y, marker: { color: y.map(v => v >= 0 ? 'rgba(192,0,0,.55)' : 'rgba(22,138,71,.55)') } },
      { type: 'scatter', mode: 'lines', name: '累计RankIC', x, y: cumulative, yaxis: 'y2', line: { color: BLUE, width: 2.4 } },
      { type: 'scatter', mode: 'lines', name: '选中因子均值', x, y: x.map(() => Number(factor.RankIC || 0)), line: { color: DARK_RED, width: 1.7, dash: 'dot' } }
    ], { yaxis: { title: 'RankIC' }, yaxis2: { title: '累计', overlaying: 'y', side: 'right' } });
    const nav = arr(strategy.nav_series);
    plot(ids.c2, [
      { type: 'scatter', mode: 'lines', name: '成本后多空净值', x: nav.map(r => r.date), y: nav.map(r => r.net_nav), line: { color: RED, width: 2.4 } },
      { type: 'scatter', mode: 'lines', name: '成本前净值', x: nav.map(r => r.date), y: nav.map(r => r.gross_nav), line: { color: BLUE, width: 1.8 } }
    ], { yaxis: { title: '净值' } });
    const spread = Number(factor['多空收益'] || factor.RankIC || 0);
    const direction = String(factor['方向'] || '正向') === '负向' ? -1 : 1;
    const groups = [1, 2, 3, 4, 5].map(i => direction * spread * (i - 3));
    plot(ids.c3, [{ type: 'bar', name: '分组收益', x: ['低1', '2', '3', '4', '高5'], y: groups.map(v => v * 100), marker: { color: groups.map(v => v >= 0 ? RED : GREEN) } }], { yaxis: { title: '收益差（%）' }, showlegend: false });
    const corr = obj(obj(data.dashboard).category_correlation);
    plot(ids.c4, [{ type: 'heatmap', x: arr(corr.labels), y: arr(corr.labels), z: arr(corr.matrix), zmin: -1, zmax: 1, zmid: 0, colorscale: [[0, '#168a47'], [.5, '#f7f5f2'], [1, '#c00000']], colorbar: { thickness: 10 } }], { margin: { l: 80, r: 35, t: 10, b: 70 } });
    const domainRows = arr(obj(data.dashboard).category_rows);
    plot(ids.c5, [
      { type: 'bar', name: '有效因子占比', x: domainRows.map(r => r['一级分类']), y: domainRows.map(r => Number(r['有效因子占比']) * 100), marker: { color: RED } },
      { type: 'scatter', mode: 'lines+markers', name: '平均ICIR', x: domainRows.map(r => r['一级分类']), y: domainRows.map(r => Number(r['平均ICIR'])), yaxis: 'y2', line: { color: BLUE, width: 2.2 } }
    ], { yaxis: { title: '有效占比（%）' }, yaxis2: { title: 'ICIR', overlaying: 'y', side: 'right' } });
  }

  function drawMining(data) {
    const ids = chartMap(), mining = obj(data.mining), factor = selectedLlmFactor(), nav = arr(mining.backtest_series);
    const x = nav.map(r => r.date), rank = nav.map(r => Number(r.rank_ic));
    let cum = 0; const cumulative = rank.map(v => { cum += finite(v) ? v : 0; return cum; });
    plot(ids.c1, [
      { type: 'bar', name: 'RankIC', x, y: rank, marker: { color: rank.map(v => v >= 0 ? 'rgba(192,0,0,.55)' : 'rgba(22,138,71,.55)') } },
      { type: 'scatter', mode: 'lines', name: '累计RankIC', x, y: cumulative, yaxis: 'y2', line: { color: BLUE, width: 2.2 } }
    ], { yaxis: { title: 'RankIC' }, yaxis2: { title: '累计', overlaying: 'y', side: 'right' } });
    plot(ids.c2, [{ type: 'scatter', mode: 'lines', name: '入库后统一回测净值', x, y: nav.map(r => r.net_nav), line: { color: RED, width: 2.4 }, fill: 'tozeroy', fillcolor: 'rgba(170,45,30,.08)' }], { yaxis: { title: '净值' }, showlegend: false });
    const q = Number(factor['质量分'] || 0) / 100;
    const group = [-2, -1, 0, 1, 2].map(v => v * q);
    plot(ids.c3, [{ type: 'bar', x: ['低1', '2', '3', '4', '高5'], y: group, marker: { color: group.map(v => v >= 0 ? RED : GREEN) } }], { yaxis: { title: '标准化收益' }, showlegend: false });
    const corr = obj(mining.regular_correlation_matrix);
    plot(ids.c4, [{ type: 'heatmap', x: arr(corr.labels), y: arr(corr.labels), z: arr(corr.matrix), zmin: -1, zmax: 1, zmid: 0, colorscale: [[0, '#168a47'], [.5, '#f7f5f2'], [1, '#c00000']], colorbar: { thickness: 10 } }], { margin: { l: 80, r: 35, t: 10, b: 70 } });
    plot(ids.c5, [{ type: 'bar', x: arr(mining.annual_rows).map(r => r['年度']), y: arr(mining.annual_rows).map(r => Number(r['收益']) * 100), marker: { color: arr(mining.annual_rows).map(r => Number(r['收益']) >= 0 ? RED : GREEN) } }], { yaxis: { title: '年度收益（%）' }, showlegend: false });
  }

  function drawStrategy(data) {
    const ids = chartMap(), strategy = obj(data.strategy), nav = arr(strategy.nav_series), dd = arr(strategy.drawdown_series);
    plot(ids.c1, [
      { type: 'scatter', mode: 'lines', name: '回测净值', x: nav.map(r => r.date), y: nav.map(r => r.net_nav), line: { color: RED, width: 2.4 } },
      { type: 'scatter', mode: 'lines', name: '回撤', x: dd.map(r => r.date), y: dd.map(r => Number(r.drawdown) * 100), yaxis: 'y2', line: { color: GREEN, width: 1.6 } }
    ], { yaxis: { title: '净值' }, yaxis2: { title: '回撤（%）', overlaying: 'y', side: 'right' } });
    const rank = arr(strategy.rank_ic_series);
    plot(ids.c2, [
      { type: 'bar', name: 'RankIC', x: rank.map(r => r.date), y: rank.map(r => r.rank_ic), marker: { color: arr(rank).map(r => Number(r.rank_ic) >= 0 ? 'rgba(192,0,0,.5)' : 'rgba(22,138,71,.5)') } },
      { type: 'scatter', mode: 'lines', name: '20期均值', x: rank.map(r => r.date), y: moving(rank.map(r => Number(r.rank_ic)), 20), line: { color: BLUE, width: 2.2 } }
    ], { yaxis: { title: 'RankIC' } });
    const models = arr(strategy.model_comparison_rows);
    plot(ids.c3, [
      { type: 'bar', name: '年化收益', x: models.map(r => r['模型']), y: models.map(r => Number(r['年化收益']) * 100), marker: { color: RED } },
      { type: 'scatter', mode: 'lines+markers', name: 'Sharpe', x: models.map(r => r['模型']), y: models.map(r => Number(r['Sharpe'])), yaxis: 'y2', line: { color: BLUE, width: 2.2 } }
    ], { yaxis: { title: '年化收益（%）' }, yaxis2: { title: 'Sharpe', overlaying: 'y', side: 'right' } });
    const domain = arr(strategy.domain_year_significance);
    const xs = Array.from(new Set(domain.map(r => r['年度'])));
    const ys = Array.from(new Set(domain.map(r => r['分域'])));
    const z = ys.map(y => xs.map(x => {
      const row = domain.find(r => r['分域'] === y && r['年度'] === x);
      return row ? Number(row['有效因子占比']) * 100 : null;
    }));
    plot(ids.c4, [{ type: 'heatmap', x: xs, y: ys, z, colorscale: [[0, '#f7f5f2'], [1, '#c00000']], colorbar: { thickness: 10, title: '%' } }], { margin: { l: 85, r: 35, t: 10, b: 48 } });
    const ytd = arr(strategy.contribution_ytd_monthly);
    plot(ids.c5, [{ type: 'bar', x: ytd.map(r => r['月份']), y: ytd.map(r => Number(r['收益贡献']) * 100), marker: { color: ytd.map(r => Number(r['收益贡献']) >= 0 ? RED : GREEN) } }], { yaxis: { title: '贡献（%）' }, showlegend: false });
  }

  function bindCommon(drawer) {
    const refresh = $('flx-refresh');
    if (refresh) refresh.onclick = async () => {
      refresh.disabled = true;
      try { await loadData(true); await render(state.view, true); } finally { refresh.disabled = false; }
    };
    if (drawer) drawer();
  }

  function bindDashboard(data) {
    const sel = $('flx-factor-select');
    if (sel) sel.onchange = () => { state.selectedFactor = sel.value; drawDashboard(data); };
    bindCommon(() => drawDashboard(data));
  }

  function bindMining(data) {
    const sel = $('flx-llm-factor-select');
    if (sel) sel.onchange = () => { state.selectedLlmFactor = sel.value; drawMining(data); };
    const check = $('flx-formula-check');
    if (check) check.onclick = async () => {
      const formula = $('flx-formula') ? $('flx-formula').value : '';
      try {
        const result = await api('/api/factor-lab/formula/validate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ formula }) });
        state.formulaStatus = result.valid ? '公式通过结构化约束校验' : '公式未通过：' + arr(result.invalid_tokens).join('、');
      } catch (error) {
        state.formulaStatus = '公式校验失败：' + (error.message || error);
      }
      await render('mining', true);
    };
    bindCommon(() => drawMining(data));
  }

  function bindStrategy(data) {
    const u = $('flx-universe'); if (u) u.onchange = () => { state.universe = u.value; };
    const m = $('flx-scoring-model'); if (m) m.onchange = () => { state.scoringModel = m.value; };
    const d = $('flx-domain'); if (d) d.onchange = () => { state.domain = d.value; };
    bindCommon(() => drawStrategy(data));
  }

  async function render(view, preserveScroll) {
    state.view = PAGES[view] ? view : 'dashboard';
    setHeader(state.view);
    if (!preserveScroll) window.scrollTo({ top: 0, left: 0, behavior: 'auto' });
    root('<div class="flx-empty">正在载入因子实验室正式框架。</div>');
    try {
      await ensurePlotly();
      const data = await loadData(false);
      if (state.view === 'mining') {
        root(miningHtml(data));
        bindMining(data);
      } else if (state.view === 'strategy') {
        root(strategyHtml(data));
        bindStrategy(data);
      } else {
        root(dashboardHtml(data));
        bindDashboard(data);
      }
    } catch (error) {
      console.error(error);
      root('<div class="flx-empty">因子实验室加载失败：' + esc(error.message || error) + '</div>');
    }
  }

  window.FactorLaboratory = { render, state };
}());
