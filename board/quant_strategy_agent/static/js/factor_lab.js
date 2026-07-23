(function () {
  'use strict';

  const BOOT = window.APP_BOOT || {};
  const BASE = String(BOOT.basePath || '').replace(/\/$/, '');
  const state = {
    view: 'home', bootstrap: null, catalog: null, runs: [], selected: null,
    miningTab: 'formula', family: 'all', period: 'all', line: 'net', poll: null,
    historyEngine: 'all', historyStatus: 'all', historyTab: 'runs'
  };
  const PAGE = {
    home: '主页', dashboard: '因子看板', mining: '因子挖掘',
    testing: '联合检验', strategy: '投资策略', history: '历史记录'
  };
  const ENGINE = {
    lstm: 'LSTM', rl_transformer: 'RL+Transformer', strategy: 'OLS / Lasso / 深度模型', joint_test: '联合检验'
  };
  const FAMILY = {
    all: '全部', technical: '技术', money: '资金', fundamental: '基本面', valuation: '估值',
    macro: '宏观', discovered: '普通因子', lstm: 'LSTM', rl_transformer: 'RL+Transformer'
  };
  const STATUS = { queued: '排队中', running: '运行中', completed: '已完成', failed: '失败', cancelled: '已取消', cancelling: '取消中' };

  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const path = url => BASE + url;
  const num = (value, digits = 3) => Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : '—';
  const signed = (value, digits = 3) => Number.isFinite(Number(value)) ? (Number(value) > 0 ? '+' : '') + Number(value).toFixed(digits) : '—';
  const pct = (value, digits = 2) => Number.isFinite(Number(value)) ? (Number(value) * 100).toFixed(digits) + '%' : '—';
  const root = html => { const el = $('view-root'); if (el) el.innerHTML = '<div class="fl2-shell">' + html + '</div>'; };

  async function api(url, options) {
    const response = await fetch(path(url), Object.assign({ credentials: 'same-origin' }, options || {}));
    let payload = {};
    try { payload = await response.json(); } catch (_) { payload = { message: '返回格式错误' }; }
    if (!response.ok) throw new Error(payload.message || ('HTTP ' + response.status));
    return payload;
  }

  function setHeader(view) {
    const title = PAGE[view] || PAGE.home;
    const heading = $('page-title'); if (heading) heading.textContent = title;
    const eyebrow = $('page-eyebrow'); if (eyebrow) eyebrow.textContent = '因子实验室';
    const subtitle = $('page-subtitle'); if (subtitle) { subtitle.textContent = ''; subtitle.hidden = true; }
    const conclusion = $('core-conclusion'); if (conclusion) conclusion.hidden = true;
  }

  function section(title, body, actions) {
    return '<section class="fl2-section"><header><h2>' + esc(title) + '</h2>' + (actions || '') + '</header>' + body + '</section>';
  }
  function field(label, id, value, type = 'number', attrs = '') {
    return '<label class="fl2-field"><span>' + esc(label) + '</span><input id="' + esc(id) + '" type="' + esc(type) + '" value="' + esc(value) + '" ' + attrs + '></label>';
  }
  function select(label, id, options, value) {
    return '<label class="fl2-field"><span>' + esc(label) + '</span><select id="' + esc(id) + '">' + options.map(item => {
      const pair = Array.isArray(item) ? item : [item, item];
      return '<option value="' + esc(pair[0]) + '" ' + (String(pair[0]) === String(value) ? 'selected' : '') + '>' + esc(pair[1]) + '</option>';
    }).join('') + '</select></label>';
  }
  function paramGroup(title, body, open) {
    return '<details class="fl2-param-group" ' + (open ? 'open' : '') + '><summary>' + esc(title) + '</summary><div class="fl2-param-body"><div class="fl2-param-grid">' + body + '</div></div></details>';
  }
  function advanced(body) { return '<details class="fl2-advanced"><summary>高级参数</summary><div class="fl2-param-grid">' + body + '</div></details>'; }
  function read(id, fallback) { const el = $(id); return el ? el.value : fallback; }

  function primaryToolbar(engine, fixed) {
    return '<div class="fl2-toolbar"><div class="fl2-toolbar-row">' +
      (fixed ? '<div class="fl2-field"><span>模型</span><input value="' + esc(ENGINE[engine]) + '" disabled></div>' : select('模型', 'fl2-engine', [['lstm', 'LSTM'], ['rl_transformer', 'RL+Transformer'], ['strategy', 'OLS / Lasso / 深度模型'], ['joint_test', '联合检验']], engine || 'lstm')) +
      select('运行等级', 'fl2-mode', [['smoke', '烟测'], ['research', '研究'], ['production', '生产']], 'research') +
      select('标的池', 'fl2-universe', [['ALL_A', '全 A 可交易池'], ['CSI300', '沪深 300'], ['CSI500', '中证 500'], ['CSI1000', '中证 1000']], 'ALL_A') +
      select('风险偏好', 'fl2-risk', [['conservative', '稳健'], ['balanced', '平衡'], ['aggressive', '进取']], 'balanced') +
      '</div></div>';
  }

  function modelFields(engine) {
    if (engine === 'lstm') return paramGroup('训练与搜索',
      field('序列长度（交易日）', 'fl2-sequence', 120, 'number', 'min="40" max="504" step="20"') +
      field('训练轮数', 'fl2-epochs', 18, 'number', 'min="1" max="60"') +
      field('集成种子数', 'fl2-seeds', 5, 'number', 'min="1" max="12"') +
      field('搜索候选数', 'fl2-trials', 12, 'number', 'min="1" max="48"') +
      advanced(
        field('隐藏维度', 'fl2-hidden', 160, 'number', 'min="64" max="512" step="32"') +
        field('LSTM 层数', 'fl2-lstm-layers', 3, 'number', 'min="2" max="5"') +
        field('注意力层数', 'fl2-attn-layers', 3, 'number', 'min="1" max="5"') +
        field('注意力头数', 'fl2-heads', 8, 'number', 'min="4" max="16" step="4"') +
        field('状态专家数', 'fl2-experts', 6, 'number', 'min="3" max="12"') +
        field('Dropout', 'fl2-dropout', .18, 'number', 'min="0.05" max="0.5" step="0.01"') +
        field('学习率', 'fl2-lr', .0003, 'number', 'min="0.00001" max="0.003" step="0.00001"') +
        field('候选训练轮数', 'fl2-trial-epochs', 4, 'number', 'min="1" max="12"')
      ), true);
    if (engine === 'rl_transformer') return paramGroup('训练与搜索',
      field('PPO Episodes', 'fl2-episodes', 2048, 'number', 'min="8" max="8192" step="8"') +
      field('Rollout 批次', 'fl2-rollout', 64, 'number', 'min="4" max="256" step="4"') +
      field('公式长度上限', 'fl2-max-tokens', 18, 'number', 'min="6" max="32"') +
      field('PPO 更新轮数', 'fl2-ppo-epochs', 4, 'number', 'min="1" max="12"') +
      advanced(
        field('表示维度', 'fl2-dmodel', 256, 'number', 'min="64" max="512" step="64"') +
        field('Transformer 层数', 'fl2-rl-layers', 6, 'number', 'min="2" max="10"') +
        field('注意力头数', 'fl2-rl-heads', 8, 'number', 'min="4" max="16" step="4"') +
        field('Dropout', 'fl2-rl-dropout', .15, 'number', 'min="0.05" max="0.5" step="0.01"') +
        field('PPO Clip', 'fl2-clip', .2, 'number', 'min="0.05" max="0.4" step="0.01"') +
        field('Gamma', 'fl2-gamma', .99, 'number', 'min="0.8" max="1" step="0.01"') +
        field('Entropy', 'fl2-entropy', .01, 'number', 'min="0" max="0.1" step="0.001"') +
        field('学习率', 'fl2-rl-lr', .0002, 'number', 'min="0.00001" max="0.003" step="0.00001"')
      ), true);
    if (engine === 'strategy') return paramGroup('训练与搜索',
      field('Lasso Alpha', 'fl2-lasso', .00002, 'number', 'min="0.000001" max="0.01" step="0.000001"') +
      field('深度模型训练轮数', 'fl2-strategy-epochs', 30, 'number', 'min="2" max="60"') +
      select('融合方式', 'fl2-blend', [['valid_sharpe', '验证期 Sharpe'], ['rank_ic', '验证期 RankIC'], ['equal', '等权']], 'valid_sharpe') +
      field('最大训练样本', 'fl2-max-samples', 300000, 'number', 'min="50000" max="1000000" step="50000"'), true);
    return paramGroup('检验范围',
      select('检验模式', 'fl2-test-mode', [['single_joint', '单因子与多因子'], ['single', '单因子'], ['joint', '多因子']], 'single_joint') +
      select('相关性口径', 'fl2-corr', [['spearman', 'Spearman'], ['pearson', 'Pearson']], 'spearman') +
      field('最大因子数', 'fl2-factor-limit', 240, 'number', 'min="20" max="500"') +
      select('测试集', 'fl2-test-lock', [['locked', '冻结']], 'locked'), true);
  }

  function parameterGroups(engine) {
    return '<div class="fl2-params">' +
      paramGroup('数据与样本',
        field('最大股票数', 'fl2-assets', 240, 'number', 'min="40" max="800" step="20"') +
        field('历史月数', 'fl2-months', 72, 'number', 'min="12" max="180" step="6"') +
        field('标签周期', 'fl2-horizons', '5,10,20', 'text') +
        select('信号频率', 'fl2-frequency', [['daily', '日频'], ['weekly', '周频'], ['monthly', '月频']], 'daily'), true) +
      '<div id="fl2-model-fields">' + modelFields(engine) + '</div>' +
      paramGroup('组合与风险',
        field('目标波动率', 'fl2-target-vol', .15, 'number', 'min="0.03" max="0.5" step="0.01"') +
        field('单票权重上限', 'fl2-position-cap', .03, 'number', 'min="0.005" max="0.1" step="0.005"') +
        field('换手率上限', 'fl2-turnover-cap', .35, 'number', 'min="0.05" max="1" step="0.05"') +
        select('暴露约束', 'fl2-neutral', [['industry_style', '行业与风格'], ['industry', '行业'], ['none', '不约束']], 'industry_style'), false) +
      paramGroup('执行与成本',
        field('单边总成本（bp）', 'fl2-cost', 15, 'number', 'min="0" max="200"') +
        select('调仓频率', 'fl2-rebalance', [['5d', '5 日'], ['10d', '10 日'], ['20d', '20 日']], '5d') +
        select('执行延迟', 'fl2-delay', [['t1', 'T+1'], ['t2', 'T+2']], 't1') +
        select('计算设备', 'fl2-cuda', [['1', 'GPU 优先'], ['0', '仅 CPU']], '1') +
        advanced(field('CPU 线程数', 'fl2-cpu', 4, 'number', 'min="1" max="16"') + field('随机种子', 'fl2-seed', 20260720, 'number', 'min="1" max="2147483647"')), false) +
      '</div>';
  }

  function payload(engine) {
    const result = {
      engine: engine || read('fl2-engine', 'lstm'), mode: read('fl2-mode', 'research'),
      universe: read('fl2-universe', 'ALL_A'), risk_profile: read('fl2-risk', 'balanced'),
      max_assets: Number(read('fl2-assets', 240)), max_months: Number(read('fl2-months', 72)),
      sequence_length: Number(read('fl2-sequence', 120)),
      horizons: String(read('fl2-horizons', '5,10,20')).split(',').map(Number).filter(Boolean),
      frequency: read('fl2-frequency', 'daily'), cost_bps: Number(read('fl2-cost', 15)),
      target_volatility: Number(read('fl2-target-vol', .15)), position_cap: Number(read('fl2-position-cap', .03)),
      turnover_cap: Number(read('fl2-turnover-cap', .35)), neutralization: read('fl2-neutral', 'industry_style'),
      rebalance: read('fl2-rebalance', '5d'), execution_delay: read('fl2-delay', 't1'),
      allow_cuda: read('fl2-cuda', '1') === '1', cpu_threads: Number(read('fl2-cpu', 4)),
      seed: Number(read('fl2-seed', 20260720)), task_name: ENGINE[engine || read('fl2-engine', 'lstm')]
    };
    if (result.engine === 'lstm') Object.assign(result, {
      epochs: Number(read('fl2-epochs', 18)), ensemble_seeds: Number(read('fl2-seeds', 5)),
      hidden_dim: Number(read('fl2-hidden', 160)), lstm_layers: Number(read('fl2-lstm-layers', 3)),
      attention_layers: Number(read('fl2-attn-layers', 3)), heads: Number(read('fl2-heads', 8)),
      experts: Number(read('fl2-experts', 6)), dropout: Number(read('fl2-dropout', .18)),
      learning_rate: Number(read('fl2-lr', .0003)), search: { trials: Number(read('fl2-trials', 12)), trial_epochs: Number(read('fl2-trial-epochs', 4)) }
    });
    if (result.engine === 'rl_transformer') Object.assign(result, {
      episodes: Number(read('fl2-episodes', 2048)), rollout_batch: Number(read('fl2-rollout', 64)),
      max_formula_tokens: Number(read('fl2-max-tokens', 18)), ppo_epochs: Number(read('fl2-ppo-epochs', 4)),
      d_model: Number(read('fl2-dmodel', 256)), layers: Number(read('fl2-rl-layers', 6)),
      heads: Number(read('fl2-rl-heads', 8)), dropout: Number(read('fl2-rl-dropout', .15)),
      ppo_clip: Number(read('fl2-clip', .2)), gamma: Number(read('fl2-gamma', .99)),
      entropy: Number(read('fl2-entropy', .01)), learning_rate: Number(read('fl2-rl-lr', .0002))
    });
    if (result.engine === 'strategy') Object.assign(result, {
      lasso_alpha: Number(read('fl2-lasso', .00002)), epochs: Number(read('fl2-strategy-epochs', 30)),
      max_training_samples: Number(read('fl2-max-samples', 300000)), blend_method: read('fl2-blend', 'valid_sharpe')
    });
    return result;
  }

  async function loadBase(force) {
    if (force || !state.bootstrap) state.bootstrap = await api('/api/factor-lab/bootstrap');
    if (force || !state.catalog) state.catalog = await api('/api/factor-lab/catalog' + (force ? '?refresh=1' : ''));
    state.runs = (await api('/api/factor-lab/runs?limit=200')).runs || [];
  }
  function displayName(run) {
    if (!run) return '未选择';
    const label = ENGINE[run.engine] || run.engine || '任务';
    let stamp = String(run.created_at || '').replace('T', ' ').replace(/\+00:00$/, '');
    if (stamp.length > 16) stamp = stamp.slice(0, 16);
    return label + (stamp ? ' · ' + stamp : '');
  }
  async function hydrate(run) {
    if (!run) return null;
    if (run.result || run.status !== 'completed') return run;
    return api('/api/factor-lab/runs/' + encodeURIComponent(run.run_id));
  }
  async function selectRun(runId) {
    state.selected = await api('/api/factor-lab/runs/' + encodeURIComponent(runId));
    await render(state.view, true);
    if (['queued', 'running', 'cancelling'].includes(state.selected.status)) poll(runId);
  }
  function poll(runId) {
    clearTimeout(state.poll);
    state.poll = setTimeout(async () => {
      try {
        state.selected = await api('/api/factor-lab/runs/' + encodeURIComponent(runId));
        await render(state.view, true);
        if (['queued', 'running', 'cancelling'].includes(state.selected.status)) poll(runId);
      } catch (error) { console.error(error); }
    }, 2500);
  }
  async function startRun(engine) {
    const run = await api('/api/factor-lab/runs', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload(engine))
    });
    state.selected = run;
    await render(state.view, true);
    poll(run.run_id);
  }

  function runPanel(run) {
    if (!run) return '<div class="fl2-empty">暂无任务</div>';
    const progress = Math.max(0, Math.min(100, Number(run.progress || 0) * 100));
    return '<div class="fl2-run-card"><div class="fl2-run-head"><div><div class="fl2-run-name">' + esc(displayName(run)) + '</div>' +
      '<div class="fl2-run-meta"><span>' + esc(run.mode || '') + '</span><span>' + esc(run.stage || '') + '</span><span>' + esc(run.message || '') + '</span></div></div>' +
      '<span class="fl2-status ' + esc(run.status) + '">' + esc(STATUS[run.status] || run.status) + '</span></div>' +
      '<div class="fl2-progress"><i style="width:' + progress.toFixed(1) + '%"></i></div><div class="fl2-actions">' +
      '<button class="fl2-button" data-run-open="' + esc(run.run_id) + '">查看</button>' +
      (['queued', 'running'].includes(run.status) ? '<button class="fl2-button danger" data-run-cancel="' + esc(run.run_id) + '">取消</button>' : '') +
      '</div></div>';
  }
  function bindRunActions() {
    document.querySelectorAll('[data-run-open]').forEach(el => { el.onclick = () => selectRun(el.dataset.runOpen); });
    document.querySelectorAll('[data-run-cancel]').forEach(el => { el.onclick = async () => { await api('/api/factor-lab/runs/' + encodeURIComponent(el.dataset.runCancel) + '/cancel', { method: 'POST' }); await selectRun(el.dataset.runCancel); }; });
  }

  function metric(run) {
    const result = run && run.result || {};
    return (result.metrics || {}).test || {};
  }
  function resultRows(result) {
    const rows = result && result.diagnostics && result.diagnostics.rolling;
    if (Array.isArray(rows)) return rows;
    return (((result || {}).metrics || {}).test || {}).series || [];
  }
  function gateGrid(result) {
    const gates = (result && result.gates) || [];
    if (!gates.length) return '<div class="fl2-empty">暂无检验结果</div>';
    return '<div class="fl2-gates">' + gates.map(g => '<div class="fl2-gate ' + (g.passed ? 'pass' : 'fail') + '"><b>' + esc(g.label || g.gate) + '</b><strong>' + (g.passed ? '通过' : '未通过') + '</strong></div>').join('') + '</div>';
  }
  function kpis(run) {
    const m = metric(run), result = run.result || {}, gates = result.gates || [];
    const passed = gates.filter(g => g.passed).length;
    const items = [
      ['测试 RankIC', signed(m.rank_ic, 4), 'Spearman', Math.abs(Number(m.rank_ic)) >= .03],
      ['测试 ICIR', signed(m.icir, 3), '', Number(m.icir) > 0],
      ['成本后 Sharpe', num(m.sharpe, 3), '', Number(m.sharpe) >= .5],
      ['年化收益', pct(m.annual_return, 2), '', Number(m.annual_return) > 0],
      ['最大回撤', pct(m.max_drawdown, 2), '', Number(m.max_drawdown) >= -.25],
      ['检验通过', passed + ' / ' + gates.length, '', gates.length > 0 && passed === gates.length]
    ];
    return '<div class="fl2-kpis">' + items.map(item => '<div class="fl2-kpi ' + (item[3] ? 'good' : 'bad') + '"><small>' + item[0] + '</small><strong>' + item[1] + '</strong><em>' + item[2] + '</em></div>').join('') + '</div>';
  }
  function conclusions(run) {
    const m = metric(run), gates = (run.result && run.result.gates) || [], passed = gates.filter(g => g.passed).length;
    const rankText = Math.abs(Number(m.rank_ic || 0)) >= .03 ? 'RankIC 达标' : 'RankIC 未达标';
    const sharpeText = Number(m.sharpe || 0) >= .5 ? 'Sharpe 达标' : 'Sharpe 未达标';
    const ddText = Number(m.max_drawdown || 0) >= -.25 ? '回撤达标' : '回撤未达标';
    return '<div class="fl2-conclusion"><div><small>预测</small><strong>' + rankText + '，测试 RankIC ' + signed(m.rank_ic, 4) + '</strong></div>' +
      '<div><small>收益风险</small><strong>' + sharpeText + '，' + ddText + '</strong></div>' +
      '<div><small>结论</small><strong>' + passed + ' / ' + gates.length + ' 项通过，' + (passed === gates.length && gates.length ? '可进入下一阶段' : '不晋升') + '</strong></div></div>';
  }
  function chart(id, title, conclusion, wide) {
    return '<div class="fl2-chart ' + (wide ? 'is-wide' : '') + '"><h3>' + esc(title) + '</h3><div class="fl2-chart-conclusion">' + esc(conclusion || '') + '</div><div class="fl2-plot" id="' + esc(id) + '"></div></div>';
  }
  function table(title, rows, columns) {
    rows = rows || [];
    return '<div class="fl2-table-card"><header><h3>' + esc(title) + '</h3></header><div class="fl2-table-scroll"><table class="fl2-table"><thead><tr>' + columns.map(c => '<th>' + esc(c[1]) + '</th>').join('') + '</tr></thead><tbody>' +
      rows.map(row => '<tr>' + columns.map(c => { const value = row && row[c[0]]; const numeric = typeof value === 'number'; return '<td class="' + (numeric ? 'num' : '') + '">' + esc(numeric ? num(value, c[2] ?? 4) : (value ?? '—')) + '</td>'; }).join('') + '</tr>').join('') +
      '</tbody></table></div></div>';
  }

  function analyticsHtml(run, prefix) {
    const result = run.result || {}, diagnostics = result.diagnostics || {}, m = metric(run);
    const icConclusion = Math.abs(Number(m.rank_ic || 0)) >= .03 ? '绝对 RankIC 达到 0.03' : '绝对 RankIC 低于 0.03';
    const riskConclusion = Number(m.sharpe || 0) >= .5 ? '成本后 Sharpe 达标' : '成本后 Sharpe 未达标';
    const candidateRows = (result.selection && result.selection.candidates) || result.candidates || [];
    let html = section('预测能力', '<div class="fl2-chart-grid">' +
      chart(prefix + '-rank', 'RankIC 与滚动 RankIC', icConclusion) +
      chart(prefix + '-icdist', 'RankIC 分布', '观察方向稳定性与尾部') +
      chart(prefix + '-split', '训练 / 验证 / 测试', '同口径比较 RankIC 与 Sharpe') +
      chart(prefix + '-search', '候选排序', '仅训练与验证集参与排序') + '</div>');
    html += section('收益与风险', '<div class="fl2-chart-grid">' +
      chart(prefix + '-nav', '多空净值', riskConclusion) +
      chart(prefix + '-drawdown', '回撤', '测试期最大回撤 ' + pct(m.max_drawdown, 2)) +
      chart(prefix + '-rollsharpe', '滚动 Sharpe', '按非重叠标签周期计算') +
      chart(prefix + '-monthly', '月度收益', '成本后收益热力图') + '</div>');
    html += section('交易特征', '<div class="fl2-chart-grid">' +
      chart(prefix + '-turnover', '换手率与成本拖累', '平均换手率 ' + pct(m.turnover, 2)) +
      chart(prefix + '-cost', '成本敏感性', '0–50bp 统一口径') +
      chart(prefix + '-yearly', '年度表现', '逐年收益与 Sharpe') +
      chart(prefix + '-training', '训练过程', '验证指标与搜索收敛') + '</div>');
    html += section('稳健性', gateGrid(result) + '<div style="height:12px"></div>' +
      table('候选与验证结果', candidateRows.slice(0, 30), [
        ['name', '名称'], ['selection_score', '选择得分', 4], ['train_rank_ic', '训练 RankIC', 4],
        ['valid_rank_ic', '验证 RankIC', 4], ['valid_sharpe', '验证 Sharpe', 3]
      ]));
    if (result.correlation && result.correlation.matrix) {
      html += section('相关性', '<div class="fl2-chart-grid">' + chart(prefix + '-corr', '因子相关矩阵', '识别冗余与聚类', true) + '</div>');
    }
    return html;
  }

  const palette = { red: '#a93222', blue: '#2d6f9f', green: '#2d8b55', gold: '#bd8026', gray: '#7a8798' };
  function plot(id, data, extra) {
    const el = $(id); if (!el || !window.Plotly) return;
    const layout = Object.assign({
      font: { family: 'Arial, Microsoft YaHei, sans-serif', size: 11, color: '#405066' },
      paper_bgcolor: '#fff', plot_bgcolor: '#fff', margin: { l: 48, r: 22, t: 28, b: 48 },
      xaxis: { gridcolor: '#edf0f3', zerolinecolor: '#d8dee6' }, yaxis: { gridcolor: '#edf0f3', zerolinecolor: '#d8dee6' },
      legend: { orientation: 'h', x: 0, y: 1.13 }, hovermode: 'x unified'
    }, extra || {});
    Plotly.react(el, data, layout, { responsive: true, displaylogo: false, modeBarButtonsToRemove: ['lasso2d', 'select2d'] });
  }
  function periodRows(rows) {
    if (state.period === 'all' || !rows.length) return rows;
    const days = state.period === '1y' ? 365 : 1095;
    const last = new Date(rows[rows.length - 1].date); const start = new Date(last.getTime() - days * 86400000);
    return rows.filter(row => new Date(row.date) >= start);
  }
  function drawAnalytics(run, prefix) {
    const result = run.result || {}, diagnostics = result.diagnostics || {};
    const rows = periodRows(resultRows(result));
    const dates = rows.map(x => x.date);
    plot(prefix + '-rank', [
      { type: 'scatter', mode: 'lines', x: dates, y: rows.map(x => x.rank_ic), name: 'RankIC', line: { color: palette.blue, width: 1 } },
      { type: 'scatter', mode: 'lines', x: dates, y: rows.map(x => x.rolling_rank_ic), name: '20 日滚动', line: { color: palette.red, width: 2 } }
    ], { yaxis: { gridcolor: '#edf0f3', zerolinecolor: '#aeb8c5' } });
    const dist = diagnostics.ic_distribution || [];
    plot(prefix + '-icdist', [{ type: 'bar', x: dist.map(x => ((x.left + x.right) / 2).toFixed(3)), y: dist.map(x => x.count), marker: { color: dist.map(x => x.left >= 0 ? palette.green : '#c75d50') }, name: '频数' }], { showlegend: false });
    const split = diagnostics.split_summary || [];
    plot(prefix + '-split', [
      { type: 'bar', x: split.map(x => x.split), y: split.map(x => x.rank_ic), name: 'RankIC', marker: { color: palette.blue } },
      { type: 'bar', x: split.map(x => x.split), y: split.map(x => x.sharpe), name: 'Sharpe', marker: { color: palette.gold }, yaxis: 'y2' }
    ], { barmode: 'group', yaxis2: { overlaying: 'y', side: 'right', showgrid: false } });
    const candidates = ((result.selection || {}).candidates || result.candidates || []).slice(0, 12);
    plot(prefix + '-search', [{ type: 'bar', orientation: 'h', y: candidates.map((x, i) => x.name || ('候选 ' + (i + 1))).reverse(), x: candidates.map(x => Number(x.selection_score || x.valid_rank_ic || 0)).reverse(), marker: { color: palette.red } }], { showlegend: false, margin: { l: 115, r: 20, t: 24, b: 42 } });
    plot(prefix + '-nav', [
      { type: 'scatter', mode: 'lines', x: dates, y: rows.map(x => x.nav_gross), name: '成本前', line: { color: palette.gray, width: 1.5 } },
      { type: 'scatter', mode: 'lines', x: dates, y: rows.map(x => x.nav_net), name: '成本后', line: { color: palette.green, width: 2.2 } }
    ]);
    plot(prefix + '-drawdown', [{ type: 'scatter', mode: 'lines', x: dates, y: rows.map(x => x.drawdown), fill: 'tozeroy', line: { color: '#b64334', width: 1.5 }, fillcolor: 'rgba(182,67,52,.18)' }], { showlegend: false, yaxis: { tickformat: '.1%', gridcolor: '#edf0f3' } });
    plot(prefix + '-rollsharpe', [{ type: 'scatter', mode: 'lines', x: dates, y: rows.map(x => x.rolling_sharpe), line: { color: palette.gold, width: 2 } }], { showlegend: false });
    const monthly = diagnostics.monthly || [], years = [...new Set(monthly.map(x => x.month.slice(0, 4)))], months = Array.from({ length: 12 }, (_, i) => String(i + 1).padStart(2, '0'));
    plot(prefix + '-monthly', [{ type: 'heatmap', x: months, y: years, z: years.map(y => months.map(m => { const row = monthly.find(x => x.month === y + '-' + m); return row ? row.return : null; })), colorscale: [[0, '#b84b3f'], [.5, '#f7f7f5'], [1, '#298453']], zmid: 0, colorbar: { tickformat: '.1%' }, hovertemplate: '%{y}-%{x}<br>%{z:.2%}<extra></extra>' }], { margin: { l: 55, r: 70, t: 25, b: 45 } });
    plot(prefix + '-turnover', [
      { type: 'scatter', mode: 'lines', x: dates, y: rows.map(x => x.turnover), name: '换手率', line: { color: palette.blue, width: 1.5 } },
      { type: 'bar', x: dates, y: rows.map(x => Number(x.gross || 0) - Number(x.net || 0)), name: '成本拖累', marker: { color: 'rgba(169,50,34,.28)' }, yaxis: 'y2' }
    ], { yaxis: { tickformat: '.0%', gridcolor: '#edf0f3' }, yaxis2: { overlaying: 'y', side: 'right', tickformat: '.2%', showgrid: false } });
    const costs = diagnostics.cost_sensitivity || [];
    plot(prefix + '-cost', [
      { type: 'scatter', mode: 'lines+markers', x: costs.map(x => x.cost_bps), y: costs.map(x => x.sharpe), name: 'Sharpe', line: { color: palette.red } },
      { type: 'scatter', mode: 'lines+markers', x: costs.map(x => x.cost_bps), y: costs.map(x => x.return), name: '年化收益', line: { color: palette.green }, yaxis: 'y2' }
    ], { xaxis: { title: 'bp', gridcolor: '#edf0f3' }, yaxis2: { overlaying: 'y', side: 'right', tickformat: '.0%', showgrid: false } });
    const yearly = diagnostics.yearly || [];
    plot(prefix + '-yearly', [
      { type: 'bar', x: yearly.map(x => x.year), y: yearly.map(x => x.return), name: '年化收益', marker: { color: palette.green } },
      { type: 'scatter', mode: 'lines+markers', x: yearly.map(x => x.year), y: yearly.map(x => x.sharpe), name: 'Sharpe', line: { color: palette.red }, yaxis: 'y2' }
    ], { yaxis: { tickformat: '.0%', gridcolor: '#edf0f3' }, yaxis2: { overlaying: 'y', side: 'right', showgrid: false } });
    const history = (result.training_history || []).flatMap(seed => (seed.history || []).map(x => Object.assign({ seed: seed.seed }, x)));
    const curve = result.training_curve || [];
    if (history.length) plot(prefix + '-training', [
      { type: 'scatter', mode: 'lines+markers', x: history.map(x => x.epoch), y: history.map(x => x.valid_rank_ic), name: '验证 RankIC', line: { color: palette.blue } },
      { type: 'scatter', mode: 'lines', x: history.map(x => x.epoch), y: history.map(x => x.train_loss), name: '训练损失', line: { color: palette.gray }, yaxis: 'y2' }
    ], { yaxis2: { overlaying: 'y', side: 'right', showgrid: false } });
    else plot(prefix + '-training', [
      { type: 'scatter', mode: 'lines+markers', x: curve.map(x => x.episodes), y: curve.map(x => x.mean_reward), name: '平均奖励', line: { color: palette.blue } },
      { type: 'scatter', mode: 'lines+markers', x: curve.map(x => x.episodes), y: curve.map(x => x.best_reward), name: '最优奖励', line: { color: palette.red } }
    ]);
    if (result.correlation && result.correlation.matrix) plot(prefix + '-corr', [{ type: 'heatmap', x: result.correlation.labels, y: result.correlation.labels, z: result.correlation.matrix, zmin: -1, zmax: 1, zmid: 0, colorscale: [[0, '#38678f'], [.5, '#f7f7f5'], [1, '#a83b2d']], colorbar: { thickness: 12 } }], { margin: { l: 105, r: 50, t: 30, b: 100 } });
  }

  function bindConfig(engine, fixed) {
    const engineSelect = $('fl2-engine');
    if (engineSelect && !fixed) engineSelect.onchange = () => { const host = $('fl2-model-fields'); if (host) host.innerHTML = modelFields(engineSelect.value); };
    const start = $('fl2-start'); if (start) start.onclick = () => startRun(fixed ? engine : read('fl2-engine', 'lstm')).catch(showError);
    bindRunActions();
  }
  function showError(error) { const box = $('core-conclusion'); if (box) { box.hidden = false; box.innerHTML = '<p>' + esc(error.message || error) + '</p>'; } }

  async function renderHome() {
    setHeader('home'); await loadBase();
    const latest = state.selected || state.runs[0];
    root(section('任务设置', primaryToolbar('lstm', false) + '<div style="height:12px"></div>' + parameterGroups('lstm') +
      '<div class="fl2-actions"><button class="fl2-button primary" id="fl2-start">运行</button></div>') +
      section('任务状态', runPanel(latest)));
    bindConfig('lstm', false);
  }

  function runSelector(id, completedOnly, engine) {
    let runs = state.runs.filter(x => !completedOnly || x.status === 'completed');
    if (engine) runs = runs.filter(x => x.engine === engine);
    return select('任务', id, [['', '请选择'], ...runs.map(x => [x.run_id, displayName(x)])], state.selected && state.selected.run_id || '');
  }
  function viewerToolbar(extra) {
    return '<div class="fl2-toolbar"><div class="fl2-toolbar-row is-compact">' + runSelector('fl2-view-run', true) +
      select('区间', 'fl2-period', [['all', '全部'], ['3y', '近 3 年'], ['1y', '近 1 年']], state.period) +
      select('收益口径', 'fl2-line', [['net', '成本后'], ['gross', '成本前']], state.line) +
      (extra || '<div></div>') + '<button class="fl2-button" id="fl2-refresh">刷新</button></div></div>';
  }
  function bindViewer(draw) {
    const selectRunEl = $('fl2-view-run'); if (selectRunEl) selectRunEl.onchange = () => selectRunEl.value && selectRun(selectRunEl.value);
    const period = $('fl2-period'); if (period) period.onchange = () => { state.period = period.value; draw(); };
    const line = $('fl2-line'); if (line) line.onchange = () => { state.line = line.value; draw(); };
    const refresh = $('fl2-refresh'); if (refresh) refresh.onclick = () => render(state.view, true);
  }

  function familyGrid() {
    const families = [{ id: 'all', count: (state.catalog.factors || []).length }, ...(state.catalog.families || [])];
    return '<div class="fl2-family-grid">' + families.map(f => '<button class="fl2-family ' + (state.family === f.id ? 'is-active' : '') + '" data-family="' + esc(f.id) + '"><span>' + esc(FAMILY[f.id] || f.id) + '</span><strong>' + esc(f.count || 0) + '</strong></button>').join('') + '</div>';
  }
  async function renderDashboard() {
    setHeader('dashboard'); await loadBase();
    let selected = state.selected;
    if (!selected || selected.status !== 'completed') selected = state.runs.find(x => x.status === 'completed');
    selected = await hydrate(selected); state.selected = selected;
    const factors = (state.catalog.factors || []).filter(f => state.family === 'all' || String(f.factor_group || '').toLowerCase().includes(state.family)).slice(0, 120);
    const factorTable = table('因子目录', factors, [['factor_name', '因子'], ['factor_group', '类型'], ['source_agent', '来源'], ['rank_ic', 'RankIC', 4], ['icir', 'ICIR', 3], ['coverage', '覆盖率', 3], ['last_date', '更新日期']]);
    root(viewerToolbar(select('因子类型', 'fl2-family-select', Object.entries(FAMILY), state.family)) + '<div style="height:12px"></div>' + familyGrid() +
      (selected ? section('核心结果', conclusions(selected) + kpis(selected)) + analyticsHtml(selected, 'dash') : '<div class="fl2-empty">暂无已完成任务</div>') +
      section('因子目录', factorTable));
    bindViewer(() => selected && drawAnalytics(selected, 'dash'));
    const familySelect = $('fl2-family-select'); if (familySelect) familySelect.onchange = () => { state.family = familySelect.value; renderDashboard(); };
    document.querySelectorAll('[data-family]').forEach(el => { el.onclick = () => { state.family = el.dataset.family; renderDashboard(); }; });
    if (selected) drawAnalytics(selected, 'dash');
  }

  function miningTabs() {
    return '<div class="fl2-tabs">' + [['formula', '普通因子'], ['lstm', 'LSTM'], ['rl_transformer', 'RL+Transformer']].map(x => '<button class="fl2-tab ' + (state.miningTab === x[0] ? 'is-active' : '') + '" data-mining-tab="' + x[0] + '">' + x[1] + '</button>').join('') + '</div>';
  }
  async function renderMining() {
    setHeader('mining'); await loadBase();
    let body = '';
    if (state.miningTab === 'formula') {
      body = '<div class="fl2-split"><div class="fl2-card" style="padding:16px"><div class="fl2-field"><span>可用字段</span><div class="fl2-code">ret_1 ret_5 ret_20 ret_60 vol_20 down_vol_20 price_pos_60 volume_z_20 amihud_20 turnover volume_ratio value_ep value_bp value_sp dividend log_mv moneyflow large_flow extreme_flow range_1 gap_1</div></div></div>' +
        '<div class="fl2-card" style="padding:16px"><label class="fl2-field"><span>因子表达式（Postfix）</span><textarea id="fl2-formula">ret_20 CS_RANK ret_5 CS_RANK SUB</textarea></label>' +
        '<label class="fl2-field" style="margin-top:12px"><span>批注</span><textarea id="fl2-annotation"></textarea></label><div id="fl2-formula-result" class="fl2-code" style="margin-top:12px">—</div>' +
        '<div class="fl2-actions"><button class="fl2-button primary" id="fl2-formula-check">校验</button></div></div></div>';
    } else {
      body = primaryToolbar(state.miningTab, true) + '<div style="height:12px"></div>' + parameterGroups(state.miningTab) +
        '<div class="fl2-actions"><button class="fl2-button primary" id="fl2-start">运行 ' + esc(ENGINE[state.miningTab]) + '</button></div>';
    }
    let selected = state.selected && state.selected.engine === state.miningTab ? state.selected : state.runs.find(x => x.engine === state.miningTab);
    selected = await hydrate(selected); if (selected) state.selected = selected;
    root(miningTabs() + section('参数', body) + section('任务状态', runPanel(selected)) +
      (selected && selected.result ? section('结果', conclusions(selected) + kpis(selected) + analyticsHtml(selected, 'mine')) : ''));
    document.querySelectorAll('[data-mining-tab]').forEach(el => { el.onclick = () => { state.miningTab = el.dataset.miningTab; renderMining(); }; });
    if (state.miningTab === 'formula') {
      const button = $('fl2-formula-check'); if (button) button.onclick = async () => {
        const result = await api('/api/factor-lab/formula/validate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ formula: $('fl2-formula').value }) });
        $('fl2-formula-result').textContent = result.valid ? '校验通过\n' + result.tokens.join(' ') : '校验未通过\n' + (result.invalid_tokens || []).join(' ');
      };
    } else bindConfig(state.miningTab, true);
    bindRunActions();
    if (selected && selected.result) drawAnalytics(selected, 'mine');
  }

  async function renderTesting() {
    setHeader('testing'); await loadBase();
    let selected = state.selected && state.selected.status === 'completed' ? state.selected : state.runs.find(x => x.status === 'completed');
    selected = await hydrate(selected); state.selected = selected;
    const toolbar = '<div class="fl2-toolbar"><div class="fl2-toolbar-row">' + runSelector('fl2-test-source', true) +
      select('标的池', 'fl2-universe', [['ALL_A', '全 A 可交易池'], ['CSI300', '沪深 300'], ['CSI500', '中证 500'], ['CSI1000', '中证 1000']], 'ALL_A') +
      field('最大股票数', 'fl2-assets', 240, 'number', 'min="40" max="800" step="20"') + field('历史月数', 'fl2-months', 72, 'number', 'min="12" max="180" step="6"') +
      field('单边成本（bp）', 'fl2-cost', 15, 'number', 'min="0" max="200"') + select('检验模式', 'fl2-test-mode', [['single_joint', '单因子与多因子'], ['single', '单因子'], ['joint', '多因子']], 'single_joint') +
      select('测试集', 'fl2-test-lock', [['locked', '冻结']], 'locked') + '<div class="fl2-actions" style="margin:0"><button class="fl2-button primary" id="fl2-start-test">运行检验</button></div></div></div>';
    const factors = selected && selected.result && selected.result.factors || [];
    root(section('检验设置', toolbar) + (selected ? section('核心结果', conclusions(selected) + kpis(selected)) + analyticsHtml(selected, 'test') : '<div class="fl2-empty">请选择任务</div>') +
      (factors.length ? section('单因子结果', table('单因子结果', factors, [['factor', '因子'], ['train_rank_ic', '训练 RankIC', 4], ['valid_rank_ic', '验证 RankIC', 4], ['test_rank_ic', '测试 RankIC', 4], ['test_sharpe', '测试 Sharpe', 3], ['test_max_drawdown', '最大回撤', 3], ['stability', '稳定性', 4]])) : ''));
    const source = $('fl2-test-source'); if (source) source.onchange = () => source.value && selectRun(source.value);
    const start = $('fl2-start-test'); if (start) start.onclick = () => startRun('joint_test').catch(showError);
    if (selected) drawAnalytics(selected, 'test');
  }

  async function renderStrategy() {
    setHeader('strategy'); await loadBase();
    let selected = state.selected && state.selected.engine === 'strategy' ? state.selected : state.runs.find(x => x.engine === 'strategy');
    selected = await hydrate(selected); if (selected) state.selected = selected;
    root(section('参数', primaryToolbar('strategy', true) + '<div style="height:12px"></div>' + parameterGroups('strategy') + '<div class="fl2-actions"><button class="fl2-button primary" id="fl2-start">运行策略</button></div>') +
      section('任务状态', runPanel(selected)) + (selected && selected.result ? section('结果', conclusions(selected) + kpis(selected) + analyticsHtml(selected, 'strategy')) : ''));
    bindConfig('strategy', true); bindRunActions(); if (selected && selected.result) drawAnalytics(selected, 'strategy');
  }

  async function renderHistory(force) {
    setHeader('history'); await loadBase(Boolean(force));
    let runs = state.runs.filter(x => state.historyEngine === 'all' || x.engine === state.historyEngine).filter(x => state.historyStatus === 'all' || x.status === state.historyStatus);
    const controls = '<div class="fl2-toolbar"><div class="fl2-toolbar-row">' +
      select('类型', 'fl2-history-engine', [['all', '全部'], ['lstm', 'LSTM'], ['rl_transformer', 'RL+Transformer'], ['strategy', '投资策略'], ['joint_test', '联合检验']], state.historyEngine) +
      select('状态', 'fl2-history-status', [['all', '全部'], ['completed', '已完成'], ['running', '运行中'], ['failed', '失败'], ['cancelled', '已取消']], state.historyStatus) +
      '<div></div><button class="fl2-button" id="fl2-history-refresh">刷新</button></div></div>';
    const runRows = runs.map(x => ({ name: displayName(x), engine: ENGINE[x.engine], mode: x.mode, status: STATUS[x.status] || x.status, stage: x.stage, progress: num(Number(x.progress || 0) * 100, 1) + '%', created_at: x.created_at, elapsed_seconds: x.elapsed_seconds, run_id: x.run_id }));
    const runTable = '<div class="fl2-table-card"><header><h3>任务记录</h3></header><div class="fl2-table-scroll"><table class="fl2-table"><thead><tr><th>任务</th><th>模型</th><th>等级</th><th>状态</th><th>阶段</th><th>进度</th><th>耗时（秒）</th><th></th></tr></thead><tbody>' +
      runRows.map(x => '<tr><td>' + esc(x.name) + '</td><td>' + esc(x.engine) + '</td><td>' + esc(x.mode) + '</td><td>' + esc(x.status) + '</td><td>' + esc(x.stage || '') + '</td><td class="num">' + esc(x.progress) + '</td><td class="num">' + esc(x.elapsed_seconds ?? '—') + '</td><td><button class="fl2-button" data-run-open="' + esc(x.run_id) + '">查看</button></td></tr>').join('') + '</tbody></table></div></div>';
    root('<div class="fl2-tabs"><button class="fl2-tab ' + (state.historyTab === 'runs' ? 'is-active' : '') + '" data-history-tab="runs">任务记录</button><button class="fl2-tab ' + (state.historyTab === 'catalog' ? 'is-active' : '') + '" data-history-tab="catalog">本地因子目录</button></div>' +
      section('筛选', controls) + (state.historyTab === 'runs' ? section('任务记录', runTable) : section('本地因子目录', table('本地因子目录', state.catalog.factors || [], [['factor_name', '因子'], ['factor_group', '类型'], ['source_agent', '来源'], ['value_count', '记录数', 0], ['rank_ic', 'RankIC', 4], ['icir', 'ICIR', 3], ['coverage', '覆盖率', 3], ['last_date', '更新日期']]))));
    document.querySelectorAll('[data-history-tab]').forEach(el => { el.onclick = () => { state.historyTab = el.dataset.historyTab; renderHistory(); }; });
    $('fl2-history-engine').onchange = e => { state.historyEngine = e.target.value; renderHistory(); };
    $('fl2-history-status').onchange = e => { state.historyStatus = e.target.value; renderHistory(); };
    $('fl2-history-refresh').onclick = () => renderHistory(true); bindRunActions();
  }

  async function render(view, preserveScroll) {
    state.view = view || state.view; setHeader(state.view);
    if (!preserveScroll) window.scrollTo({ top: 0, left: 0, behavior: 'auto' });
    root('<div class="fl2-empty">加载中</div>');
    try {
      if (state.view === 'home') await renderHome();
      else if (state.view === 'dashboard') await renderDashboard();
      else if (state.view === 'mining') await renderMining();
      else if (state.view === 'testing') await renderTesting();
      else if (state.view === 'strategy') await renderStrategy();
      else await renderHistory();
    } catch (error) { console.error(error); root('<div class="fl2-empty">' + esc(error.message || error) + '</div>'); showError(error); }
  }

  function cleanFactorNavigation() {
    document.querySelectorAll('.nav-item[data-target^="factorlab:"]').forEach(item => {
      for (const node of item.childNodes) if (node.nodeType === Node.TEXT_NODE) node.textContent = node.textContent.replace(/^\s*\d+\s*/, '');
    });
  }
  window.FactorLaboratory = { render, state };
  window.addEventListener('DOMContentLoaded', cleanFactorNavigation);
}());

/* Final family-selector normalization. */
(function () {
  'use strict';
  const familyValues = { technical: '技术', money: '资金', fundamental: '基本面', valuation: '估值', macro: '宏观', discovered: '普通因子', lstm: 'LSTM', rl_transformer: 'RL+Transformer' };

  function normalizeFamilySelect() {
    const select = document.getElementById('fl2-family-select');
    if (!select || select.dataset.normalized === '1') return;
    Array.from(select.options).forEach(option => { if (familyValues[option.value]) option.value = familyValues[option.value]; });
    select.dataset.normalized = '1';
  }

  const observer = new MutationObserver(normalizeFamilySelect);
  window.addEventListener('DOMContentLoaded', function () {
    observer.observe(document.body, { childList: true, subtree: true });
    // Route correction is handled by the canonical app router.
  });

  document.addEventListener('click', function (event) {
    const button = event.target.closest && event.target.closest('[data-family]');
    if (!button || button.dataset.family === 'all' || !familyValues[button.dataset.family]) return;
    const select = document.getElementById('fl2-family-select');
    if (!select) return;
    event.preventDefault(); event.stopPropagation(); event.stopImmediatePropagation();
    normalizeFamilySelect();
    select.value = familyValues[button.dataset.family];
    select.dispatchEvent(new Event('change', { bubbles: true }));
  }, true);
}());
