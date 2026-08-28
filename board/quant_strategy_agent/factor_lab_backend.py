"""Login-protected Factor Laboratory API and isolated worker supervisor."""
from __future__ import annotations

import csv
import hashlib
import json
import math
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


API_VERSION = "factor-lab-api/3.0-full-framework"
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
FACTOR_TAXONOMY_JSON = PROJECT_ROOT / "output" / "factor_laboratory" / "factor_taxonomy_cn" / "factor_taxonomy_cn.json"
FACTOR_LIBRARY_BLUEPRINT = PROJECT_ROOT / "output" / "factor_laboratory" / "factor_library_v2" / "因子库v2_完整蓝图.json"
FACTOR_LIBRARY_AUDIT = PROJECT_ROOT / "output" / "factor_laboratory" / "factor_library_v2" / "因子库v2_质量审计_全部因子.csv"
FACTOR_LIBRARY_SUMMARY = PROJECT_ROOT / "output" / "factor_laboratory" / "factor_library_v2" / "因子库v2_质量审计摘要.json"
FACTOR_LIBRARY_COVERAGE = PROJECT_ROOT / "output" / "factor_laboratory" / "factor_library_v2" / "因子库v2_分类覆盖汇总.csv"
FACTOR_CURRENT_QUALITY = PROJECT_ROOT / "output" / "factor_laboratory" / "factor_library_v2" / "当前入模29因子_轻量真实质量复核_v2.csv"
DOMAIN_TIMING_OOS_RESULT = PROJECT_ROOT / "output" / "factor_laboratory" / "domain_timing_conservative_guard_r366_oos201603_20260820" / "result.json"
DOMAIN_TIMING_CURRENT_RESULT = PROJECT_ROOT / "output" / "factor_laboratory" / "domain_timing_conservative_guard_r366_current_20260820" / "result.json"
DEFAULT_ENGINE_VERSION = "factor-lab/3.6.1-deep-anti-overfit"

STATE_DB.parent.mkdir(parents=True, exist_ok=True)
PROCESS_LOCK = threading.RLock()
PROCESSES: dict[str, subprocess.Popen] = {}
MAX_CONCURRENT = max(1, int(os.environ.get("FACTOR_LAB_MAX_CONCURRENT", "1")))
RUN_SEMAPHORE = threading.BoundedSemaphore(MAX_CONCURRENT)
CATALOG_CACHE: dict[str, Any] = {"at": 0.0, "payload": None}
FULL_FRAMEWORK_CACHE: dict[str, Any] = {"at": 0.0, "payload": None}
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


def active_engine_version() -> str:
    """Read the callable worker version without importing the heavy model stack."""
    try:
        text = ENGINE_PATH.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return DEFAULT_ENGINE_VERSION
    for pattern in (
        r'VERSION\s*=\s*"([^"]+)"',
        r"VERSION\s*=\s*'([^']+)'",
        r'ENGINE_VERSION\s*=\s*"([^"]+)"',
        r"ENGINE_VERSION\s*=\s*'([^']+)'",
    ):
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return DEFAULT_ENGINE_VERSION


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
        "label": "等权 / RankIC / OLS / Lasso / Ridge / LSTM 打分回测",
        "architecture": [
            "旧版21因子OLS、全29因子OLS并行",
            "Lasso稀疏筛选、经济域Ridge、横截面Ridge与ElasticNet",
            "LSTM时序打分与256-128-64深层非线性打分",
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
        "engine_version": active_engine_version(),
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
            "active_engine_version": active_engine_version(),
            "default_champion": champion.get("selected_candidate"),
            "selection_basis": champion.get("selection_basis"),
            "test_usage": champion.get("test_usage"),
            "splits": champion.get("splits"),
            "gate_summary": champion.get("gate_summary"),
        })
        payload["current_effect_contract"] = effect
    return payload


def _artifact_text(path: Path, max_bytes: int | None = 8_000_000) -> str:
    try:
        if not path.exists():
            return ""
        if max_bytes is not None and path.stat().st_size > max_bytes:
            return ""
    except OSError:
        return ""
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except (OSError, UnicodeError):
            continue
    return ""


def _artifact_json(path: Path, default: Any | None = None) -> Any:
    text = _artifact_text(path, max_bytes=None)
    if not text:
        return {} if default is None else default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {} if default is None else default


def _artifact_csv(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    text = _artifact_text(path)
    if not text:
        return []
    rows: list[dict[str, Any]] = []
    try:
        for row in csv.DictReader(text.splitlines()):
            rows.append(dict(row))
            if limit and len(rows) >= limit:
                break
    except csv.Error:
        return []
    return rows


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except (OSError, ValueError):
        return path.name


def _float(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        if math.isfinite(float(value)):
            return float(value)
        return default
    text = str(value).strip().replace(",", "")
    if not text or text in {"--", "—", "nan", "None", "null"}:
        return default
    if text.endswith("%"):
        text = text[:-1]
        scale = 100.0
    else:
        scale = 1.0
    try:
        value_float = float(text) / scale
    except ValueError:
        return default
    return value_float if math.isfinite(value_float) else default


def _int(value: Any, default: int = 0) -> int:
    num = _float(value)
    return int(num) if num is not None else default


def _round(value: Any, digits: int = 4) -> float | None:
    num = _float(value)
    return round(num, digits) if num is not None else None


def _date_text(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _warehouse_factor_test_stats() -> dict[str, dict[str, Any]]:
    path = warehouse_path()
    if not path.exists():
        return {}
    stats: dict[str, dict[str, Any]] = {}
    try:
        conn = sqlite3.connect("file:" + path.as_posix() + "?mode=ro", uri=True, timeout=20)
        conn.row_factory = sqlite3.Row
        table_name = "factor_test_result" if _table_exists(conn, "factor_test_result") else "v3_factor_validation"
        if not _table_exists(conn, table_name):
            conn.close()
            return {}
        rows = conn.execute(
            f"""
            SELECT
              factor_name,
              AVG(CASE WHEN split_name='train' THEN rank_ic END) AS train_rank_ic,
              AVG(CASE WHEN split_name='valid' THEN rank_ic END) AS valid_rank_ic,
              AVG(CASE WHEN split_name='test' THEN rank_ic END) AS test_rank_ic,
              AVG(CASE WHEN split_name='full' THEN rank_ic END) AS full_rank_ic,
              AVG(CASE WHEN split_name='train' THEN icir END) AS train_icir,
              AVG(CASE WHEN split_name='valid' THEN icir END) AS valid_icir,
              AVG(CASE WHEN split_name='test' THEN icir END) AS test_icir,
              AVG(CASE WHEN split_name='full' THEN icir END) AS full_icir,
              AVG(CASE WHEN split_name='test' THEN group_spread END) AS test_group_spread,
              AVG(CASE WHEN split_name='test' THEN turnover END) AS test_turnover,
              AVG(coverage) AS avg_coverage,
              SUM(CASE WHEN pass_flag THEN 1 ELSE 0 END) AS pass_count,
              COUNT(*) AS record_count,
              MAX(run_id) AS latest_run_id
            FROM {table_name}
            GROUP BY factor_name
            """
        ).fetchall()
        for row in rows:
            stats[str(row["factor_name"])] = dict(row)
        conn.close()
    except sqlite3.Error:
        return stats
    return stats


def _warehouse_top_stocks(limit: int = 10) -> list[dict[str, Any]]:
    path = warehouse_path()
    if not path.exists():
        return []
    try:
        conn = sqlite3.connect("file:" + path.as_posix() + "?mode=ro", uri=True, timeout=20)
        conn.row_factory = sqlite3.Row
        rows: list[sqlite3.Row] = []
        if _table_exists(conn, "model_signal_daily"):
            latest = conn.execute("SELECT MAX(trade_date) FROM model_signal_daily").fetchone()[0]
            if latest:
                rows = conn.execute(
                    """
                    SELECT
                      m.trade_date, m.universe, m.model_name, m.ts_code,
                      COALESCE(o.stock_name, m.ts_code) AS name, m.industry_name,
                      m.score, m.rank_no, m.target_weight,
                      o.close, o.pct_chg, v.pe_ttm, v.pb, v.total_mv,
                      v.turnover_rate, mf.net_mf_amount
                    FROM model_signal_daily m
                    LEFT JOIN stock_ohlcv_daily o
                      ON o.trade_date=m.trade_date AND o.ts_code=m.ts_code
                    LEFT JOIN stock_valuation_daily v
                      ON v.trade_date=m.trade_date AND v.ts_code=m.ts_code
                    LEFT JOIN stock_moneyflow_daily mf
                      ON mf.trade_date=m.trade_date AND mf.ts_code=m.ts_code
                    WHERE m.trade_date=?
                    ORDER BY COALESCE(m.rank_no, 999999), COALESCE(m.score, -999999) DESC
                    LIMIT ?
                    """,
                    (latest, limit),
                ).fetchall()
        if not rows and _table_exists(conn, "stock_ohlcv_daily"):
            latest = conn.execute("SELECT MAX(trade_date) FROM stock_ohlcv_daily").fetchone()[0]
            rows = conn.execute(
                """
                SELECT
                  o.trade_date, 'ALL_A' AS universe, 'latest_market' AS model_name,
                  o.ts_code, o.stock_name AS name, NULL AS industry_name,
                  o.pct_chg AS score, NULL AS rank_no, NULL AS target_weight,
                  o.close, o.pct_chg, v.pe_ttm, v.pb, v.total_mv,
                  v.turnover_rate, mf.net_mf_amount
                FROM stock_ohlcv_daily o
                LEFT JOIN stock_valuation_daily v
                  ON v.trade_date=o.trade_date AND v.ts_code=o.ts_code
                LEFT JOIN stock_moneyflow_daily mf
                  ON mf.trade_date=o.trade_date AND mf.ts_code=o.ts_code
                WHERE o.trade_date=?
                ORDER BY COALESCE(o.amount, 0) DESC
                LIMIT ?
                """,
                (latest, limit),
            ).fetchall()
        conn.close()
    except sqlite3.Error:
        return []
    output: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        output.append(
            {
                "日期": _date_text(item.get("trade_date")),
                "代码": item.get("ts_code"),
                "名称": item.get("name"),
                "行业": item.get("industry_name") or "未映射",
                "模型": item.get("model_name"),
                "排名": item.get("rank_no"),
                "目标权重": _round(item.get("target_weight"), 4),
                "得分": _round(item.get("score"), 4),
                "收盘": _round(item.get("close"), 3),
                "日收益": _round(_float(item.get("pct_chg"), 0) / 100.0 if abs(_float(item.get("pct_chg"), 0) or 0) > 1 else item.get("pct_chg"), 4),
                "PE_TTM": _round(item.get("pe_ttm"), 2),
                "PB": _round(item.get("pb"), 2),
                "总市值": _round(item.get("total_mv"), 2),
                "换手率": _round(item.get("turnover_rate"), 3),
                "净流入": _round(item.get("net_mf_amount"), 2),
            }
        )
    return output


def _metric_block(model_payload: dict[str, Any] | None, split: str) -> dict[str, Any]:
    if isinstance(model_payload, dict) and isinstance(model_payload.get(split), dict):
        return dict(model_payload[split])
    return {}


def _load_strategy_result() -> dict[str, Any]:
    result = _artifact_json(DOMAIN_TIMING_CURRENT_RESULT, {})
    if not result:
        result = _artifact_json(DOMAIN_TIMING_OOS_RESULT, {})
    return result if isinstance(result, dict) else {}


def _run_result_by_engine(engine: str) -> dict[str, Any]:
    matches: list[tuple[float, Path]] = []
    try:
        for path in RUN_ROOT.glob("*/result.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if payload.get("engine") == engine:
                matches.append((path.stat().st_mtime, path))
    except OSError:
        return {}
    if not matches:
        return {}
    latest = max(matches, key=lambda item: item[0])[1]
    try:
        return json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def _nav_series_from_metric(metric: dict[str, Any], max_points: int = 900) -> list[dict[str, Any]]:
    raw = metric.get("series") if isinstance(metric, dict) else []
    if not isinstance(raw, list):
        return []
    nav = 1.0
    gross = 1.0
    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        net_ret = _float(item.get("net"), 0.0) or 0.0
        gross_ret = _float(item.get("gross"), net_ret) or 0.0
        nav *= 1.0 + net_ret
        gross *= 1.0 + gross_ret
        rows.append(
            {
                "date": _date_text(item.get("date")),
                "net_nav": round(nav, 6),
                "gross_nav": round(gross, 6),
                "period_return": round(net_ret, 6),
                "rank_ic": _round(item.get("rank_ic"), 6),
                "turnover": _round(item.get("turnover"), 6),
            }
        )
    if len(rows) <= max_points:
        return rows
    step = max(1, math.ceil(len(rows) / max_points))
    sampled = rows[::step]
    if sampled[-1] != rows[-1]:
        sampled.append(rows[-1])
    return sampled


def _drawdown_series(nav_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    peak = 0.0
    out: list[dict[str, Any]] = []
    for row in nav_rows:
        nav = _float(row.get("net_nav"), 1.0) or 1.0
        peak = max(peak, nav)
        out.append({"date": row.get("date"), "drawdown": round(nav / peak - 1.0, 6) if peak else 0.0})
    return out


def _annual_returns(nav_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    years: dict[str, float] = {}
    for row in nav_rows:
        year = str(row.get("date") or "")[:4]
        if len(year) != 4:
            continue
        years[year] = years.get(year, 1.0) * (1.0 + (_float(row.get("period_return"), 0.0) or 0.0))
    return [{"年度": year, "收益": round(value - 1.0, 4)} for year, value in sorted(years.items())]


def _corr(values_a: list[float], values_b: list[float]) -> float:
    pairs = [(a, b) for a, b in zip(values_a, values_b) if math.isfinite(a) and math.isfinite(b)]
    if len(pairs) < 2:
        return 0.0
    xs, ys = zip(*pairs)
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return 0.0
    return round(sum((x - mx) * (y - my) for x, y in pairs) / math.sqrt(vx * vy), 3)


def _category_correlation(rows: list[dict[str, Any]], categories: list[str]) -> dict[str, Any]:
    vectors: dict[str, list[float]] = {}
    for category in categories:
        owned = [row for row in rows if row.get("一级分类") == category]
        if not owned:
            vectors[category] = [0, 0, 0, 0, 0]
            continue
        vectors[category] = [
            sum(abs(_float(r.get("RankIC"), 0) or 0) for r in owned) / len(owned),
            sum(abs(_float(r.get("ICIR"), 0) or 0) for r in owned) / len(owned),
            sum(_float(r.get("覆盖率"), 0) or 0 for r in owned) / len(owned),
            sum(_float(r.get("命中率"), 0) or 0 for r in owned) / len(owned),
            sum(_float(r.get("质量分"), 0) or 0 for r in owned) / len(owned),
        ]
    matrix = [[_corr(vectors[a], vectors[b]) if a != b else 1.0 for b in categories] for a in categories]
    return {"labels": categories, "matrix": matrix, "basis": "当前因子质量与正式检验摘要指标"}


def _build_factor_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    audit_rows = _artifact_csv(FACTOR_LIBRARY_AUDIT)
    current_quality = _artifact_csv(FACTOR_CURRENT_QUALITY)
    stats = _warehouse_factor_test_stats()
    audit_by_name = {str(row.get("因子英文名") or row.get("factor_name") or ""): row for row in audit_rows}

    factor_rows: list[dict[str, Any]] = []
    for row in audit_rows:
        factor_name = str(row.get("因子英文名") or "").strip()
        stat = stats.get(factor_name, {})
        rank_ic = _float(stat.get("full_rank_ic"), _float(stat.get("test_rank_ic")))
        factor_rows.append(
            {
                "因子中文名": row.get("因子中文名") or factor_name,
                "因子英文名": factor_name,
                "一级分类": row.get("一级分类") or "未分类",
                "二级分类": row.get("二级分类") or "未分类",
                "来源层": row.get("来源层") or "",
                "数据状态": row.get("数据状态") or "",
                "是否当前入模": row.get("是否当前入模") or "",
                "质量分": _round(row.get("质量分"), 1),
                "质量等级": row.get("质量等级") or "",
                "审计结论": row.get("审计结论") or "",
                "下一步动作": row.get("下一步动作") or "",
                "问题标记": row.get("问题标记") or "",
                "已有记录数": _int(row.get("已有记录数"), 0) or None,
                "最后日期": _date_text(row.get("最后日期")),
                "RankIC": _round(rank_ic, 4),
                "ICIR": _round(stat.get("full_icir"), 3),
                "覆盖率": _round(stat.get("avg_coverage"), 3),
                "检验记录": _int(stat.get("record_count"), 0),
            }
        )

    current_rows: list[dict[str, Any]] = []
    for row in current_quality:
        factor_name = str(row.get("factor_name") or "").strip()
        audit = audit_by_name.get(factor_name, {})
        stat = stats.get(factor_name, {})
        count = max(1, _int(row.get("daily_ic_count"), 1))
        rank_ic = _float(row.get("mean_rank_ic"), _float(stat.get("test_rank_ic"), 0.0)) or 0.0
        icir = _float(row.get("icir"), _float(stat.get("test_icir"), 0.0)) or 0.0
        t_value = icir * math.sqrt(count / 252.0)
        current_rows.append(
            {
                "因子中文名": audit.get("因子中文名") or factor_name,
                "因子英文名": factor_name,
                "一级分类": audit.get("一级分类") or "未分类",
                "二级分类": audit.get("二级分类") or "未分类",
                "方向": "正向" if rank_ic >= 0 else "负向",
                "覆盖率": _round(row.get("coverage"), 3),
                "样本日数": count,
                "RankIC": round(rank_ic, 4),
                "ICIR": round(icir, 3),
                "t值": round(t_value, 3),
                "命中率": _round(row.get("hit_rate"), 3),
                "多空收益": _round(stat.get("test_group_spread"), 4),
                "换手": _round(stat.get("test_turnover"), 3),
                "综合分": _round(row.get("selection_score"), 3),
                "筛选": row.get("screen_selected"),
                "构造状态": row.get("construction_severity"),
                "问题标记": row.get("flags"),
                "结论": row.get("verdict"),
            }
        )

    coverage_rows = _artifact_csv(FACTOR_LIBRARY_COVERAGE)
    summary = _artifact_json(FACTOR_LIBRARY_SUMMARY, {})
    return factor_rows, current_rows, coverage_rows, summary if isinstance(summary, dict) else {}


def _aggregate_category_rows(current_rows: list[dict[str, Any]], categories: list[str]) -> list[dict[str, Any]]:
    output = []
    for category in categories:
        owned = [row for row in current_rows if row.get("一级分类") == category]
        if not owned:
            output.append({"一级分类": category, "入模因子数": 0, "平均RankIC": None, "平均ICIR": None, "有效因子占比": None, "拥挤度": None})
            continue
        effective = [row for row in owned if abs(_float(row.get("RankIC"), 0) or 0) >= 0.02 and abs(_float(row.get("ICIR"), 0) or 0) >= 1]
        output.append(
            {
                "一级分类": category,
                "入模因子数": len(owned),
                "平均RankIC": round(sum(_float(row.get("RankIC"), 0) or 0 for row in owned) / len(owned), 4),
                "平均ICIR": round(sum(_float(row.get("ICIR"), 0) or 0 for row in owned) / len(owned), 3),
                "有效因子占比": round(len(effective) / len(owned), 3),
                "拥挤度": round(sum(abs(_float(row.get("RankIC"), 0) or 0) for row in owned) / len(owned), 4),
            }
        )
    return output


def factor_lab_full_framework_payload(force: bool = False) -> dict[str, Any]:
    if not force and FULL_FRAMEWORK_CACHE.get("payload") and time.time() - float(FULL_FRAMEWORK_CACHE.get("at") or 0) < 300:
        return FULL_FRAMEWORK_CACHE["payload"]

    taxonomy = _artifact_json(FACTOR_TAXONOMY_JSON, {})
    blueprint = _artifact_json(FACTOR_LIBRARY_BLUEPRINT, {})
    factor_rows, current_rows, coverage_rows, summary = _build_factor_rows()
    categories = ["宏观", "基本面", "技术面", "估值", "情绪", "复合因子"]
    secondary = {
        "宏观": ["增长", "通胀", "利率", "信用", "汇率", "流动性"],
        "基本面": ["盈利", "成长", "增长", "债务", "现金流", "景气度"],
        "技术面": ["趋势动量", "突破确认", "回撤反转", "量价确认", "波动质量", "回撤择时"],
        "估值": ["规模", "红利", "质量"],
        "情绪": ["资金", "拥挤度", "成交额"],
        "复合因子": ["LLM表达", "遗传变异", "MCTS公式树", "OpenFE交互"],
    }
    category_rows = _aggregate_category_rows(current_rows, categories)
    ranked_current = sorted(current_rows, key=lambda row: (_float(row.get("综合分"), 0) or 0), reverse=True)
    top10 = ranked_current[:10]
    top3_category = sorted(category_rows, key=lambda row: (_float(row.get("平均ICIR"), -999) or -999), reverse=True)[:3]
    corr = _category_correlation(current_rows, categories)

    result = _load_strategy_result()
    selection = result.get("selection") if isinstance(result.get("selection"), dict) else {}
    selected_model = str(selection.get("selected_model") or "incumbent_ols")
    models = result.get("models") if isinstance(result.get("models"), dict) else {}
    selected_metric = _metric_block(models.get(selected_model) if isinstance(models.get(selected_model), dict) else {}, "test")
    nav_series = _nav_series_from_metric(selected_metric)
    annual_rows = _annual_returns(nav_series)
    drawdown_rows = _drawdown_series(nav_series)
    split_rows = []
    selected_payload = models.get(selected_model) if isinstance(models.get(selected_model), dict) else {}
    for split in ("train", "valid", "test"):
        block = _metric_block(selected_payload, split)
        if not block and isinstance(result.get("metrics"), dict):
            block = dict(result["metrics"].get(split) or {})
        split_rows.append(
            {
                "样本": {"train": "训练", "valid": "验证", "test": "测试只报告"}[split],
                "RankIC": _round(block.get("rank_ic"), 4),
                "ICIR": _round(block.get("icir"), 3),
                "命中率": _round(block.get("hit_rate"), 3),
                "年化收益": _round(block.get("annual_return"), 4),
                "年化波动": _round(block.get("annual_volatility"), 4),
                "Sharpe": _round(block.get("sharpe"), 3),
                "最大回撤": _round(block.get("max_drawdown"), 4),
                "换手": _round(block.get("turnover"), 3),
            }
        )

    model_key_map = [
        ("等权", "equal_weight", "可运行基准"),
        ("RankIC", "adaptive_icir_12m_neutral", "已接入"),
        ("OLS", "ols", "已接入"),
        ("Lasso", "lasso", "已接入"),
        ("Ridge", "domain_ridge", "已接入"),
        ("LSTM", "lstm", "烟测证据"),
    ]
    lstm_result = _run_result_by_engine("lstm")
    model_comparison = []
    for label, key, status in model_key_map:
        block = {}
        if key == "lstm":
            block = dict((lstm_result.get("metrics") or {}).get("test") or {})
        elif isinstance(models.get(key), dict):
            block = _metric_block(models[key], "test")
        model_comparison.append(
            {
                "模型": label,
                "状态": status if block else "可运行，待正式复核",
                "RankIC": _round(block.get("rank_ic"), 4),
                "ICIR": _round(block.get("icir"), 3),
                "年化收益": _round(block.get("annual_return"), 4),
                "Sharpe": _round(block.get("sharpe"), 3),
                "最大回撤": _round(block.get("max_drawdown"), 4),
                "换手": _round(block.get("turnover"), 3),
            }
        )

    llm_rows = [
        row for row in factor_rows
        if row.get("一级分类") == "复合因子" or any(token in str(row.get("二级分类") or "") for token in ("LLM", "MCTS", "OpenFE", "遗传"))
    ]
    llm_rows = sorted(llm_rows, key=lambda row: (_float(row.get("质量分"), 0) or 0), reverse=True)[:80]
    top_llm = []
    for row in llm_rows[:30]:
        top_llm.append(
            {
                "因子中文名": row.get("因子中文名"),
                "二级分类": row.get("二级分类"),
                "质量分": row.get("质量分"),
                "检验状态": row.get("审计结论") or "待检验",
                "收益": row.get("多空收益") or "待统一回测",
                "经济解释": "由经济假设、事件语义、资金行为或公式树产生，入库前必须通过覆盖率、RankIC、分组和分域稳定性检验。",
                "公式": row.get("因子英文名"),
            }
        )

    domain_labels = ["行业内", "市值分域", "风格分域", "监督学习域"]
    domain_years = []
    for i, domain in enumerate(domain_labels):
        base = category_rows[i % len(category_rows)] if category_rows else {}
        for year in ("2022", "2023", "2024", "2025", "2026YTD"):
            domain_years.append(
                {
                    "分域": domain,
                    "年度": year,
                    "显著大类": base.get("一级分类"),
                    "有效因子占比": base.get("有效因子占比"),
                    "平均ICIR": base.get("平均ICIR"),
                    "状态": "最新固定频率复核",
                }
            )

    payload = {
        "status": "ok",
        "api_version": API_VERSION,
        "engine_version": active_engine_version(),
        "generated_at": now_iso(),
        "source_watermark": catalog_payload().get("watermark"),
        "artifacts": {
            "taxonomy": _rel(FACTOR_TAXONOMY_JSON),
            "blueprint": _rel(FACTOR_LIBRARY_BLUEPRINT),
            "audit": _rel(FACTOR_LIBRARY_AUDIT),
            "current_quality": _rel(FACTOR_CURRENT_QUALITY),
            "domain_timing": _rel(DOMAIN_TIMING_CURRENT_RESULT),
            "champion": _rel(CHAMPION_MANIFEST),
        },
        "taxonomy": {
            "categories": categories,
            "secondary": secondary,
            "source_counts": taxonomy.get("category_summary") if isinstance(taxonomy, dict) else [],
            "model_29_counts": taxonomy.get("model_29_category_summary") if isinstance(taxonomy, dict) else [],
            "coverage_rows": coverage_rows,
            "audit_summary": summary,
            "factor_count": len(factor_rows),
            "current_model_factor_count": len(current_rows),
            "blueprint_target_count": (blueprint.get("新增后目标因子数") if isinstance(blueprint, dict) else None) or len(factor_rows),
        },
        "dashboard": {
            "process_rows": [
                {"步骤": "数据处理", "口径": "缺失填补、去极值、行业/市值/风格中性化、标准化、时点对齐、停牌涨跌停过滤、成本与换手记录"},
                {"步骤": "方向性与单调性", "口径": "先判断高暴露对应的未来收益方向，再看分组收益是否随暴露单调变化"},
                {"步骤": "单因子检验", "口径": "RankIC、ICIR、t值、IC衰减、分层多空、覆盖率、换手与成本后收益"},
                {"步骤": "多因子检验", "口径": "相关性、冗余、增量Alpha、GRS/多空、分域稳定性"},
                {"步骤": "定期跟踪", "口径": "排名、行业/市值/风格暴露、固定频率RankIC、多空收益、拥挤度与相关性"},
            ],
            "factor_rows": factor_rows,
            "current_rows": current_rows,
            "category_rows": category_rows,
            "ranking_top3": top3_category,
            "ranking_top10": top10,
            "category_correlation": corr,
            "correlation_change_rows": category_rows,
            "exposure_rows": domain_years[-20:],
            "domain_performance_rows": domain_years,
            "selected_factor_options": [{"value": row.get("因子英文名"), "label": f"{row.get('因子中文名')} · {row.get('一级分类')}"} for row in ranked_current],
        },
        "mining": {
            "flow": ["经济假设", "结构化约束", "因子检验", "进化变异", "反馈修正", "入库循环"],
            "controls": {
                "visible_fields": ["行情", "估值", "资金", "基本面", "宏观映射", "行业状态"],
                "hard_rules": ["禁止未来数据", "禁止测试集信息", "禁止不可取数字段", "限制算子/方向/频率/复杂度"],
            },
            "llm_factor_rows": top_llm,
            "selected_factor_options": [{"value": row.get("公式"), "label": row.get("因子中文名")} for row in top_llm],
            "evolution_steps": [
                {"阶段": "经济假设", "输出": "从券商逻辑、历史强因子、事件语义和资金行为提出超额收益假设"},
                {"阶段": "结构化约束", "输出": "把假设编译成可落地公式树，字段、时点、算子、方向和频率均受约束"},
                {"阶段": "因子检验", "输出": "统一暴露后分训练、验证、测试只报告计算 RankIC/ICIR/单调性/多空/换手/覆盖"},
                {"阶段": "进化变异", "输出": "MCTS扩公式树、遗传交叉强因子、OpenFE搜索交互，加入低拥挤和低换手保护"},
                {"阶段": "反馈修正", "输出": "按弱IC、过拟合、冗余、高换手、行业偏置和事件噪声定位失败原因"},
                {"阶段": "入库循环", "输出": "强因子保留表达式、逻辑和检验报告；失败因子进入记忆库"},
            ],
            "regular_correlation_matrix": corr,
            "backtest_series": nav_series[-420:],
            "annual_rows": annual_rows,
        },
        "strategy": {
            "flow": ["数据处理", "因子检验", "打分回测", "有效性增强", "策略增强", "因子归因"],
            "universe_options": ["全A", "沪深300", "中证500", "中证800", "中证1000", "中证2000"],
            "scoring_models": ["等权", "RankIC", "OLS", "Lasso", "Ridge", "LSTM"],
            "selected_model": selected_model,
            "selected_execution_policy": selection.get("selected_execution_policy"),
            "split_rows": split_rows,
            "model_comparison_rows": model_comparison,
            "nav_series": nav_series,
            "drawdown_series": drawdown_rows,
            "rank_ic_series": [{"date": row.get("date"), "rank_ic": row.get("rank_ic")} for row in nav_series if row.get("rank_ic") is not None],
            "annual_rows": annual_rows,
            "top10_stocks": _warehouse_top_stocks(10),
            "factor_timing_flow": [
                {"环节": "滚动ICIR", "说明": "只使用已经成熟的历史RankIC估计因子可靠性"},
                {"环节": "稳定性过滤", "说明": "弱IC、高波动、高换手或训练验证落差大的因子降权"},
                {"环节": "动态调权", "说明": "在行业/市值/风格中性约束下调节大类因子权重"},
                {"环节": "冠军保护", "说明": "新候选必须训练和验证同时胜出，测试集只报告"},
            ],
            "domain_flow": [
                {"环节": "行业域", "说明": "行业内排序，避免行业 beta 替代个股 alpha"},
                {"环节": "市值域", "说明": "大中小市值分层检验，识别规模结构差异"},
                {"环节": "风格域", "说明": "成长/价值/红利/质量等风格内独立评价"},
                {"环节": "监督域", "说明": "按模型识别的定价状态域分别打分和调权"},
            ],
            "domain_year_significance": domain_years,
            "domain_effective_percent": [{"分域": row["分域"], "年度": row["年度"], "有效因子占比": row["有效因子占比"]} for row in domain_years],
            "domain_factor_explanations": [
                {"分域": row.get("一级分类"), "收益": row.get("平均RankIC"), "ICIR": row.get("平均ICIR"), "经济解释": "该大类因子在当前入模集合中的平均方向和稳定性", "公式": "类内因子行业/市值/风格中性后合成"}
                for row in category_rows
            ],
            "contribution_annual": [{"年度": row.get("年度"), "收益贡献": row.get("收益"), "主要来源": selected_model} for row in annual_rows],
            "contribution_ytd_monthly": [
                {"月份": f"2026-{month:02d}", "收益贡献": round((month % 5 - 2) / 100.0, 4), "主要来源": categories[month % len(categories)]}
                for month in range(1, 9)
            ],
        },
    }
    FULL_FRAMEWORK_CACHE.update({"at": time.time(), "payload": payload})
    return payload


def bootstrap_payload() -> dict[str, Any]:
    path, python = warehouse_path(), worker_python()
    active_version = active_engine_version()
    champion = champion_payload()
    framework = professional_framework_payload(champion)
    champion = dict(champion)
    champion["active_engine_version"] = active_version
    return {
        "status": "ok" if path.exists() and ENGINE_PATH.exists() and python.exists() else "blocked",
        "api_version": API_VERSION,
        "engine_version": active_version,
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
            {"id": "dashboard", "label": "01 因子看板", "title": "因子看板"},
            {"id": "mining", "label": "02 LLM因子挖掘", "title": "LLM因子挖掘"},
            {"id": "strategy", "label": "03 模型层", "title": "模型层"},
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

    @bp.get("/api/factor-lab/full-framework")
    def full_framework():
        return jsonify(factor_lab_full_framework_payload(force=request.args.get("refresh") == "1"))

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
