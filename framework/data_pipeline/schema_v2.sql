pragma journal_mode = wal;
pragma synchronous = normal;

create table if not exists source_manifest (
  source_name text not null,
  source_path text not null,
  source_table text not null,
  target_table text not null,
  start_date text not null,
  end_date text not null,
  rows_loaded integer not null default 0,
  min_date text,
  max_date text,
  frequency text not null,
  update_mode text not null,
  quota_policy text not null,
  status text not null,
  message text,
  updated_at text not null,
  primary key (source_name, source_table, target_table)
);

create table if not exists update_log (
  run_id text not null,
  step text not null,
  status text not null,
  message text,
  started_at text not null,
  ended_at text,
  primary key (run_id, step)
);

create table if not exists split_definition (
  split_name text primary key,
  start_date text not null,
  end_date text not null,
  purpose text not null
);

create table if not exists trade_calendar (
  trade_date text primary key,
  is_trade_day integer not null,
  is_month_last_trade integer default 0,
  source text
);

create table if not exists security_master (
  ts_code text primary key,
  symbol text,
  stock_name text,
  exchange text,
  market text,
  board_name text,
  list_status text,
  list_date text,
  delist_date text,
  is_hs text,
  source text
);

create table if not exists stock_ohlcv_daily (
  trade_date text not null,
  ts_code text not null,
  stock_name text,
  open real,
  high real,
  low real,
  close real,
  qfq_close real,
  pre_close real,
  pct_chg real,
  vol real,
  amount real,
  up_limit real,
  down_limit real,
  suspend_timing text,
  primary key (trade_date, ts_code)
);

create index if not exists idx_stock_ohlcv_code_date on stock_ohlcv_daily(ts_code, trade_date);

create table if not exists stock_valuation_daily (
  trade_date text not null,
  ts_code text not null,
  pe_ttm real,
  pb real,
  ps_ttm real,
  dv_ttm real,
  total_mv real,
  circ_mv real,
  turnover_rate real,
  turnover_rate_f real,
  volume_ratio real,
  primary key (trade_date, ts_code)
);

create index if not exists idx_stock_val_code_date on stock_valuation_daily(ts_code, trade_date);

create table if not exists stock_moneyflow_daily (
  trade_date text not null,
  ts_code text not null,
  net_mf_amount real,
  buy_lg_amount real,
  sell_lg_amount real,
  buy_elg_amount real,
  sell_elg_amount real,
  primary key (trade_date, ts_code)
);

create table if not exists financial_report_visible (
  ts_code text not null,
  visible_date text not null,
  end_date text,
  report_type text,
  total_revenue real,
  n_income_attr_p real,
  gross_margin real,
  netprofit_margin real,
  roe real,
  roa real,
  debt_to_assets real,
  current_ratio real,
  assets_turn real,
  op_yoy real,
  tr_yoy real,
  netprofit_yoy real,
  primary key (ts_code, visible_date, end_date)
);

create index if not exists idx_fin_visible_code_date on financial_report_visible(ts_code, visible_date);

create table if not exists sw_l1_industry_daily (
  ts_code text not null,
  start_date text not null,
  end_date text,
  industry_code text,
  industry_name text not null,
  source text,
  primary key (ts_code, start_date, industry_code)
);

create table if not exists index_constituent_period (
  universe text not null,
  index_code text,
  trade_date text not null,
  con_code text not null,
  weight real,
  source text,
  status text not null default 'ready',
  primary key (universe, trade_date, con_code)
);

create index if not exists idx_index_constituent_lookup on index_constituent_period(universe, con_code, trade_date);

create table if not exists etf_master (
  ts_code text primary key,
  fund_name text,
  asset_class text,
  market text,
  source text
);

create table if not exists etf_ohlcv_daily (
  trade_date text not null,
  ts_code text not null,
  fund_name text,
  open real,
  high real,
  low real,
  close real,
  pct_chg real,
  vol real,
  amount real,
  fund_type text,
  primary key (trade_date, ts_code)
);

create index if not exists idx_etf_code_date on etf_ohlcv_daily(ts_code, trade_date);

create table if not exists macro_monthly (
  month text primary key,
  pmi_manufacturing real,
  pmi_non_manufacturing real,
  pmi_composite real,
  cpi_national_yoy real,
  ppi_yoy real,
  m1_yoy real,
  m2_yoy real,
  sf_inc_month real,
  sf_stock_endval real,
  source text
);

create table if not exists news_event_daily (
  news_id text primary key,
  publish_date text not null,
  headline text,
  category text,
  subject_type text,
  subject_code text,
  source_site text,
  source_url text,
  event_tag text
);

create index if not exists idx_news_event_date_source on news_event_daily(publish_date, source_site);

create table if not exists broker_report_index (
  report_id text primary key,
  report_date text,
  org text,
  title text,
  researcher text,
  link text,
  model_tag text,
  source text
);

create table if not exists lhb_daily (
  trade_date text not null,
  ts_code text not null,
  stock_name text,
  reason text,
  buy_amount real,
  sell_amount real,
  net_amount real,
  institution_net_amount real,
  source text,
  primary key (trade_date, ts_code, reason)
);

create table if not exists kline_feature_daily (
  trade_date text not null,
  ts_code text not null,
  frequency text not null default 'D',
  feature_name text not null,
  feature_value real,
  signal_direction integer,
  evidence text,
  primary key (trade_date, ts_code, frequency, feature_name)
);

create table if not exists factor_value_daily (
  trade_date text not null,
  ts_code text not null,
  factor_name text not null,
  factor_value real,
  factor_group text,
  source_agent text,
  primary key (trade_date, ts_code, factor_name)
);

create table if not exists factor_test_result (
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

create table if not exists model_signal_daily (
  trade_date text not null,
  universe text not null,
  model_name text not null,
  ts_code text not null,
  industry_name text,
  score real,
  rank_no integer,
  target_weight real,
  primary key (trade_date, universe, model_name, ts_code)
);

create table if not exists style_score_daily (
  trade_date text not null,
  universe text not null,
  style_name text not null,
  score real,
  chosen integer,
  primary key (trade_date, universe, style_name)
);

create table if not exists industry_score_daily (
  trade_date text not null,
  universe text not null,
  industry_name text not null,
  prosperity_score real,
  valuation_score real,
  earnings_score real,
  flow_score real,
  crowding_score real,
  technical_score real,
  event_score real,
  total_score real,
  chosen integer,
  primary key (trade_date, universe, industry_name)
);

create table if not exists asset_regime_daily (
  trade_date text primary key,
  regime text,
  equity_budget real,
  bond_budget real,
  commodity_budget real,
  cash_budget real,
  evidence text
);

create table if not exists portfolio_target_daily (
  trade_date text not null,
  universe text not null,
  model_name text not null,
  ts_code text not null,
  target_weight real,
  score real,
  industry_name text,
  primary key (trade_date, universe, model_name, ts_code)
);

create table if not exists backtest_nav (
  run_id text not null,
  universe text not null,
  model_name text not null,
  split_name text not null,
  trade_date text not null,
  nav real,
  period_return real,
  benchmark_return real,
  excess_return real,
  primary key (run_id, universe, model_name, split_name, trade_date)
);

create table if not exists backtest_trade (
  run_id text not null,
  universe text not null,
  model_name text not null,
  trade_date text not null,
  ts_code text not null,
  old_weight real,
  new_weight real,
  turnover real,
  cost real,
  primary key (run_id, universe, model_name, trade_date, ts_code)
);

create table if not exists metrics_by_split_year (
  run_id text not null,
  universe text not null,
  model_name text not null,
  split_name text not null,
  year text not null,
  periods integer,
  total_return real,
  annual_return real,
  annual_volatility real,
  sharpe real,
  max_drawdown real,
  win_rate real,
  excess_annual_return real,
  information_ratio real,
  target_pass integer,
  message text,
  primary key (run_id, universe, model_name, split_name, year)
);

create table if not exists data_quality_check (
  check_id text primary key,
  table_name text not null,
  field_name text,
  check_type text not null,
  status text not null,
  metric_value real,
  message text,
  checked_at text not null
);

create table if not exists live_tracking (
  trade_date text not null,
  universe text not null,
  model_name text not null,
  ts_code text not null,
  prediction_score real,
  realized_t1 real,
  realized_t5 real,
  realized_t20 real,
  review_status text,
  primary key (trade_date, universe, model_name, ts_code)
);
