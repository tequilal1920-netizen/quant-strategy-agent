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


API_VERSION = "factor-lab-api/1.0"
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
STATE_DB.parent.mkdir(parents=True, exist_ok=True)
PROCESS_LOCK = threading.RLock()
PROCESSES: dict[str, subprocess.Popen] = {}
MAX_CONCURRENT = max(1, int(os.environ.get("FACTOR_LAB_MAX_CONCURRENT", "1")))
RUN_SEMAPHORE = threading.BoundedSemaphore(MAX_CONCURRENT)
CATALOG_CACHE: dict[str, Any] = {"at": 0.0, "payload": None}


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
            "净化嵌套搜索、successive halving、五种子深度集成",
        ],
        "defaults": {
            "sequence_length": 252, "hidden_dim": 160, "lstm_layers": 3,
            "attention_layers": 3, "heads": 8, "experts": 6, "dropout": .18,
            "learning_rate": 0.0003, "weight_decay": 0.0001, "grad_clip": 1.0,
            "epochs": 18, "ensemble_seeds": 5,
            "search": {"method": "purged_successive_halving", "trials": 12, "trial_epochs": 4},
        },
    },
    "rl_transformer": {
        "label": "语法约束协同 RL+Transformer",
        "architecture": [
            "后缀 AST 公式环境与栈类型系统",
            "六层 causal Transformer actor + critic value head",
            "字段/单位/栈深/算子/窗口硬动作掩码",
            "PPO clipped objective + GAE + KL/熵正则",
            "训练/验证最弱折、残差IC、净Sharpe、换手、冗余、复杂度联合奖励",
            "35%→100%多保真 successive halving",
            "复杂度×因子域质量多样性 archive",
            "搜索期严格隔离测试集，最终一次性报告",
        ],
        "defaults": {
            "d_model": 256, "layers": 6, "heads": 8, "dropout": .15,
            "max_formula_tokens": 18, "episodes": 2048, "rollout_batch": 64,
            "ppo_epochs": 4, "ppo_clip": .20, "gamma": .99, "gae_lambda": .95,
            "entropy": .01, "value_coef": .5, "learning_rate": .0002, "weight_decay": .0001,
        },
    },
    "strategy": {
        "label": "OLS / Lasso / 深度融合策略",
        "architecture": ["共同样本OLS", "一标准误Lasso", "256-128-64深度残差预测", "验证期净Sharpe约束融合", "次期成交与15bp成本"],
        "defaults": {"lasso_alpha": .00002, "epochs": 30, "max_training_samples": 300000},
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


def catalog_payload(force: bool = False) -> dict[str, Any]:
    if not force and CATALOG_CACHE.get("payload") and time.time() - float(CATALOG_CACHE.get("at") or 0) < 300:
        return CATALOG_CACHE["payload"]
    path = warehouse_path()
    base = {
        "status": "ok" if path.exists() else "blocked", "database_available": path.exists(),
        "watermark": None, "standard_factor_count": 107, "discovered_factor_count": 7,
        "families": [
            {"id": "technical", "label": "技术", "count": 25}, {"id": "money", "label": "资金", "count": 22},
            {"id": "fundamental", "label": "基本面", "count": 24}, {"id": "valuation", "label": "估值", "count": 18},
            {"id": "macro", "label": "宏观", "count": 18}, {"id": "discovered", "label": "发现", "count": 7},
            {"id": "lstm", "label": "LSTM", "count": 0}, {"id": "rl_transformer", "label": "RL+Transformer", "count": 0},
        ], "factors": [],
    }
    if path.exists():
        try:
            conn = sqlite3.connect("file:" + path.as_posix() + "?mode=ro", uri=True, timeout=20)
            conn.row_factory = sqlite3.Row
            watermark = conn.execute("SELECT MAX(trade_date) FROM stock_ohlcv_daily").fetchone()[0]
            factors = [dict(x) for x in conn.execute("SELECT factor_name,COALESCE(factor_group,'未分类') factor_group,COALESCE(source_agent,'local') source_agent,COUNT(*) value_count,MAX(trade_date) last_date FROM factor_value_daily GROUP BY factor_name,factor_group,source_agent ORDER BY last_date DESC,factor_name LIMIT 240")]
            tests = {x[0]: dict(x) for x in conn.execute("SELECT factor_name,MAX(ABS(rank_ic)) rank_ic,MAX(icir) icir,MAX(coverage) coverage,MAX(pass_flag) pass_flag FROM factor_test_result GROUP BY factor_name")}
            conn.close()
            for factor in factors:
                factor.update(tests.get(factor["factor_name"], {}))
            base.update({"watermark": watermark, "factors": factors, "registered_factor_count": len(factors)})
        except sqlite3.Error as exc:
            base.update({"status": "blocked", "message": str(exc)})
    with state_conn() as conn:
        counts = {row[0]: row[1] for row in conn.execute("SELECT engine,COUNT(*) FROM factor_lab_run WHERE status='completed' GROUP BY engine")}
    for family in base["families"]:
        if family["id"] in counts: family["count"] = counts[family["id"]]
    CATALOG_CACHE.update({"at": time.time(), "payload": base})
    return base


def bootstrap_payload() -> dict[str, Any]:
    path, python = warehouse_path(), worker_python()
    return {
        "status": "ok" if path.exists() and ENGINE_PATH.exists() and python.exists() else "blocked",
        "api_version": API_VERSION, "engine_version": "factor-lab/1.0-causal-mixture-ppo",
        "data": {"database_available": path.exists(), "database_hint": "server-side research warehouse", "watermark": catalog_payload().get("watermark"), "point_in_time": True},
        "worker": {"python_available": python.exists(), "isolated_process": True, "max_concurrent": MAX_CONCURRENT},
        "models": MODEL_PRESETS, "mode_caps": MODE_CAPS,
        "pages": [
            {"id": "home", "label": "01 主页"}, {"id": "dashboard", "label": "02 因子看板"},
            {"id": "mining", "label": "03 因子挖掘"}, {"id": "testing", "label": "04 联合检验"},
            {"id": "strategy", "label": "05 投资策略"}, {"id": "history", "label": "06 历史记录"},
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

