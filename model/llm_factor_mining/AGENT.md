# 05 Factor Mining Agent

Purpose: systematically mine new A-share alpha factors with an AI-methodology loop.

The agent learns AI factor-mining methodology from local method cards and report plans, then runs a real search/evaluate/mutate loop. It is not a broker-report factor reproducer.

## Inputs

- SQLite warehouse: `database/research_warehouse.db`
- Universes: `ALL_A`, `CSI800_ENH`, `CSI2000_ENH`
- Date window: default `20120101` to `20260630`
- Optional recursive CSV/parquet feature path
- Methodology tables: `v4_method_card`, `v4_factor_agent_plan`

## Search Channels

- LLM hypothesis generation, currently a strict local structured fallback until the user provides the API adapter.
- GPT 5.5 xhigh through an OpenAI-compatible AI Router adapter when `AI_ROUTER_API_KEY` is present.
- MCTS expression-tree search.
- Genetic crossover and mutation.
- OpenFE-style feature interaction search.
- Deep representation search through rolling cross-sectional SVD.
- Failure-memory mutation.

## Data Spaces

- Price/volume windows from `stock_ohlcv_daily`
- Valuation/crowding from `stock_valuation_daily`
- Moneyflow from `stock_moneyflow_daily`
- Point-in-time fundamentals from `financial_report_visible.visible_date`
- Event/text counts from `news_event_daily`
- K-line context from `kline_feature_daily`
- Industry neutralization from `sw_l1_industry_daily`
- Universe membership from `index_constituent_period`

## Validation Stack

- Static audit: missing fields, low coverage, label leakage, expression complexity.
- Preprocessing: cross-sectional winsorization, z-score, size and industry neutralization.
- Single-factor test: RankIC, ICIR, t value, win rate, group spread, monotonicity, turnover, coverage and yearly splits.
- Portfolio test: top-quantile long-only, top-bottom long-short, transaction-cost adjustment, annual return, Sharpe, drawdown and information ratio.
- Reward and judge: valid/test IC, group spread, monotonicity, cost-adjusted Sharpe and coverage minus redundancy, turnover, complexity and train-test decay penalties.

## Outputs

- `factor_test_result`
- `factor_value_daily`
- `v3_factor_candidate_registry`
- `v3_factor_validation`
- `output/llm_factor_mining/factor_mining_{universe}.json`
- `output/llm_factor_mining/factor_leaderboard_{universe}.csv`
- `output/llm_factor_mining/methodology_cards_{universe}.json`

## Rules

- Future return is used only as the label.
- Financial statements must use `visible_date`.
- LLM/API layer must never see future labels or raw future returns.
- API keys, financial tokens, account passwords and cookies are read only from environment/runtime context and are never persisted to this folder.
- iFind/Wind/CSMAR/CNKI/PPM are quota or login-sensitive sources; bulk extraction is disabled unless explicitly enabled by runtime flags.
- Exact universe membership is required; no proxy universe is used.
- A factor is production-ready only after train/valid/test/full gates, redundancy audit and cost-adjusted portfolio checks.
