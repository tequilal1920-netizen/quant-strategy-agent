create table if not exists v4_run_manifest (
  run_id text primary key,
  started_at text not null,
  ended_at text,
  status text not null,
  start_date text not null,
  end_date text not null,
  train_window text not null,
  valid_window text not null,
  test_window text not null,
  strict_target text not null,
  message text
);

create table if not exists v4_method_card (
  run_id text not null,
  card_id text not null,
  module_name text not null,
  report_title text not null,
  org text,
  report_date text,
  evidence_level text not null,
  method_summary text not null,
  required_data text not null,
  expected_model_change text not null,
  current_gap text not null,
  adoption_status text not null,
  primary key (run_id, card_id)
);

create table if not exists v4_gap_register (
  run_id text not null,
  gap_id text not null,
  module_name text not null,
  severity text not null,
  gap_title text not null,
  current_state text not null,
  required_state text not null,
  repair_action text not null,
  acceptance_gate text not null,
  status text not null,
  owner_agent text not null,
  primary key (run_id, gap_id)
);

create table if not exists v4_agent_review_cycle (
  run_id text not null,
  cycle_id text not null,
  phase text not null,
  agent_name text not null,
  finding text not null,
  repair_decision text not null,
  recheck_rule text not null,
  status text not null,
  created_at text not null,
  primary key (run_id, cycle_id, phase, agent_name)
);

create table if not exists v4_model_upgrade_spec (
  run_id text not null,
  model_name text not null,
  universe text not null,
  current_test_annual real,
  current_test_sharpe real,
  current_full_annual real,
  current_full_sharpe real,
  failure_mode text not null,
  upgrade_block text not null,
  new_features text not null,
  portfolio_constraints text not null,
  validation_rule text not null,
  status text not null,
  primary key (run_id, model_name, universe)
);

create table if not exists v4_strict_gate (
  run_id text not null,
  universe text not null,
  model_name text not null,
  test_annual real,
  test_sharpe real,
  full_annual real,
  full_sharpe real,
  pass_flag integer not null,
  issue text,
  primary key (run_id, universe, model_name)
);

create table if not exists v4_kline_skill_doc_index (
  run_id text not null,
  skill_id text not null,
  family text not null,
  pattern_name text not null,
  doc_path text not null,
  executable_flag integer not null,
  missing_logic text,
  primary key (run_id, skill_id)
);

create table if not exists v4_factor_agent_plan (
  run_id text not null,
  plan_id text not null,
  factor_family text not null,
  data_scope text not null,
  search_channel text not null,
  validation_stack text not null,
  stop_rule text not null,
  current_status text not null,
  primary key (run_id, plan_id)
);
