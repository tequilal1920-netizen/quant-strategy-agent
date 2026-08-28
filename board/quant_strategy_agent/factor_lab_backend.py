"""Login-protected Factor Laboratory API and isolated worker supervisor."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Blueprint, Flask, jsonify, request, session


API_VERSION = "factor-lab-api/2.9"
APP_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = APP_ROOT.parents[1]
ENGINE_PATH = Path(
    os.environ.get(
        "FACTOR_LAB_ENGINE",
        str(PROJECT_ROOT / "model" / "factor_laboratory" / "worker.py"),
    )
).resolve()
STATE_DB = Path(os.environ.get("FACTOR_LAB_STATE_DB", str(PROJECT_ROOT / "database" / "factor_lab_state.sqlite3"))).resolve()
RUN_ROOT = Path(os.environ.get("FACTOR_LAB_RUN_ROOT", str(PROJECT_ROOT / "output" / "factor_laboratory" / "runs"))).resolve()
RUN_ROOT.mkdir(parents=True, exist_ok=True)
CHAMPION_MANIFEST = Path(
    os.environ.get(
        "FACTOR_LAB_CHAMPION_MANIFEST",
        str(PROJECT_ROOT / "model" / "factor_laboratory" / "champion_manifest.json"),
    )
).resolve()
PROFESSIONAL_FRAMEWORK = Path(
    os.environ.get(
        "FACTOR_LAB_PROFESSIONAL_FRAMEWORK",
        str(PROJECT_ROOT / "model" / "factor_laboratory" / "professional_framework.json"),
    )
).resolve()

STATE_DB.parent.mkdir(parents=True, exist_ok=True)
PROCESS_LOCK = threading.RLock()
PROCESSES: dict[str, subprocess.Popen] = {}
MAX_CONCURRENT = max(1, int(os.environ.get("FACTOR_LAB_MAX_CONCURRENT", "1")))
RUN_SEMAPHORE = threading.BoundedSemaphore(MAX_CONCURRENT)
CATALOG_CACHE: dict[str, Any] = {"at": 0.0, "payload": None}
FACTOR_MODEL_ROOT = (PROJECT_ROOT / "model" / "factor_laboratory").resolve()
if str(FACTOR_MODEL_ROOT) not in sys.path:
    sys.path.insert(0, str(FACTOR_MODEL_ROOT))
try:
    from factor_catalog import build_factor_catalog
except Exception:
    build_factor_catalog = None
try:
    from environment.data_sources.factor_lab_vendor_data import audit_vendor_data_layer
except Exception:  # noqa: BLE001
    audit_vendor_data_layer = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def warehouse_path() -> Path:
    candidates = [
        os.environ.get("FACTOR_LAB_DB"),
        r"F:\apps\ai_quant_v2_public_8890\report\database\research_warehouse.db",
        str(PROJECT_ROOT / "database" / "research_warehouse.db"),
        str(APP_ROOT / "database" / "research_warehouse.db"),
    ]
    for raw in candidates:
        if raw and Path(raw).exists():
            return Path(raw).resolve()
    return Path(candidates[0] or candidates[2]).resolve()


def worker_python() -> Path:
    candidates = [
        os.environ.get("FACTOR_LAB_PYTHON"),
        r"D:\Download\Anaconda\python.exe",
        r"C:\ProgramData\anaconda3\python.exe",
        sys.executable,
    ]
    for raw in candidates:
        if raw and Path(raw).exists():
            return Path(raw).resolve()
    return Path(sys.executable)


@contextmanager
def state_conn():
    conn = sqlite3.connect(STATE_DB, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_state() -> None:
    with state_conn() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS factor_lab_run (
                run_id TEXT PRIMARY KEY,
                user_name TEXT NOT NULL,
                engine TEXT NOT NULL,
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                stage TEXT,
                progress REAL NOT NULL DEFAULT 0,
                message TEXT,
                config_hash TEXT NOT NULL,
                config_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                elapsed_seconds REAL,
                pid INTEGER,
                result_path TEXT,
                error TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_factor_lab_run_created ON factor_lab_run(created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_factor_lab_run_engine ON factor_lab_run(engine,status)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS factor_lab_audit (
                event_id TEXT PRIMARY KEY,
                run_id TEXT,
                action TEXT NOT NULL,
                actor TEXT NOT NULL,
                payload_json TEXT,
                created_at TEXT NOT NULL
            )
        """)
        # A process cannot survive a web-service restart as a supervised child.
        conn.execute("UPDATE factor_lab_run SET status='failed',stage='recovery',message='worker supervisor restarted',completed_at=? WHERE status IN ('queued','running','cancelling')", (now_iso(),))


def audit(run_id: str | None, action: str, payload: dict[str, Any] | None = None) -> None:
    actor = str(session.get("user") or "system")
    with state_conn() as conn:
        conn.execute(
            "INSERT INTO factor_lab_audit(event_id,run_id,action,actor,payload_json,created_at) VALUES(?,?,?,?,?,?)",
            (uuid.uuid4().hex, run_id, action, actor, json.dumps(payload or {}, ensure_ascii=False, sort_keys=True), now_iso()),
        )


MODEL_PRESETS: dict[str, dict[str, Any]] = {
    "lstm": {
        "label": "因果混合残差 LSTM",
        "architecture": [
            "五域变量选择门控与缺失年龄编码",
            "3/5/9核多尺度因果深度卷积",
            "三层 projected LSTM 状态空间",
            "三层因果多头注意力",
            "六专家市场状态 MoE 路由",
            "5/10/20日异方差概率与分位数预测头",
            "横截面Rank+Huber+NLL+符号+换手+暴露+路由均衡复合损失",
            "训练期时间裁剪、特征dropout、输入噪声，验证/测试不扰动",
            "验证均值+最弱时间折+正IC占比-波动-训练验证落差的稳健早停",
            "净化嵌套搜索、successive halving、五种子深度集成",
        ],
        "defaults": {
            "sequence_length": 180, "hidden_dim": 128, "lstm_layers": 2,
            "attention_layers": 2, "heads": 4, "experts": 4, "dropout": .22,
            "learning_rate": 0.00025, "weight_decay": 0.0002, "grad_clip": 0.8,
            "epochs": 20, "ensemble_seeds": 5, "patience": 5, "min_delta": 0.0005,
            "feature_dropout": 0.08, "input_noise": 0.015, "min_sequence_fraction": 0.78,
            "selection_folds": 4, "train_valid_gap_penalty": 0.60,
            "search": {"method": "purged_successive_halving", "trials": 16, "trial_epochs": 3},
        },
    },
    "gru": {
        "label": "因果混合残差 GRU",
        "architecture": [
            "五域变量选择门控与缺失年龄编码",
            "3/5/9核多尺度因果深度卷积",
            "三层 projected GRU 状态空间",
            "三层因果多头注意力",
            "六专家市场状态 MoE 路由",
            "5/10/20日异方差概率与分位数预测头",
            "横截面Rank+Huber+NLL+符号+换手+暴露+路由均衡复合损失",
            "训练期时间裁剪、特征dropout、输入噪声，验证/测试不扰动",
            "验证均值+最弱时间折+正IC占比-波动-训练验证落差的稳健早停",
            "净化嵌套搜索、successive halving、七种子深度集成",
        ],
        "defaults": {
            "recurrent_cell": "gru", "sequence_length": 120,
            "hidden_dim": 96, "gru_layers": 2,
            "attention_layers": 2, "heads": 4, "experts": 4, "dropout": .24,
            "learning_rate": 0.00035, "weight_decay": 0.0002, "grad_clip": 0.8,
            "epochs": 22, "ensemble_seeds": 7, "patience": 5, "min_delta": 0.0005,
            "feature_dropout": 0.10, "input_noise": 0.012, "min_sequence_fraction": 0.80,
            "selection_folds": 4, "train_valid_gap_penalty": 0.65,
            "search": {"method": "purged_successive_halving", "trials": 18, "trial_epochs": 3},
        },
    },
    "rl_transformer": {
        "label": "语法约束协同 RL+Transformer",
        "architecture": [
            "后缀 AST 公式环境与栈类型系统",
            "四层稳健 causal Transformer actor + critic value head",
            "字段/单位/栈深/算子/窗口硬动作掩码",
            "PPO clipped objective + GAE + KL/熵正则",
            "训练/验证最弱折、残差IC、净Sharpe、换手、冗余、复杂度联合奖励",
            "候选公式增加训练-验证落差、验证换手、验证回撤硬门控",
            "35%→100%多保真 successive halving",
            "复杂度×因子域质量多样性 archive",
            "搜索期严格隔离测试集，最终一次性报告",
        ],
        "defaults": {
            "d_model": 192, "layers": 4, "heads": 8, "dropout": .18,
            "max_formula_tokens": 20, "episodes": 2048, "rollout_batch": 64,
            "ppo_epochs": 4, "ppo_clip": .20, "gamma": .99, "gae_lambda": .95,
            "entropy": .018, "value_coef": .5, "learning_rate": .00018, "weight_decay": .0002,
            "reward_stability_folds": 4, "max_formula_complexity_penalty": 0.010,
            "min_valid_rank_ic": 0.0, "min_valid_sharpe": 0.0,
            "max_train_valid_ic_gap": 0.08, "max_valid_turnover": 0.80, "max_valid_drawdown": 0.30,
        },
    },
    "strategy": {
        "label": "等权 / RankIC / OLS / Lasso / Ridge / MLP 打分回测",
        "architecture": [
            "旧版21因子OLS、全29因子OLS并行",
            "Lasso稀疏筛选、经济域Ridge、横截面Ridge与ElasticNet",
            "256-128-64深层MLP非线性打分",
            "自适应ICIR、OLS/ICIR固定秩集成",
            "Top10%、连续排名、缓冲换仓、成本感知、逆波动、可靠性调仓",
        ],
        "defaults": {
            "lasso_alpha": .00002,
            "epochs": 30,
            "max_training_samples": 300000,
            "factor_universe_mode": "screened_full",
            "max_factor_candidates": 180,
            "factor_screen_top_n": 60,
            "factor_screen_lookback_days": 252,
            "factor_screen_rebalance_days": 63,
            "factor_screen_min_coverage": 0.35,
            "factor_screen_min_dates": 20,
            "factor_screen_min_assets_per_date": 30,
            "factor_screen_max_pair_corr": 0.92,
            "external_factor_max_staleness_days": 63,
            "selection_turnover_budget": 0.65,
            "selection_prefer_best_development": True,
            "include_subject_parquet": False,
        },
    },
    "joint_test": {
        "label": "单因子与多因子联合检验",
        "architecture": ["RankIC/ICIR", "分组成本后收益", "相关与冗余", "样本外衰减", "DSR/PBO台账", "十项晋升闸门"],
        "defaults": {},
    },
}


MODE_CAPS = {
    "smoke": {"max_assets": 80, "max_months": 18, "sequence_length": 60, "epochs": 1, "ensemble_seeds": 1, "search_trials": 1, "trial_epochs": 1, "episodes": 12, "rollout_batch": 4, "timeout": 1200},
    "research": {"max_assets": 300, "max_months": 96, "sequence_length": 252, "epochs": 24, "ensemble_seeds": 7, "search_trials": 16, "trial_epochs": 5, "episodes": 1024, "rollout_batch": 96, "timeout": 14400},
    "production": {"max_assets": 800, "max_months": 180, "sequence_length": 504, "epochs": 60, "ensemble_seeds": 12, "search_trials": 48, "trial_epochs": 12, "episodes": 8192, "rollout_batch": 256, "timeout": 43200},
}


def clamp(value: Any, minimum: int | float, maximum: int | float, cast=int):
    try:
        return cast(max(minimum, min(maximum, cast(value))))
    except (TypeError, ValueError):
        return cast(minimum)


def normalized_config(payload: dict[str, Any]) -> dict[str, Any]:
    engine = str(payload.get("engine") or "lstm")
    if engine not in MODEL_PRESETS:
        raise ValueError("unsupported engine")
    mode = str(payload.get("mode") or "research")
    if mode not in MODE_CAPS:
        raise ValueError("unsupported mode")
    caps = MODE_CAPS[mode]
    defaults = json.loads(json.dumps(MODEL_PRESETS[engine]["defaults"]))
    defaults.update({k: v for k, v in payload.items() if k not in {"search"}})
    if isinstance(payload.get("search"), dict):
        defaults.setdefault("search", {}).update(payload["search"])
    if engine == "gru":
        defaults["recurrent_cell"] = "gru"
        defaults["gru_layers"] = clamp(payload.get("gru_layers", payload.get("lstm_layers", defaults.get("gru_layers", 3))), 2, 5)
    elif engine == "lstm":
        defaults["recurrent_cell"] = "lstm"
    defaults.update({
        "engine": engine, "mode": mode, "database_path": str(warehouse_path()),
        "max_assets": clamp(payload.get("max_assets", 240), 40, caps["max_assets"]),
        "max_months": clamp(payload.get("max_months", 72), 12, caps["max_months"]),
        "sequence_length": clamp(payload.get("sequence_length", defaults.get("sequence_length", 120)), 40, caps["sequence_length"]),
        "epochs": clamp(payload.get("epochs", defaults.get("epochs", 12)), 1, caps["epochs"]),
        "ensemble_seeds": clamp(payload.get("ensemble_seeds", defaults.get("ensemble_seeds", 3)), 1, caps["ensemble_seeds"]),
        "episodes": clamp(payload.get("episodes", defaults.get("episodes", 256)), 8, caps["episodes"]),
        "rollout_batch": clamp(payload.get("rollout_batch", defaults.get("rollout_batch", 32)), 4, caps["rollout_batch"]),
        "horizons": sorted({clamp(x, 1, 60) for x in payload.get("horizons", [5, 10, 20])})[:5],
        "cost_bps": clamp(payload.get("cost_bps", 15), 0, 200, float),
        "seed": clamp(payload.get("seed", 20260720), 1, 2_147_483_647),
        "cpu_threads": clamp(payload.get("cpu_threads", 4), 1, 16),
        "timeout_seconds": caps["timeout"], "allow_cuda": bool(payload.get("allow_cuda", True)),
        "universe": str(payload.get("universe") or "ALL_A")[:32],
        "frequency": str(payload.get("frequency") or "daily")[:16],
        "risk_profile": str(payload.get("risk_profile") or "balanced")[:24],
    })
    if engine == "strategy":
        mode_value = str(defaults.get("factor_universe_mode") or "screened_full").strip().lower()
        defaults["factor_universe_mode"] = mode_value if mode_value in {"core_29", "screened_full", "warehouse_screened"} else "screened_full"
        defaults["max_factor_candidates"] = clamp(defaults.get("max_factor_candidates", 180), 1, 600)
        defaults["factor_screen_top_n"] = clamp(defaults.get("factor_screen_top_n", 60), 0, 240)
        defaults["factor_screen_lookback_days"] = clamp(defaults.get("factor_screen_lookback_days", 252), 40, 756)
        defaults["factor_screen_rebalance_days"] = clamp(defaults.get("factor_screen_rebalance_days", 63), 20, 252)
        defaults["factor_screen_min_coverage"] = clamp(defaults.get("factor_screen_min_coverage", 0.35), 0.01, 0.99, float)
        defaults["factor_screen_min_dates"] = clamp(defaults.get("factor_screen_min_dates", 20), 5, 252)
        defaults["factor_screen_min_assets_per_date"] = clamp(defaults.get("factor_screen_min_assets_per_date", 30), 5, 500)
        defaults["factor_screen_max_pair_corr"] = clamp(defaults.get("factor_screen_max_pair_corr", 0.92), 0.50, 0.999, float)
        defaults["external_factor_max_staleness_days"] = clamp(defaults.get("external_factor_max_staleness_days", 63), 0, 252)
        defaults["selection_turnover_budget"] = clamp(defaults.get("selection_turnover_budget", 0.65), 0.05, 2.0, float)
        defaults["selection_prefer_best_development"] = bool(defaults.get("selection_prefer_best_development", True))
        defaults["include_subject_parquet"] = bool(defaults.get("include_subject_parquet", False))
    defaults.setdefault("search", {})
    defaults["search"]["trials"] = clamp(defaults["search"].get("trials", 6), 1, caps["search_trials"])
    defaults["search"]["trial_epochs"] = clamp(defaults["search"].get("trial_epochs", 2), 1, caps["trial_epochs"])
    return defaults


def run_dict(row: sqlite3.Row, include_result: bool = False) -> dict[str, Any]:
    item = dict(row)
    item["config"] = json.loads(item.pop("config_json") or "{}")
    result_path = Path(item.get("result_path") or "")
    item["result_available"] = result_path.exists()
    if include_result and result_path.exists():
        try:
            item["result"] = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            item["result_error"] = str(exc)
    return item


def read_progress(run_id: str, progress_path: Path) -> None:
    try:
        payload = json.loads(progress_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    with state_conn() as conn:
        conn.execute("UPDATE factor_lab_run SET stage=?,progress=?,message=? WHERE run_id=?", (payload.get("stage"), float(payload.get("progress") or 0), payload.get("message"), run_id))


def supervise(run_id: str, config: dict[str, Any]) -> None:
    run_dir = RUN_ROOT / run_id
    config_path, result_path = run_dir / "config.json", run_dir / "result.json"
    progress_path, stdout_path, stderr_path = run_dir / "progress.json", run_dir / "stdout.log", run_dir / "stderr.log"
    with RUN_SEMAPHORE:
        with state_conn() as conn:
            status = conn.execute("SELECT status FROM factor_lab_run WHERE run_id=?", (run_id,)).fetchone()
            if not status or status[0] == "cancelled":
                return
            conn.execute("UPDATE factor_lab_run SET status='running',stage='initializing',started_at=?,message='worker starting' WHERE run_id=?", (now_iso(), run_id))
        command = [str(worker_python()), str(ENGINE_PATH), "--config", str(config_path), "--output", str(result_path), "--progress", str(progress_path)]
        started = time.time()
        try:
            with stdout_path.open("w", encoding="utf-8") as out, stderr_path.open("w", encoding="utf-8") as err:
                proc = subprocess.Popen(command, cwd=APP_ROOT, stdout=out, stderr=err, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                with PROCESS_LOCK:
                    PROCESSES[run_id] = proc
                with state_conn() as conn:
                    conn.execute("UPDATE factor_lab_run SET pid=? WHERE run_id=?", (proc.pid, run_id))
                deadline = started + int(config.get("timeout_seconds", 14400))
                while proc.poll() is None:
                    read_progress(run_id, progress_path)
                    if time.time() > deadline:
                        proc.terminate(); raise TimeoutError("factor laboratory worker timeout")
                    time.sleep(1.0)
                read_progress(run_id, progress_path)
                return_code = proc.returncode
            payload = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
            status = "completed" if return_code == 0 and payload.get("status") == "completed" else "failed"
            message = "研究任务完成" if status == "completed" else str(payload.get("message") or f"worker exited {return_code}")
            with state_conn() as conn:
                conn.execute("UPDATE factor_lab_run SET status=?,stage=?,progress=1,message=?,completed_at=?,elapsed_seconds=?,error=? WHERE run_id=?", (status, status, message, now_iso(), round(time.time() - started, 3), None if status == "completed" else message, run_id))
        except Exception as exc:  # noqa: BLE001
            with state_conn() as conn:
                conn.execute("UPDATE factor_lab_run SET status='failed',stage='failed',progress=1,message=?,completed_at=?,elapsed_seconds=?,error=? WHERE run_id=?", (str(exc), now_iso(), round(time.time() - started, 3), str(exc), run_id))
        finally:
            with PROCESS_LOCK:
                PROCESSES.pop(run_id, None)


def create_run(payload: dict[str, Any]) -> dict[str, Any]:
    config = normalized_config(payload)
    serialized = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    config_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    run_id = f"fl_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    run_dir = RUN_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    with state_conn() as conn:
        conn.execute(
            "INSERT INTO factor_lab_run(run_id,user_name,engine,mode,status,stage,progress,message,config_hash,config_json,created_at,result_path) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, str(session.get("user") or "unknown"), config["engine"], config["mode"], "queued", "queued", 0.0, "等待独立训练进程", config_hash, serialized, now_iso(), str(run_dir / "result.json")),
        )
    audit(run_id, "create_run", {"engine": config["engine"], "mode": config["mode"], "config_hash": config_hash})
    thread = threading.Thread(target=supervise, args=(run_id, config), daemon=True, name=f"factor-lab-{run_id}")
    thread.start()
    with state_conn() as conn:
        row = conn.execute("SELECT * FROM factor_lab_run WHERE run_id=?", (run_id,)).fetchone()
    return run_dict(row)


def latest_factor_evaluations(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Return one auditable formal record per factor without metric splicing."""

    rows = conn.execute(
        """
        with ranked as (
          select
            factor_name,
            rank_ic,
            icir,
            coverage,
            pass_flag,
            run_id as evaluation_run_id,
            universe as evaluation_universe,
            split_name as evaluation_split,
            row_number() over (
              partition by factor_name
              order by
                case when universe = 'ALL_A' then 0 else 1 end,
                case split_name when 'full' then 0 when 'test' then 1
                     when 'valid' then 2 when 'train' then 3 else 4 end,
                run_id desc
            ) as record_rank
          from factor_test_result
          where split_name in ('full','test','valid','train')
        )
        select factor_name, rank_ic, icir, coverage, pass_flag,
               evaluation_run_id, evaluation_universe, evaluation_split
        from ranked
        where record_rank = 1
        """
    )
    return {row["factor_name"]: dict(row) for row in rows}


def catalog_payload(force: bool = False) -> dict[str, Any]:
    if not force and CATALOG_CACHE.get("payload") and time.time() - float(CATALOG_CACHE.get("at") or 0) < 300:
        return CATALOG_CACHE["payload"]
    path = warehouse_path()
    if build_factor_catalog is not None:
        try:
            base = build_factor_catalog(path if path.exists() else None)
        except Exception as exc:  # noqa: BLE001
            base = {
                "status": "blocked",
                "message": str(exc),
                "families": [],
                "factors": [],
                "registered_factor_count": 0,
                "explicit_factor_entry_count": 0,
                "materialized_factor_count": 0,
                "current_model_feature_count": 0,
            }
    else:
        base = {
            "status": "blocked",
            "message": "factor_catalog_module_unavailable",
            "families": [],
            "factors": [],
            "registered_factor_count": 0,
            "explicit_factor_entry_count": 0,
            "materialized_factor_count": 0,
            "current_model_feature_count": 0,
        }
    base["database_available"] = path.exists()
    base.setdefault("watermark", None)
    if path.exists():
        try:
            conn = sqlite3.connect("file:" + path.as_posix() + "?mode=ro", uri=True, timeout=20)
            conn.row_factory = sqlite3.Row
            try:
                base["watermark"] = conn.execute("SELECT MAX(trade_date) FROM stock_ohlcv_daily").fetchone()[0]
            except sqlite3.Error:
                base["watermark"] = None
            tests = latest_factor_evaluations(conn)
            conn.close()
            for factor in base.get("factors", []):
                factor.update(tests.get(factor.get("factor_name"), {}))
        except sqlite3.Error as exc:
            base.update({"status": "blocked", "message": str(exc)})
    with state_conn() as conn:
        counts = {row[0]: row[1] for row in conn.execute("SELECT engine,COUNT(*) FROM factor_lab_run WHERE status='completed' GROUP BY engine")}
    base["completed_model_runs"] = counts
    base["model_catalog"] = [
        {"id": key, "label": value.get("label", key), "completed_runs": int(counts.get(key, 0))}
        for key, value in MODEL_PRESETS.items()
    ]
    family_counts = {row.get("id"): int(row.get("count") or 0) for row in base.get("families", [])}
    base["standard_factor_count"] = sum(family_counts.get(key, 0) for key in ("technical", "money", "fundamental", "valuation", "macro"))
    base["discovered_factor_count"] = sum(family_counts.get(key, 0) for key in ("llm_mined", "deep_mined", "discovered", "warehouse_dynamic"))
    base["status"] = "ok" if base.get("status") != "blocked" else "blocked"
    CATALOG_CACHE.update({"at": time.time(), "payload": base})
    return base

def champion_payload() -> dict[str, Any]:
    """Load the compact, audited strategy champion contract for the UI."""
    unavailable = {
        "status": "unavailable",
        "engine_version": "factor-lab/3.2-inverse-volatility-rank-execution",
        "message": "validated_champion_manifest_unavailable",
    }
    try:
        if not CHAMPION_MANIFEST.exists() or CHAMPION_MANIFEST.stat().st_size > 128_000:
            return unavailable
        payload = json.loads(CHAMPION_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return unavailable
    required = {
        "schema_version",
        "engine_version",
        "selected_candidate",
        "selection_basis",
        "test_usage",
        "candidate_count",
        "splits",
        "gates",
        "candidate_diagnostics",
    }
    if not isinstance(payload, dict) or not required.issubset(payload):
        return unavailable
    if payload.get("selection_basis") != "train_and_validation_only":
        return unavailable
    if payload.get("test_usage") != "report_only":
        return unavailable
    if not isinstance(payload.get("splits"), list) or {
        row.get("split") for row in payload["splits"] if isinstance(row, dict)
    } != {"train", "valid", "test"}:
        return unavailable
    if not isinstance(payload.get("gates"), list) or not payload["gates"]:
        return unavailable
    if not isinstance(payload.get("candidate_diagnostics"), list):
        return unavailable
    payload = dict(payload)
    payload["status"] = "ok"
    payload["gate_summary"] = {
        "passed": sum(bool(row.get("passed")) for row in payload["gates"]),
        "total": len(payload["gates"]),
        "all_passed": all(bool(row.get("passed")) for row in payload["gates"]),
    }
    return payload


def professional_framework_payload(champion: dict[str, Any] | None = None) -> dict[str, Any]:
    """Load the professional Factor Lab framework shared by UI and Skill."""
    unavailable = {
        "status": "unavailable",
        "version": "r35.9-professional-factor-lab-framework",
        "message": "professional_framework_artifact_unavailable",
    }
    try:
        if not PROFESSIONAL_FRAMEWORK.exists() or PROFESSIONAL_FRAMEWORK.stat().st_size > 512_000:
            return unavailable
        payload = json.loads(PROFESSIONAL_FRAMEWORK.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return unavailable
    if not isinstance(payload, dict):
        return unavailable
    payload = dict(payload)
    payload.setdefault("status", "ok")
    payload["artifact"] = "model/factor_laboratory/professional_framework.json"
    if champion and champion.get("status") == "ok":
        effect = dict(payload.get("current_effect_contract") or {})
        effect.update({
            "default_champion": champion.get("selected_candidate"),
            "selection_basis": champion.get("selection_basis"),
            "test_usage": champion.get("test_usage"),
            "splits": champion.get("splits"),
            "gate_summary": champion.get("gate_summary"),
        })
        payload["current_effect_contract"] = effect
    return payload
def bootstrap_payload() -> dict[str, Any]:
    path, python = warehouse_path(), worker_python()
    champion = champion_payload()
    framework = professional_framework_payload(champion)
    return {
        "status": "ok" if path.exists() and ENGINE_PATH.exists() and python.exists() else "blocked",
        "api_version": API_VERSION,
        "engine_version": champion.get("engine_version", "factor-lab/3.2-inverse-volatility-rank-execution"),
        "data": {
            "database_available": path.exists(),
            "database_hint": "server-side research warehouse",
            "watermark": catalog_payload().get("watermark"),
            "point_in_time": True,
            "vendor_data_layer": audit_vendor_data_layer(PROJECT_ROOT) if audit_vendor_data_layer else {"status": "unavailable"},
        },
        "worker": {"python_available": python.exists(), "isolated_process": True, "max_concurrent": MAX_CONCURRENT},
        "champion": champion,
        "professional_framework": framework,
        "models": MODEL_PRESETS, "mode_caps": MODE_CAPS,
        "pages": [
            {"id": "dashboard", "label": "01 因子看板"},
            {"id": "mining", "label": "02 LLM因子挖掘"},
            {"id": "strategy", "label": "03 模型层"},
            {"id": "testing", "label": "04 联合检验"},
            {"id": "history", "label": "05 历史记录"},
        ],
        "policies": {"split": "60/20/20 chronological + max-horizon embargo", "test": "report-only once", "cost_bps": 15, "gates": 10, "credentials_in_worker": False},
    }


def register_factor_lab(app: Flask) -> None:
    init_state()
    bp = Blueprint("factor_lab", __name__)

    @bp.get("/api/factor-lab/health")
    def health():
        payload = bootstrap_payload()
        return jsonify(payload), (200 if payload["status"] == "ok" else 503)

    @bp.get("/api/factor-lab/bootstrap")
    def bootstrap():
        return jsonify(bootstrap_payload())

    @bp.get("/api/factor-lab/professional-framework")
    def professional_framework():
        return jsonify(professional_framework_payload(champion_payload()))

    @bp.get("/api/factor-lab/catalog")
    def catalog():
        return jsonify(catalog_payload(force=request.args.get("refresh") == "1"))

    @bp.get("/api/factor-lab/runs")
    def list_runs():
        limit = clamp(request.args.get("limit", 80), 1, 300)
        engine = str(request.args.get("engine") or "")
        status = str(request.args.get("status") or "")
        where, params = ["user_name=?"], [str(session.get("user") or "unknown")]
        if engine in MODEL_PRESETS: where.append("engine=?"); params.append(engine)
        if status in {"queued", "running", "completed", "failed", "cancelled", "cancelling"}: where.append("status=?"); params.append(status)
        with state_conn() as conn:
            rows = conn.execute(f"SELECT * FROM factor_lab_run WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT ?", (*params, limit)).fetchall()
        return jsonify({"status": "ok", "runs": [run_dict(row) for row in rows]})

    @bp.post("/api/factor-lab/runs")
    def start_run():
        payload = request.get_json(silent=True) or {}
        try:
            item = create_run(payload)
            return jsonify(item), 202
        except (ValueError, OSError) as exc:
            return jsonify({"status": "failed", "message": str(exc)}), 400

    @bp.get("/api/factor-lab/runs/<run_id>")
    def get_run(run_id: str):
        with state_conn() as conn:
            row = conn.execute("SELECT * FROM factor_lab_run WHERE run_id=? AND user_name=?", (run_id, str(session.get("user") or "unknown"))).fetchone()
        if not row:
            return jsonify({"status": "failed", "message": "run_not_found"}), 404
        return jsonify(run_dict(row, include_result=True))

    @bp.post("/api/factor-lab/runs/<run_id>/cancel")
    def cancel_run(run_id: str):
        with state_conn() as conn:
            row = conn.execute("SELECT status FROM factor_lab_run WHERE run_id=? AND user_name=?", (run_id, str(session.get("user") or "unknown"))).fetchone()
            if not row: return jsonify({"status": "failed", "message": "run_not_found"}), 404
            if row[0] in {"completed", "failed", "cancelled"}: return jsonify({"status": row[0], "run_id": run_id})
            conn.execute("UPDATE factor_lab_run SET status='cancelling',stage='cancelling',message='正在终止独立训练进程' WHERE run_id=?", (run_id,))
        with PROCESS_LOCK:
            proc = PROCESSES.get(run_id)
            if proc and proc.poll() is None: proc.terminate()
        with state_conn() as conn:
            conn.execute("UPDATE factor_lab_run SET status='cancelled',stage='cancelled',progress=1,message='用户取消',completed_at=? WHERE run_id=?", (now_iso(), run_id))
        audit(run_id, "cancel_run")
        return jsonify({"status": "cancelled", "run_id": run_id})

    @bp.get("/api/factor-lab/dashboard")
    def dashboard():
        selected = str(request.args.get("run_id") or "")
        with state_conn() as conn:
            if selected:
                row = conn.execute("SELECT * FROM factor_lab_run WHERE run_id=? AND user_name=?", (selected, str(session.get("user") or "unknown"))).fetchone()
            else:
                row = conn.execute("SELECT * FROM factor_lab_run WHERE status='completed' AND user_name=? ORDER BY completed_at DESC LIMIT 1", (str(session.get("user") or "unknown"),)).fetchone()
        return jsonify({"status": "ok", "catalog": catalog_payload(), "selected_run": run_dict(row, include_result=True) if row else None})

    @bp.post("/api/factor-lab/formula/validate")
    def validate_formula():
        formula = str((request.get_json(silent=True) or {}).get("formula") or "").strip()
        tokens = formula.split()
        allowed = set(["NEG", "ABS", "SLOG", "CS_RANK", "TS_Z20", "DELTA5", "DECAY10", "ADD", "SUB", "MUL", "DIV"])
        allowed.update(["ret_1", "ret_5", "ret_20", "ret_60", "vol_20", "down_vol_20", "price_pos_60", "volume_z_20", "amihud_20", "turnover", "volume_ratio", "value_ep", "value_bp", "value_sp", "dividend", "log_mv", "moneyflow", "large_flow", "extreme_flow", "range_1", "gap_1"])
        invalid = [x for x in tokens if x not in allowed]
        stack = 0
        for token in tokens:
            if token in invalid: continue
            if token in {"NEG", "ABS", "SLOG", "CS_RANK", "TS_Z20", "DELTA5", "DECAY10"}:
                if stack < 1: invalid.append(token); break
            elif token in {"ADD", "SUB", "MUL", "DIV"}:
                if stack < 2: invalid.append(token); break
                stack -= 1
            else: stack += 1
        valid = not invalid and stack == 1 and 0 < len(tokens) <= 32
        return jsonify({"status": "ok", "valid": valid, "tokens": tokens, "invalid_tokens": invalid, "stack_depth": stack, "formula_hash": hashlib.sha256(formula.encode()).hexdigest() if valid else None})

    app.register_blueprint(bp)
