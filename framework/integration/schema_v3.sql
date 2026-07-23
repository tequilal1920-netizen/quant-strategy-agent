create table if not exists v3_run_manifest (
  run_id text primary key,
  started_at text not null,
  ended_at text,
  status text not null,
  start_date text not null,
  end_date text not null,
  train_window text not null,
  valid_window text not null,
  test_window text not null,
  target_annual_return real not null,
  target_sharpe real not null,
  message text
);

create table if not exists v3_module_status (
  run_id text not null,
  module_name text not null,
  status text not null,
  coverage real,
  latest_date text,
  rows integer,
  target_pass integer,
  message text,
  artifact_path text,
  updated_at text not null,
  primary key (run_id, module_name)
);

create table if not exists v3_data_dictionary (
  run_id text not null,
  table_name text not null,
  field_name text not null,
  field_type text,
  data_layer text not null,
  update_frequency text,
  used_by text,
  required_flag integer not null,
  quality_rule text,
  source_priority text,
  primary key (run_id, table_name, field_name)
);

create table if not exists v3_table_quality (
  run_id text not null,
  table_name text not null,
  rows integer,
  min_date text,
  max_date text,
  missing_key_rows integer,
  status text not null,
  message text,
  checked_at text not null,
  primary key (run_id, table_name)
);

create table if not exists v3_report_evidence (
  run_id text not null,
  report_id text not null,
  report_date text,
  org text,
  title text not null,
  researcher text,
  link text,
  module_name text not null,
  method_tag text,
  evidence_level text not null,
  adopted_flag integer not null,
  notes text,
  primary key (run_id, report_id, module_name)
);

create table if not exists v3_model_evidence_link (
  run_id text not null,
  model_name text not null,
  module_name text not null,
  report_id text not null,
  adopted_component text,
  limitation text,
  primary key (run_id, model_name, report_id)
);

create table if not exists v3_weekly_snapshot (
  run_id text not null,
  week_end text not null,
  macro_json text,
  event_json text,
  industry_json text,
  lhb_json text,
  conclusion_json text,
  primary key (run_id, week_end)
);

create table if not exists v3_macro_regime (
  run_id text not null,
  month text not null,
  growth_state text,
  inflation_state text,
  liquidity_state text,
  risk_state text,
  equity_budget real,
  bond_budget real,
  commodity_budget real,
  cash_budget real,
  evidence_json text,
  primary key (run_id, month)
);

create table if not exists v3_asset_allocation_signal (
  run_id text not null,
  rebalance_date text not null,
  etf_code text not null,
  fund_name text,
  asset_class text not null,
  score real,
  target_weight real,
  reason_json text,
  primary key (run_id, rebalance_date, etf_code)
);

create table if not exists v3_style_signal (
  run_id text not null,
  rebalance_date text not null,
  universe text not null,
  style_name text not null,
  score real,
  target_weight real,
  reason_json text,
  primary key (run_id, rebalance_date, universe, style_name)
);

create table if not exists v3_industry_signal (
  run_id text not null,
  rebalance_date text not null,
  universe text not null,
  industry_name text not null,
  score real,
  target_weight real,
  reason_json text,
  primary key (run_id, rebalance_date, universe, industry_name)
);

create table if not exists v3_kline_skill_registry (
  run_id text not null,
  skill_id text not null,
  family text not null,
  pattern_name text not null,
  direction text,
  lookback integer,
  logic text not null,
  source_basis text,
  implementation_status text not null,
  version text not null,
  primary key (run_id, skill_id)
);

create table if not exists v3_kline_signal_audit (
  run_id text not null,
  trade_date text not null,
  ts_code text not null,
  frequency text not null,
  bullish_score real,
  bearish_score real,
  neutral_score real,
  pattern_count integer,
  feature_count integer,
  top_patterns_json text,
  codex_package_json text,
  primary key (run_id, trade_date, ts_code, frequency)
);

create table if not exists v3_factor_candidate_registry (
  run_id text not null,
  factor_name text not null,
  factor_group text,
  expression text,
  source_agent text,
  train_pass integer,
  valid_pass integer,
  test_pass integer,
  full_pass integer,
  status text not null,
  notes text,
  primary key (run_id, factor_name)
);

create table if not exists v3_factor_validation (
  run_id text not null,
  universe text not null,
  factor_name text not null,
  split_name text not null,
  rank_ic real,
  icir real,
  group_spread real,
  turnover real,
  coverage real,
  pass_flag integer,
  message text,
  primary key (run_id, universe, factor_name, split_name)
);

create table if not exists v3_fundamental_stock_card (
  run_id text not null,
  as_of text not null,
  ts_code text not null,
  stock_name text,
  industry_name text,
  quality_score real,
  growth_score real,
  valuation_score real,
  leverage_score real,
  total_score real,
  summary_json text,
  primary key (run_id, as_of, ts_code)
);

create table if not exists v3_portfolio_layer_target (
  run_id text not null,
  rebalance_date text not null,
  universe text not null,
  layer_name text not null,
  ts_code text not null,
  industry_name text,
  target_weight real,
  score real,
  source_model text,
  reason_json text,
  primary key (run_id, rebalance_date, universe, layer_name, ts_code)
);

create table if not exists v3_backtest_audit (
  run_id text not null,
  universe text not null,
  model_name text not null,
  split_name text not null,
  year text not null,
  periods integer,
  annual_return real,
  sharpe real,
  max_drawdown real,
  information_ratio real,
  target_pass integer,
  issues_json text,
  primary key (run_id, universe, model_name, split_name, year)
);
