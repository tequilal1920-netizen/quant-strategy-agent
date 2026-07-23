import json
import base64
import hashlib
import hmac
import importlib.util
import os
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from collections import defaultdict, deque
from contextlib import contextmanager
from pathlib import Path

from flask import Flask, jsonify, request, session


APP_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = APP_ROOT.parents[2]
DEFAULT_MINER = PROJECT_ROOT / "model" / "llm_factor_mining" / "factor_miner.py"
RUN_ROOT = Path(os.environ.get("FACTOR_APP_RUN_ROOT", str(PROJECT_ROOT / "output" / "llm_factor_mining" / "runs")))
RUN_ROOT.mkdir(parents=True, exist_ok=True)
STATE_ROOT = Path(os.environ.get("FACTOR_APP_STATE_DIR", str(PROJECT_ROOT / "output" / "llm_factor_mining" / "state")))
STATE_ROOT.mkdir(parents=True, exist_ok=True)
AUTH_DB = Path(os.environ.get("FACTOR_APP_AUTH_DB", str(PROJECT_ROOT / "database" / "factor_mining_users.sqlite3")))
SECRET_FILE = STATE_ROOT / "flask_session_secret.txt"

DEFAULT_DB = os.environ.get(
    "FACTOR_APP_DB",
    str(PROJECT_ROOT / "database" / "research_warehouse.db"),
)
DEFAULT_MODEL = os.environ.get("FACTOR_MINING_LLM_MODEL", "gpt-5.5")
DEFAULT_REASONING = os.environ.get("FACTOR_MINING_REASONING_EFFORT", "xhigh")
MODEL_ENGINE_VERSION = "v27.3_csi2000_point_in_time_strategy_diagnostics"
PORT = int(os.environ.get("FACTOR_APP_PORT", "8895"))
PUBLIC_MAX_MONTHS = int(os.environ.get("FACTOR_PUBLIC_MAX_MONTHS", "180"))
PUBLIC_MAX_BUDGET = int(os.environ.get("FACTOR_PUBLIC_MAX_BUDGET", "8"))
PUBLIC_MAX_ITERATIONS = int(os.environ.get("FACTOR_PUBLIC_MAX_ITERATIONS", "8"))
PUBLIC_MAX_CANDIDATES = int(os.environ.get("FACTOR_PUBLIC_MAX_CANDIDATES", "20"))
ALLOW_PUBLIC_FULL_RUN = os.environ.get("FACTOR_ALLOW_PUBLIC_FULL_RUN", "1") == "1"
REQUIRE_GPT = os.environ.get("FACTOR_REQUIRE_GPT", "1") == "1"

PRIVATE_ENV_CANDIDATES = [
    Path(os.environ["FACTOR_MINING_ENV_FILE"]) if os.environ.get("FACTOR_MINING_ENV_FILE") else None,
    Path(r"F:\apps\factor_mining_private\factor_mining_public.env"),
    APP_ROOT / "private" / "factor_mining_public.env",
]


def load_private_env():
    loaded = []
    for path in PRIVATE_ENV_CANDIDATES:
        if not path or not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
        loaded.append(str(path))
    return loaded


LOADED_ENV_FILES = load_private_env()

app = Flask(__name__)
if os.environ.get("FACTOR_APP_SECRET_KEY"):
    app.secret_key = os.environ["FACTOR_APP_SECRET_KEY"]
else:
    if SECRET_FILE.exists():
        app.secret_key = SECRET_FILE.read_text(encoding="utf-8").strip()
    else:
        app.secret_key = secrets.token_urlsafe(48)
        SECRET_FILE.write_text(app.secret_key, encoding="utf-8")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
jobs = {}
job_lock = threading.Lock()
runner_lock = threading.Lock()
rate_window = defaultdict(lambda: deque(maxlen=20))
diagnostics_module_lock = threading.Lock()
diagnostics_module_cache = {}


@contextmanager
def state_conn():
    conn = sqlite3.connect(AUTH_DB, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_state_db():
    with state_conn() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS job_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                job_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                universe TEXT,
                max_months TEXT,
                iterations INTEGER,
                budget_per_channel INTEGER,
                target_accepted INTEGER,
                max_candidates INTEGER,
                status TEXT NOT NULL,
                elapsed_seconds REAL,
                summary_json TEXT,
                result_json TEXT,
                error TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)


def normalize_username(username):
    username = str(username or "").strip()
    if len(username) < 3 or len(username) > 32:
        return ""
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
    return username if all(ch in allowed for ch in username) else ""


def hash_password(password, salt=None):
    raw = str(password or "").encode("utf-8")
    salt_bytes = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", raw, salt_bytes, 200000)
    return base64.b64encode(salt_bytes).decode("ascii"), base64.b64encode(digest).decode("ascii")


def verify_password(password, salt_text, digest_text):
    try:
        salt = base64.b64decode(salt_text.encode("ascii"))
        _, expected = hash_password(password, salt)
        return hmac.compare_digest(expected, digest_text)
    except Exception:
        return False


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    with state_conn() as conn:
        row = conn.execute("SELECT id, username, created_at FROM users WHERE id=?", (uid,)).fetchone()
    return dict(row) if row else None


def require_user_json():
    user = current_user()
    if not user:
        return None, (jsonify({"status": "failed", "message": "请先登录。"}), 401)
    return user, None


def result_to_public_payload(result, max_candidates=PUBLIC_MAX_CANDIDATES):
    leaderboard = result.get("leaderboard", [])
    accepted = result.get("accepted_factors", [])

    def factor_rows(rows, limit):
        output = []
        for row in list(rows or [])[:limit]:
            clean = dict(row)
            clean.pop("stock_diagnostics", None)
            output.append(clean)
        return output

    diagnostics = {}
    for factor_name, block in dict(result.get("stock_diagnostics") or {}).items():
        clean = dict(block)
        clean.pop("runtime_file", None)
        diagnostics[str(factor_name)] = clean
    return {
        "model_version": result.get("model_version"),
        "run_id": result.get("run_id"),
        "rows": result.get("rows"),
        "months": result.get("months"),
        "candidate_count": result.get("candidate_count"),
        "accepted_count": result.get("accepted_count"),
        "production_ready_count": result.get("production_ready_count"),
        "production_confirmed_count": result.get("production_confirmed_count"),
        "target_accepted": result.get("target_accepted"),
        "max_candidates": result.get("max_candidates"),
        "stop_reason": result.get("stop_reason"),
        "persisted_to_database": result.get("persisted_to_database"),
        "llm_adapter": result.get("llm_adapter"),
        "initial_llm_candidate_count": result.get("initial_llm_candidate_count"),
        "initial_llm_generation_audit": result.get("initial_llm_generation_audit"),
        "search_strategy": result.get("search_strategy"),
        "runtime_audit": result.get("runtime_audit"),
        "panel_cache_audit": result.get("panel_cache_audit"),
        "split_audit": result.get("split_audit"),
        "execution_audit": result.get("execution_audit"),
        "validation_policy": result.get("validation_policy"),
        "search_reliability_audit": result.get("search_reliability_audit"),
        "search_channels": result.get("search_channels"),
        "search_controller": result.get("search_controller"),
        "applied_methodology": result.get("applied_methodology"),
        "data_space": result.get("data_space"),
        "operator_space": result.get("operator_space"),
        "constraint_space": result.get("constraint_space"),
        "iteration_log": result.get("iteration_log"),
        "memory_prior": result.get("memory_prior"),
        "memory_update": result.get("memory_update"),
        "memory_store_audit": result.get("memory_store_audit"),
        "flow_steps": result.get("flow_steps"),
        "gates": result.get("gates"),
        "search_objective": result.get("search_objective"),
        "deep_method_cards": result.get("deep_method_cards"),
        "accepted_factors": factor_rows(accepted, 20),
        "factor_reports": factor_rows(leaderboard, max_candidates),
        "stock_diagnostics": diagnostics,
    }

def job_summary(job):
    result = job.get("result") or {}
    return {
        "job_id": job.get("id"),
        "created_at": job.get("created_at"),
        "universe": job.get("universe"),
        "max_months": job.get("max_months"),
        "iterations": job.get("iterations"),
        "budget_per_channel": job.get("budget_per_channel"),
        "target_accepted": job.get("target_accepted"),
        "max_candidates": job.get("max_candidates"),
        "status": job.get("status"),
        "elapsed_seconds": job.get("elapsed_seconds"),
        "rows": result.get("rows"),
        "months": result.get("months"),
        "candidate_count": result.get("candidate_count"),
        "accepted_count": result.get("accepted_count"),
        "stop_reason": result.get("stop_reason"),
        "error": job.get("error"),
    }


def upsert_history(job):
    user_id = job.get("user_id")
    if not user_id:
        return
    summary = job_summary(job)
    with state_conn() as conn:
        conn.execute("""
            INSERT INTO job_history (
                user_id, job_id, created_at, completed_at, universe, max_months, iterations,
                budget_per_channel, target_accepted, max_candidates, status, elapsed_seconds,
                summary_json, result_json, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                completed_at=excluded.completed_at,
                status=excluded.status,
                elapsed_seconds=excluded.elapsed_seconds,
                summary_json=excluded.summary_json,
                result_json=excluded.result_json,
                error=excluded.error
        """, (
            user_id,
            job.get("id"),
            job.get("created_at"),
            time.strftime("%Y-%m-%d %H:%M:%S") if job.get("status") in {"done", "failed"} else None,
            job.get("universe"),
            "full" if job.get("max_months") is None else str(job.get("max_months")),
            job.get("iterations"),
            job.get("budget_per_channel"),
            job.get("target_accepted"),
            job.get("max_candidates"),
            job.get("status"),
            job.get("elapsed_seconds"),
            json.dumps(summary, ensure_ascii=False),
            json.dumps(job.get("result"), ensure_ascii=False) if job.get("result") else None,
            job.get("error"),
        ))


def server_run_summaries(limit=50):
    rows = []
    for run_dir in sorted([p for p in RUN_ROOT.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True):
        files = sorted(run_dir.glob("factor_mining_*.json"))
        if not files:
            continue
        try:
            result = json.loads(files[0].read_text(encoding="utf-8"))
        except Exception:
            continue
        rows.append({
            "job_id": run_dir.name,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(files[0].stat().st_mtime)),
            "universe": result.get("universe"),
            "max_months": "历史文件",
            "status": "done",
            "rows": result.get("rows"),
            "months": result.get("months"),
            "candidate_count": result.get("candidate_count"),
            "accepted_count": result.get("accepted_count"),
            "stop_reason": result.get("stop_reason"),
            "source": "server_run",
        })
        if len(rows) >= limit:
            break
    return rows


def read_server_run_source(job_id):
    clean = "".join(ch for ch in str(job_id) if ch.isalnum() or ch in {"_", "-"})
    if clean != job_id:
        return None
    run_dir = RUN_ROOT / clean
    if not run_dir.exists():
        return None
    files = sorted(run_dir.glob("factor_mining_*.json"))
    if not files:
        return None
    result = json.loads(files[0].read_text(encoding="utf-8"))
    return run_dir, files[0], result


def read_server_run(job_id):
    source = read_server_run_source(job_id)
    if not source:
        return None
    return result_to_public_payload(source[2], PUBLIC_MAX_CANDIDATES)


def load_diagnostics_module(script_path):
    path = Path(script_path).resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    key = (str(path), path.stat().st_mtime_ns)
    with diagnostics_module_lock:
        module = diagnostics_module_cache.get(key)
        if module is not None:
            return module
        spec = importlib.util.spec_from_file_location("factor_diagnostics_runtime", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load diagnostics module: {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        diagnostics_module_cache.clear()
        diagnostics_module_cache[key] = module
        return module


init_state_db()


def secret_present(name):
    return bool(os.environ.get(name))


def public_config():
    return {
        "model": os.environ.get("FACTOR_MINING_LLM_MODEL", DEFAULT_MODEL),
        "model_engine_version": MODEL_ENGINE_VERSION,
        "reasoning_effort": os.environ.get("FACTOR_MINING_REASONING_EFFORT", DEFAULT_REASONING),
        "ai_router_configured": secret_present("AI_ROUTER_API_KEY") or secret_present("OPENAI_API_KEY"),
        "database_exists": Path(os.environ.get("FACTOR_APP_DB", DEFAULT_DB)).exists(),
        "db_path_public_hint": "server-side research_warehouse.db",
        "env_loaded": bool(LOADED_ENV_FILES),
        "require_gpt": REQUIRE_GPT,
        "isolated_memory_enabled": AUTH_DB.exists(),
        "memory_policy": "per_user_train_validation_search_only_no_test_fields",
        "public_limits": {
            "max_months": PUBLIC_MAX_MONTHS,
            "max_budget_per_channel": PUBLIC_MAX_BUDGET,
            "max_iterations": PUBLIC_MAX_ITERATIONS,
            "max_candidates": PUBLIC_MAX_CANDIDATES,
            "full_window_allowed": ALLOW_PUBLIC_FULL_RUN,
            "concurrency": 1,
        },
    }


def client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.remote_addr or "unknown"


def check_rate_limit(ip):
    now = time.time()
    bucket = rate_window[ip]
    while bucket and now - bucket[0] > 3600:
        bucket.popleft()
    if len(bucket) >= 5:
        return False
    bucket.append(now)
    return True


def clamp_int(value, default, low, high):
    try:
        value = int(value)
    except Exception:
        value = default
    return max(low, min(high, value))


def create_job(payload, user):
    now = time.time()
    universe = str(payload.get("universe", "ALL_A")).strip().upper()
    if universe not in {"ALL_A", "CSI800_ENH", "CSI2000_ENH"}:
        universe = "ALL_A"
    raw_months = payload.get("max_months", "full")
    full = str(raw_months).lower() in {"full", "all", "-1", "0", "none"}
    max_months = None if full and ALLOW_PUBLIC_FULL_RUN else clamp_int(raw_months, 36, 3, PUBLIC_MAX_MONTHS)
    iterations = clamp_int(payload.get("iterations", 6), 6, 1, PUBLIC_MAX_ITERATIONS)
    budget = clamp_int(payload.get("budget_per_channel", 6), 6, 1, PUBLIC_MAX_BUDGET)
    target = clamp_int(payload.get("target_accepted", 1), 1, 1, PUBLIC_MAX_CANDIDATES)
    max_candidates = clamp_int(payload.get("max_candidates", PUBLIC_MAX_CANDIDATES), PUBLIC_MAX_CANDIDATES, 2, PUBLIC_MAX_CANDIDATES)
    target = min(target, max_candidates)
    return {
        "id": uuid.uuid4().hex[:12],
        "user_id": user["id"],
        "username": user["username"],
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "created_at_epoch": now,
        "started_at_epoch": None,
        "elapsed_seconds": 0,
        "status": "queued",
        "progress": "已排队",
        "universe": universe,
        "max_months": max_months,
        "iterations": iterations,
        "budget_per_channel": budget,
        "target_accepted": target,
        "max_candidates": max_candidates,
        "strict_policy": "v27_champion_gpt_challenge_then_failure_expansion",
        "result": None,
        "error": None,
    }


def run_job(job_id):
    with runner_lock:
        started = time.time()
        with job_lock:
            job = jobs[job_id]
            job["status"] = "running"
            job["progress"] = "正在调用因子挖掘 Agent"
            job["started_at_epoch"] = started
            job["elapsed_seconds"] = 0
        job = jobs[job_id]
        out_dir = RUN_ROOT / job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        db = os.environ.get("FACTOR_APP_DB", DEFAULT_DB)
        model_script = Path(os.environ.get("FACTOR_MINING_SCRIPT", str(DEFAULT_MINER)))
        if REQUIRE_GPT and not (secret_present("AI_ROUTER_API_KEY") or secret_present("OPENAI_API_KEY")):
            with job_lock:
                job.update({
                    "status": "failed",
                    "progress": "后端 GPT API 未配置，已拒绝 fallback 挖掘。",
                    "error": "Server-side AI_ROUTER_API_KEY/OPENAI_API_KEY is required.",
                    "elapsed_seconds": 0,
                })
                upsert_history(job)
            return
        cmd = [
            sys.executable,
            str(model_script),
            "--db",
            db,
            "--universe",
            job["universe"],
            "--start",
            "20120101",
            "--end",
            "20260630",
            "--iterations",
            str(job["iterations"]),
            "--budget-per-channel",
            str(job["budget_per_channel"]),
            "--target-accepted",
            str(job["target_accepted"]),
            "--max-candidates",
            str(job["max_candidates"]),
            "--max-redundancy",
            "0.82",
            "--min-test-rank-ic",
            "0.02",
            "--stop-on-pass",
            "--no-persist",
            "--memory-db",
            str(AUTH_DB),
            "--memory-scope",
            job["username"],
            "--out-dir",
            str(out_dir),
        ]
        if job["max_months"] is not None:
            cmd.extend(["--max-months", str(job["max_months"])])
        env = os.environ.copy()
        env["FACTOR_MINING_LLM_MODEL"] = env.get("FACTOR_MINING_LLM_MODEL", "gpt-5.5")
        env["FACTOR_MINING_REASONING_EFFORT"] = env.get("FACTOR_MINING_REASONING_EFFORT", "xhigh")
        env["FACTOR_REQUIRE_GPT"] = "1" if REQUIRE_GPT else "0"
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(APP_ROOT),
                env=env,
                text=True,
                capture_output=True,
                timeout=int(os.environ.get("FACTOR_JOB_TIMEOUT_SECONDS", "10800")),
            )
            if proc.returncode != 0:
                raise RuntimeError((proc.stderr or proc.stdout or "factor miner failed")[-2000:])
            result_file = out_dir / f"factor_mining_{job['universe']}.json"
            if not result_file.exists():
                raise RuntimeError("Factor miner did not create its formal result file.")
            result = json.loads(result_file.read_text(encoding="utf-8"))
            diagnostics_enabled = os.environ.get("FACTOR_DIAGNOSTICS_ENABLED", "1") == "1"
            if diagnostics_enabled and list(result.get("accepted_factors") or []):
                diagnostics_script = Path(
                    os.environ.get("FACTOR_DIAGNOSTICS_SCRIPT", str(model_script.with_name("factor_diagnostics.py")))
                ).resolve()
                if not diagnostics_script.exists():
                    raise FileNotFoundError(f"Factor diagnostics script is missing: {diagnostics_script}")
                with job_lock:
                    job["progress"] = "正在执行三股票池四频率与个股策略回测"
                    job["elapsed_seconds"] = round(time.time() - started, 2)
                diagnostics_cmd = [
                    sys.executable,
                    str(diagnostics_script),
                    "--result",
                    str(result_file),
                    "--miner",
                    str(model_script),
                    "--cost-rate",
                    os.environ.get("FACTOR_DIAGNOSTICS_COST_RATE", "0.0015"),
                    "--workers",
                    os.environ.get("FACTOR_DIAGNOSTICS_WORKERS", "4"),
                ]
                diagnostics_start = os.environ.get("FACTOR_DIAGNOSTICS_START", "").strip()
                if diagnostics_start:
                    diagnostics_cmd.extend(["--start", diagnostics_start])
                diagnostics_end = os.environ.get("FACTOR_DIAGNOSTICS_END", "20260630").strip()
                if diagnostics_end:
                    diagnostics_cmd.extend(["--end", diagnostics_end])
                diagnostics_proc = subprocess.run(
                    diagnostics_cmd,
                    cwd=str(APP_ROOT),
                    env=env,
                    text=True,
                    capture_output=True,
                    timeout=int(os.environ.get("FACTOR_DIAGNOSTICS_TIMEOUT_SECONDS", "10800")),
                )
                if diagnostics_proc.returncode != 0:
                    detail = diagnostics_proc.stderr or diagnostics_proc.stdout or "factor diagnostics failed"
                    raise RuntimeError(detail[-3000:])
                result = json.loads(result_file.read_text(encoding="utf-8"))
            public_result = result_to_public_payload(result, job["max_candidates"])
            with job_lock:
                job.update({
                    "status": "done",
                    "progress": "完成",
                    "elapsed_seconds": round(time.time() - started, 2),
                    "result": public_result,
                })
                upsert_history(job)
        except Exception as exc:
            with job_lock:
                job.update({
                    "status": "failed",
                    "progress": "失败",
                    "error": str(exc)[-2000:],
                    "elapsed_seconds": round(time.time() - started, 2),
                })
                upsert_history(job)


def public_job_view(job):
    data = dict(job)
    if data.get("status") in {"queued", "running"}:
        start = data.get("started_at_epoch") or data.get("created_at_epoch")
        if start:
            data["elapsed_seconds"] = round(time.time() - float(start), 1)
    return data


@app.get("/")
@app.get("/factor-mining")
@app.get("/factor-mining/")
def index():
    return """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <link rel="icon" href="data:," />
  <title>AI因子挖掘Agent</title>
  <style>
    :root { color-scheme:light; --ink:#172033; --muted:#667085; --line:#d8dee9; --bg:#f5f7fb; --panel:#ffffff; --accent:#276b58; --accent2:#2f5f9f; --bad:#9d3b3b; --good:#1f8a5b; --warn:#b7791f; }
    * { box-sizing:border-box; }
    body { margin:0; background:var(--bg); color:var(--ink); font-family:Arial, KaiTi, STKaiti, "KaiTi_GB2312", "Microsoft YaHei", sans-serif; }
    body.auth-locked { overflow:hidden; background:#f4f7fb; }
    body.auth-locked .shell { display:none; }
    .shell { max-width:1240px; margin:0 auto; padding:30px 22px 42px; }
    h1 { margin:0 0 18px; font-size:30px; letter-spacing:0; font-weight:700; }
    h2 { font-size:22px; margin:0 0 14px; }
    h3 { font-size:17px; margin:14px 0 8px; }
    .sub { color:var(--muted); margin-bottom:18px; line-height:1.7; }
    .panel { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:18px; margin-bottom:16px; }
    .grid { display:grid; grid-template-columns:1.1fr 1fr .75fr .75fr .75fr .85fr; gap:12px; align-items:end; }
    label { display:block; font-size:14px; color:var(--muted); margin-bottom:7px; }
    select, input, button { width:100%; height:43px; border-radius:6px; font-size:15px; font-family:Arial, KaiTi, STKaiti, "KaiTi_GB2312", "Microsoft YaHei", sans-serif; }
    select, input { border:1px solid var(--line); padding:0 10px; background:white; color:var(--ink); }
    button { border:0; background:var(--accent); color:white; cursor:pointer; font-weight:600; }
    button:disabled { opacity:.55; cursor:not-allowed; }
    button.secondary { background:#fff; color:var(--ink); border:1px solid var(--line); }
    .status { display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; color:var(--muted); }
    .pill { display:inline-block; border:1px solid var(--line); border-radius:999px; padding:4px 9px; color:var(--muted); background:#fff; font-size:12px; }
    .pill.good, .pill.active { color:var(--good); border-color:#b9decf; background:#f0fbf5; }
    .pill.bad, .pill.danger { color:var(--bad); border-color:#efc0c0; background:#fff5f5; }
    .progress-track { height:10px; border-radius:999px; background:#e7ebf1; overflow:hidden; }
    .progress-fill { height:100%; width:0%; background:linear-gradient(90deg,#276b58,#2f5f9f); transition:width .25s ease; }
    .steps { display:grid; grid-template-columns:repeat(7, minmax(0,1fr)); gap:8px; margin-top:12px; }
    .step { border:1px solid var(--line); border-radius:6px; padding:8px 6px; text-align:center; color:var(--muted); background:#fbfcff; font-size:13px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .step.current { border-color:var(--accent2); color:var(--accent2); background:#eef5ff; font-weight:700; }
    .step.done { border-color:#b9decf; color:var(--good); background:#f0fbf5; }
    .formula { border:1px solid var(--line); background:#fbfcff; border-radius:8px; padding:14px; min-height:54px; overflow:auto; font-family:Arial, "Times New Roman", KaiTi, STKaiti, serif; font-size:16px; }
    .formula .latex { display:block; font-size:18px; line-height:1.9; white-space:normal; }
    .formula .source { display:block; margin-top:8px; color:#666; font-size:12px; white-space:pre-wrap; }
    .two { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
    .cards { display:grid; grid-template-columns:repeat(4, minmax(170px,1fr)); gap:10px; }
    .metric { border:1px solid var(--line); border-radius:6px; padding:11px 12px; background:#fbfcff; min-height:66px; }
    .metric b { display:block; font-size:13px; color:var(--muted); font-weight:500; margin-bottom:5px; }
    .metric span { font-size:18px; font-weight:700; font-family:Arial, KaiTi, STKaiti, "KaiTi_GB2312", sans-serif; }
    .bars { display:grid; gap:8px; }
    .barrow { display:grid; grid-template-columns:70px 1fr 80px; gap:8px; align-items:center; font-size:13px; }
    .track { height:10px; background:#e7ebf1; border-radius:999px; overflow:hidden; }
    .fill { height:100%; background:linear-gradient(90deg,#276b58,#2f5f9f); }
    table, .metric-table { width:100%; border-collapse:collapse; margin-top:12px; font-size:14px; }
    th, td, .metric-table th, .metric-table td { border-bottom:1px solid var(--line); padding:9px 8px; text-align:left; vertical-align:top; }
    th { background:#fbfcff; color:var(--muted); font-weight:600; }
    .muted { color:var(--muted); }
    .empty { color:var(--muted); border:1px dashed var(--line); border-radius:8px; padding:12px; background:#fbfcff; }
    .modal-backdrop { position:fixed; inset:0; z-index:9999; display:flex; align-items:center; justify-content:center; padding:20px; background:#f4f7fb; }
    .auth-modal { width:min(520px, calc(100vw - 40px)); background:#fff; border:1px solid #d8dee9; border-radius:8px; padding:38px; box-shadow:0 22px 70px rgba(23,32,51,.12); }
    .auth-modal h2 { margin:0 0 24px; font-family:Arial, sans-serif; font-size:29px; line-height:1.2; letter-spacing:0; color:#172033; }
    .auth-form { display:grid; gap:18px; margin-top:0; }
    .auth-form label { font-family:Arial, sans-serif; font-size:15px; font-weight:600; color:#667085; margin-bottom:8px; }
    .auth-form input { height:58px; border-radius:7px; border:1px solid #cfd6e2; padding:0 16px; font-size:18px; font-family:Arial, sans-serif; }
    .auth-form input:focus { outline:none; border-color:#222; box-shadow:0 0 0 1px #222; }
    .auth-actions { display:grid; gap:10px; margin-top:10px; }
    .auth-primary { height:58px; border-radius:7px; background:#4055d8; color:#fff; font-family:Arial, sans-serif; font-size:18px; font-weight:700; }
    .auth-link { height:auto; padding:6px 0 0; border:0; background:transparent; color:#4055d8; font-family:Arial, KaiTi, STKaiti, "KaiTi_GB2312", sans-serif; font-size:14px; font-weight:700; }
    .auth-msg { min-height:20px; margin:0; color:var(--muted); font-size:13px; line-height:1.5; }
    .session-strip { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:14px; }
    .session-strip button { width:120px; }
    .history-head { display:flex; justify-content:space-between; gap:12px; align-items:center; margin-bottom:12px; flex-wrap:wrap; }
    .chartbox, .canvas-wrap { border:1px solid var(--line); border-radius:8px; background:#fff; padding:12px; min-height:260px; }
    .chartbox svg { width:100%; height:260px; display:block; }
    .visual-grid { display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-top:12px; }
    .heatmap { display:grid; gap:6px; }
    .heat-row { display:grid; grid-template-columns:80px repeat(3, minmax(70px,1fr)); gap:4px; align-items:center; font-size:12px; color:var(--muted); }
    .heat-cell { min-height:22px; border-radius:4px; border:1px solid #edf0f5; display:flex; align-items:center; justify-content:center; font-size:11px; color:#172033; }
    .legend { display:flex; gap:10px; flex-wrap:wrap; margin-top:6px; font-size:12px; color:var(--muted); }
    .legend i { display:inline-block; width:16px; height:3px; vertical-align:middle; margin-right:4px; }

    .panel { overflow:hidden; }
    .two > *, .visual-grid > *, .chartbox, .metric, .formula, .panel { min-width:0; }
    .formula { overflow:hidden; }
    .math-display { width:100%; overflow-x:auto; overflow-y:hidden; padding:14px 16px; border:1px solid var(--line); border-radius:8px; background:#f8fafc; }
    .math-display mjx-container { max-width:100%; overflow-x:auto; overflow-y:hidden; }
    .formula-source { margin-top:10px; color:var(--muted); }
    .formula-source code { display:block; white-space:pre-wrap; word-break:break-word; padding:10px; border:1px solid var(--line); border-radius:8px; background:#fff; font-size:12px; }
    .table-scroll { overflow:auto; max-height:300px; border:1px solid var(--line); border-radius:8px; background:#fff; }
    .table-scroll table { min-width:720px; margin-top:0; }
    .acceptance-strip { display:grid; grid-template-columns:repeat(auto-fit, minmax(165px,1fr)); gap:10px; margin-bottom:14px; }
    .acceptance-pill { border:1px solid var(--line); border-radius:8px; padding:10px 12px; background:#fff; min-height:68px; }
    .acceptance-pill strong { display:block; margin-bottom:5px; font-size:13px; }
    .acceptance-pill span { font-size:17px; font-weight:700; }
    .acceptance-pill.pass { border-color:#b9decf; background:#f0fbf5; color:var(--good); }
    .acceptance-pill.fail { border-color:#efc0c0; background:#fff5f5; color:var(--bad); }
    .factor-cards { display:grid; grid-template-columns:repeat(auto-fit, minmax(260px,1fr)); gap:12px; margin-top:10px; }
    .factor-card { border:1px solid var(--line); border-radius:8px; background:#fff; padding:12px; cursor:pointer; transition:border-color .15s, box-shadow .15s; }
    .factor-card:hover { border-color:var(--accent2); box-shadow:0 8px 22px rgba(23,32,51,.08); }
    .factor-card.active { border-color:var(--accent2); background:#f2f7ff; }
    .factor-card h4 { margin:0 0 8px; font-size:16px; }
    .factor-card .kv { display:grid; grid-template-columns:1fr 1fr; gap:8px; color:var(--muted); }
    .factor-card .kv b { display:block; color:var(--ink); font-size:18px; margin-top:2px; }
    .result-band { display:grid; grid-template-columns:repeat(auto-fit, minmax(210px,1fr)); gap:10px; margin:12px 0 14px; }
    .metric small { display:block; margin-top:6px; color:var(--muted); line-height:1.45; }
    .fold-cards { display:grid; grid-template-columns:repeat(auto-fit, minmax(180px,1fr)); gap:10px; margin-top:10px; }
    .fold-card { border:1px solid var(--line); border-radius:8px; padding:10px; background:#fff; }
    .fold-card span { color:var(--muted); display:block; font-size:12px; }
    .fold-card b { font-size:18px; }
    details.compact-details { margin-top:10px; }
    details.compact-details summary { cursor:pointer; color:var(--accent2); }

    @media (max-width:980px) { .grid, .cards, .two, .visual-grid, .steps { grid-template-columns:1fr; } .shell { padding:18px 12px 30px; } h1 { font-size:24px; } .session-strip { align-items:flex-start; flex-direction:column; } .auth-modal { padding:30px 26px; } }
  </style>
<script>
window.MathJax = {
  tex: { inlineMath: [['\\\\(', '\\\\)']], displayMath: [['\\\\[', '\\\\]']] },
  chtml: { scale: 1.0, matchFontHeight: false }
};
</script>
<script defer src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"></script>
</head>
<body class="auth-locked">
<div id="authModal" class="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="authTitle">
  <div class="auth-modal">
    <h2 id="authTitle">AI Factor Login</h2>
    <div class="auth-form">
      <div><label>Username</label><input id="authUser" autocomplete="username" autofocus /></div>
      <div><label>Password</label><input id="authPass" type="password" autocomplete="current-password" /></div>
      <div class="auth-actions">
        <button id="loginBtn" class="auth-primary">Sign in</button>
        <button id="registerBtn" class="auth-link">Register account</button>
      </div>
      <div id="authMsg" class="auth-msg"></div>
    </div>
  </div>
</div>
<div class="shell">
  <h1>AI因子挖掘Agent</h1>
  <div class="sub">目标是让 GPT 生成可执行因子程序，再由本地检验框架做审计、回测、归因和智能变异。密钥只在服务器后端使用，前端不可见。</div>
  <div class="session-strip">
    <div id="authState" class="pill bad">未登录</div>
    <button id="logoutBtn" class="secondary">退出登录</button>
  </div>
  <div class="panel">
    <div class="grid">
      <div><label>股票池</label><select id="universe"><option>ALL_A</option><option>CSI800_ENH</option><option>CSI2000_ENH</option></select></div>
      <div><label>样本窗口</label><select id="months"><option value="full">全窗口</option><option value="36">36个月分层抽样</option><option value="24">24个月分层抽样</option></select></div>
      <div><label>目标通过数</label><input id="target" type="number" min="1" max="20" step="1" value="1" /></div>
      <div><label>候选上限</label><input id="maxc" type="number" min="2" max="20" step="1" value="20" /></div>
      <div><label>迭代轮数</label><input id="iterations" type="number" min="1" max="8" step="1" value="6" /></div>
      <div><label>每通道候选数</label><input id="budget" type="number" min="1" max="8" step="1" value="6" /></div>
    </div>
    <p><button id="start">开始挖掘</button></p>
    <div id="cfg" class="status"></div>
    <div id="runline" class="sub">尚未开始。</div>
    <div class="progress-track"><div id="pbar" class="progress-fill"></div></div>
  </div>
  <div class="panel">
    <div class="history-head">
      <h2>历史记录查询</h2>
      <button id="refreshHistory">刷新历史</button>
    </div>
    <div id="historyTable" class="empty">登录后显示历史记录。</div>
  </div>
  <div class="panel">
    <h2>模型流程与时间进度</h2>
    <div id="flow" class="steps"></div>
  </div>
  <div class="panel">
    <h2>因子公式表达式</h2>
    <div class="two">
      <div>
        <label>选择因子</label>
        <select id="factorSelect"></select>
        <div id="factorMeta" class="status"></div>
      </div>
      <div>
        <h3>构造逻辑</h3>
        <div id="construction" class="sub">等待挖掘结果。</div>
      </div>
    </div>
    <div id="formula" class="formula">等待因子公式。</div>
  </div>
  <div class="panel">
    <h2>因子检验结果</h2>
    <h3>综合检验表</h3>
    <div id="summaryTable"></div>
    <div id="metricCards" class="cards"></div>
    <div class="two">
      <div>
        <h3>训练 / 验证 / 测试分段</h3>
        <div id="splitTable"></div>
      </div>
      <div>
        <h3>五组分组收益</h3>
        <div id="groupBars" class="bars"></div>
      </div>
    </div>
    <h3>组合净值与回撤曲线</h3>
    <div class="two">
      <div class="chartbox"><div id="navChart"></div></div>
      <div class="chartbox"><div id="drawdownChart"></div></div>
    </div>
    <div class="visual-grid">
      <div class="chartbox">
        <h3>IC 时序图</h3>
        <div id="icChart"></div>
      </div>
      <div class="chartbox">
        <h3>年度热力衰减</h3>
        <div id="annualHeatmap"></div>
      </div>
    </div>
    <h3>年度稳定性</h3>
    <div id="annualTable"></div>
    <div class="two">
      <div>
        <h3>快速初筛</h3>
        <div id="quickScreen"></div>
      </div>
      <div>
        <h3>滚动样本外检验</h3>
        <div id="walkForward"></div>
      </div>
    </div>
    <h3>隔离K折防过拟合检验</h3>
    <div id="purgedKfold"></div>
    <h3>分层归因</h3>
    <div id="attributionTable"></div>
  </div>
  <div class="panel">
    <h2>综合归因与裁判打分</h2>
    <div id="scoreCards" class="cards"></div>
    <div id="diagnosis" class="sub"></div>
    <div id="candidateTable"></div>
  </div>
</div>
<script>
const defaultFlow = ['方法空间', 'GPT假设', '程序审计', '因子计算', '检验回测', '归因打分', '记忆迭代'];
let currentReports = [];
let currentUser = null;
const API_BASE = location.pathname.startsWith('/factor-mining') ? '/factor-mining' : '';
function esc(x){ return String(x ?? '').replace(/[&<>"']/g, s=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s])); }
function sig(x, digits=2){
  const n = Number(x);
  if(!Number.isFinite(n)) return '0';
  if(n === 0) return '0';
  const abs = Math.abs(n);
  const exponent = Math.floor(Math.log10(abs));
  if(exponent >= 4 || exponent <= -4) return n.toExponential(digits - 1).replace('e+', 'e');
  const decimals = digits - 1 - exponent;
  if(decimals >= 0) return n.toFixed(decimals);
  const magnitude = 10 ** (-decimals);
  return String(Math.round(n / magnitude) * magnitude);
}
function num(x, digits=2){ return sig(x, digits); }
function pct(x, digits=2){ return sig(Number(x || 0) * 100, digits) + '%'; }
function cnChannel(x){
  const m = {
    llm_hypothesis_generation:'GPT假设生成',
    llm_feedback_mutation:'GPT归因变异',
    llm_fresh_hypothesis_injection:'GPT全新假设注入',
    deep_representation:'深度表征',
    raw_causal_grammar_seed:'原始时点因果语法',
    nested_orthogonal_complement_seed:'训练期嵌套正交补充',
    mcts_tree_seed:'表达式树初始种子',
    mcts_tree_search:'表达式树搜索',
    mcts_feedback_tree_search:'反馈式树搜索',
    genetic_crossover:'遗传交叉',
    multiobjective_synergy_ensemble:'多目标协同合成',
    pareto_parent_crossover:'帕累托协同交叉',
    openfe_feature_search:'自动特征生成',
    rl_bandit_policy:'强化学习通道选择',
    window_structure_search:'窗口结构搜索',
    residual_monotonic_repair:'残差单调性修复',
    failure_memory_mutation:'失败记忆定向变异',
    bayesian_window_probe:'贝叶斯窗口探针'
  };
  return m[x] || x || '';
}
function lifecycleCn(x){
  const m = {
    healthy:'生命周期健康',
    regime_transition_watch:'状态迁移观察',
    tail_realization_watch:'尾部兑现观察',
    signal_decay_watch:'信号衰减观察',
    structural_decay:'结构性衰减',
    insufficient_recent_sample:'近期样本不足'
  };
  return m[x] || x || '尚未评估';
}
function jobStatusCn(x){ return ({done:'完成', failed:'失败', queued:'排队中', running:'运行中'})[x] || x || ''; }
function stopReasonCn(x){
  return ({
    iteration_limit_reached:'达到迭代轮数上限',
    candidate_limit_reached:'达到候选数量上限',
    candidate_pool_exhausted:'候选池已穷尽',
    lifecycle_healthy_target_confirmed_across_two_search_rounds:'生命周期健康因子已连续两轮确认',
    strict_research_target_reached:'严格研究门槛通过数已达到目标'
  })[x] || x || '';
}
function cacheStatusCn(x){
  return ({hit:'缓存命中', miss_written:'首次构建并缓存', stale:'数据库变化后重建', read_error:'缓存读取失败后重建', write_error:'缓存写入失败', disabled:'未启用'})[x] || x || '未知';
}
function productionReasonCn(x){
  const reason = x.lifecycle_production_ready_reason || '';
  if(reason === 'healthy_contiguous_monthly_test_window') return '生命周期健康且测试窗口连续完整。';
  if(reason === 'non_monthly_or_short_probe_window') return '窗口过短或非连续，仅作为研究探针。';
  if(reason === 'train_validation_posterior_evidence_not_ready') return '训练验证联合后验尚未形成正赔率，暂不进入生产。';
  if(reason.startsWith('lifecycle_state_')) return `当前为${lifecycleCn(reason.slice('lifecycle_state_'.length))}。`;
  return reason;
}
function productionCn(x){
  if(!reportAccepted(x)) return '未通过研究门槛';
  return x.lifecycle_production_ready ? '生产就绪' : '统计通过，暂不生产';
}
function purgedDecisionCn(x){
  return ({
    relative_rank_passed:'相对排名通过',
    penalty_only_absolute_complement_survived:'相对排名仅作惩罚，绝对补充证据存活',
    material_relative_and_absolute_migration_failure:'相对排名与绝对补充证据同时失败'
  })[x] || x || '未形成证据';
}
function mctsActionCn(x){
  return ({
    dual_residual_raw_temporal_expansion:'原始时点字段的双残差时序扩展',
    downstream_complementarity_residual_expansion:'面向组合边际贡献的互补残差扩展',
    downstream_orthogonal_complement_expansion:'面向组合边际贡献的正交补充扩展',
    continuous_regime_breadth_expansion:'连续市场状态广度扩展',
    turnover_aware_realization_expansion:'兼顾换手与收益兑现的平滑扩展',
    validation_stability_smoothing_expansion:'验证稳定性与低复杂度平滑扩展',
    industry_rank_monotonic_expansion:'行业内单调性修复',
    cross_domain_nonlinear_novelty_expansion:'跨数据域非线性新颖性扩展',
    low_complexity_residual_expansion:'低复杂度残差化扩展',
    posterior_uct_cross_domain_expansion:'后验UCT跨数据域扩展'
  })[x] || x || '未记录动作';
}
function lineageSummary(x){
  const events = arr(x.lineage);
  if(!events.length) return '无额外血缘记录';
  return events.slice(-3).map(e => {
    if(e.synergy_parent_factors){
      const names = Array.isArray(e.synergy_parent_names) ? e.synergy_parent_names.join('、') : '';
      const weights = e.synergy_weights && typeof e.synergy_weights === 'object'
        ? Object.values(e.synergy_weights).map(value=>num(value,2)).join(' / ')
        : '';
      const audit = e.feature_selection_audit || {};
      const internalCscv = e.selected_internal_cscv || {};
      const internalCscvText = internalCscv.candidate_evidence_available
        ? pct(internalCscv.posterior_mean_pbo,2)
        : '无候选级证据';
      return '多目标协同合成；父代 ' + (names || '已记录') + '；权重 ' + (weights || '已记录') +
        '；内部评估 ' + num(audit.internal_hypotheses_evaluated,2) +
        ' 个组合；内部CSCV后验PBO ' + internalCscvText +
        '；训练/验证隔离折选择；测试集未参与父代与权重搜索';
    }
    if(e.mcts_parent){
      const pathDepth = arr(e.mcts_path).length;
      const feature = e.expanded_feature ? `；扩展字段 ${e.expanded_feature}` : '';
      const window = e.expanded_window ? `；因果窗口 ${e.expanded_window} 期` : '';
      return `后验MCTS父节点 ${e.mcts_parent}；深度 ${e.mcts_depth ?? pathDepth}；子树访问 ${e.mcts_subtree_visits ?? 0}；Q值 ${num(e.mcts_subtree_q_value,2)}；UCT ${num(e.mcts_uct,2)}；${mctsActionCn(e.mcts_action)}${feature}${window}；测试集未参与搜索`;
    }
    return e.prior_hypothesis || e.mutation || e.mutation_reason || e.parent_factor || e.parent || e.model || '审计事件';
  }).join(' → ');
}
function flowIndexFromProgress(d){
  if(!d) return 0;
  if(d.status === 'done') return defaultFlow.length;
  if(d.status === 'failed') return Math.max(1, defaultFlow.length - 1);
  const elapsed = Number(d.elapsed_seconds || 0);
  const estimate = d.max_months ? 1200 : 5400;
  return Math.max(1, Math.min(defaultFlow.length - 1, Math.floor(defaultFlow.length * Math.min(elapsed / estimate, .92)) + 1));
}
function renderFlow(progressIndex=0){
  document.getElementById('flow').innerHTML = defaultFlow.map((s,i)=>{
    const cls = i < progressIndex ? 'step done' : (i === progressIndex ? 'step current' : 'step');
    return `<span class="${cls}">${i+1}. ${esc(s)}</span>`;
  }).join('');
}
async function loadStatus(){
  let d;
  try {
    const r = await fetch(API_BASE + '/api/status', {cache:'no-store'});
    d = await r.json();
  } catch(e) {
    document.getElementById('cfg').innerHTML = '<span class="pill bad">状态接口：不可用</span>';
    return;
  }
  document.getElementById('cfg').innerHTML = [
    ['模型', d.model + ' / ' + d.reasoning_effort],
    ['研究引擎', d.model_engine_version || '未知'],
    ['GPT API', d.ai_router_configured ? '已配置' : '未配置'],
    ['数据库', d.database_exists ? '可用' : '不可用'],
    ['隔离记忆', d.isolated_memory_enabled ? '可用' : '不可用'],
    ['全窗口', d.public_limits.full_window_allowed ? '允许' : '关闭'],
    ['候选上限', d.public_limits.max_candidates],
    ['并发', d.public_limits.concurrency]
  ].map(x=>`<span class="pill ${x[1]==='未配置'||x[1]==='不可用'?'bad':'good'}">${x[0]}：${x[1]}</span>`).join('');
}
async function loadMe(){
  const r = await fetch(API_BASE + '/api/auth/me', {cache:'no-store'});
  const d = await r.json();
  currentUser = d.authenticated ? d.user : null;
  document.getElementById('authState').textContent = currentUser ? `已登录：${currentUser.username}` : '未登录';
  document.getElementById('authState').className = currentUser ? 'pill good' : 'pill bad';
  document.getElementById('start').disabled = !currentUser;
  const modal = document.getElementById('authModal');
  if(currentUser){
    document.body.classList.remove('auth-locked');
    modal.style.display = 'none';
    await loadHistory();
  } else {
    document.body.classList.add('auth-locked');
    modal.style.display = 'flex';
    document.getElementById('historyTable').innerHTML = '<div class="empty">登录后显示历史记录。</div>';
  }
}
async function authPost(url){
  const payload = {
    username: document.getElementById('authUser').value,
    password: document.getElementById('authPass').value
  };
  const r = await fetch(API_BASE + url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  const d = await r.json();
  document.getElementById('authMsg').textContent = d.message || (d.status === 'ok' ? '操作成功。' : '操作失败。');
  if(r.ok){
    document.getElementById('authPass').value = '';
    await loadMe();
  }
}
async function logout(){
  await fetch(API_BASE + '/api/auth/logout', {method:'POST'});
  currentUser = null;
  document.getElementById('authMsg').textContent = '已退出，请重新登录。';
  document.getElementById('historyTable').innerHTML = '<div class="empty">登录后显示历史记录。</div>';
  await loadMe();
}
function renderHistoryRows(rows, sourceLabel){
  if(!rows || !rows.length) return '';
  return rows.map(x=>`<tr>
    <td>${esc(sourceLabel)}</td><td>${esc(x.job_id)}</td><td>${esc(x.created_at || '')}</td><td>${esc(x.universe || '')}</td>
    <td>${esc(jobStatusCn(x.status))}</td><td>${num(x.rows)}</td><td>${num(x.months)}</td><td>${num(x.candidate_count)}</td><td>${num(x.accepted_count)}</td>
    <td><button onclick="loadHistoryDetail('${esc(x.job_id)}')">加载</button></td>
  </tr>`).join('');
}
async function loadHistory(){
  if(!currentUser){
    document.getElementById('historyTable').innerHTML = '<div class="empty">登录后显示历史记录。</div>';
    return;
  }
  const r = await fetch(API_BASE + '/api/history', {cache:'no-store'});
  const d = await r.json();
  if(!r.ok){
    document.getElementById('historyTable').innerHTML = `<div class="empty">${esc(d.message || '历史记录读取失败。')}</div>`;
    return;
  }
  const rows = renderHistoryRows(d.account_history, '账号') + renderHistoryRows(d.server_runs, '服务器');
  document.getElementById('historyTable').innerHTML = rows ? `<div class="table-scroll"><table><thead><tr><th>来源</th><th>任务</th><th>时间</th><th>股票池</th><th>状态</th><th>样本</th><th>期数</th><th>候选</th><th>通过</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<div class="empty">暂无历史记录。</div>';
}
async function loadHistoryDetail(jobId){
  const r = await fetch(API_BASE + '/api/history/' + encodeURIComponent(jobId), {cache:'no-store'});
  const d = await r.json();
  if(!r.ok){
    document.getElementById('authMsg').textContent = d.message || '历史结果读取失败。';
    return;
  }
  renderJob({id: jobId, status:'done', progress:'历史结果', elapsed_seconds:d.elapsed_seconds || 0, result:d.result});
  window.scrollTo({top:0, behavior:'smooth'});
}
function clearResultPanels(){
  currentReports = [];
  document.getElementById('factorSelect').innerHTML = '';
  document.getElementById('factorMeta').innerHTML = '';
  document.getElementById('construction').textContent = '等待挖掘结果。';
  document.getElementById('formula').innerHTML = '<div class="empty">等待因子公式。</div>';
  ['summaryTable','metricCards','splitTable','groupBars','navChart','drawdownChart','icChart','annualHeatmap','annualTable','quickScreen','walkForward','purgedKfold','attributionTable','scoreCards','diagnosis','candidateTable'].forEach(id => {
    document.getElementById(id).innerHTML = '';
  });
}
function fmtElapsed(seconds){
  const s = Math.max(0, Number(seconds || 0));
  if(s < 60) return `${s.toFixed(0)}s`;
  const m = Math.floor(s / 60);
  const r = Math.floor(s % 60);
  if(m < 60) return `${m}m ${r}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}
function chartPath(values, minY, maxY, w, h, pad){
  if(!values.length) return '';
  const span = maxY === minY ? 1 : maxY - minY;
  return values.map((v,i)=>{
    const x = pad + (w - pad * 2) * (values.length === 1 ? 0 : i / (values.length - 1));
    const y = h - pad - (h - pad * 2) * ((v - minY) / span);
    return `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
}
function renderLineChart(id, curve, series, title, asPct=false){
  const el = document.getElementById(id);
  if(!curve || !curve.length){
    el.innerHTML = '<div class="empty">本次结果没有曲线数据；新任务会生成净值与回撤序列。</div>';
    return;
  }
  const w = 760, h = 260, pad = 34;
  const values = [];
  series.forEach(s => curve.forEach(p => {
    const v = Number(p[s.key]);
    if(Number.isFinite(v)) values.push(v);
  }));
  if(!values.length){ el.innerHTML = '<div class="empty">曲线数据为空。</div>'; return; }
  let minY = Math.min(...values), maxY = Math.max(...values);
  if(minY === maxY){ minY -= 1; maxY += 1; }
  const paths = series.map(s => {
    const vals = curve.map(p => Number(p[s.key])).map(v => Number.isFinite(v) ? v : minY);
    return `<path d="${chartPath(vals, minY, maxY, w, h, pad)}" fill="none" stroke="${s.color}" stroke-width="2.2"/>`;
  }).join('');
  const firstDate = curve[0].date || '';
  const lastDate = curve[curve.length - 1].date || '';
  const topLabel = asPct ? pct(maxY) : num(maxY);
  const bottomLabel = asPct ? pct(minY) : num(minY);
  const legend = series.map(s=>`<span><i style="background:${s.color}"></i>${esc(s.label)}</span>`).join('');
  el.innerHTML = `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(title)}">
    <rect x="0" y="0" width="${w}" height="${h}" fill="#fbfbfb"/>
    <line x1="${pad}" y1="${pad}" x2="${pad}" y2="${h-pad}" stroke="#ddd"/>
    <line x1="${pad}" y1="${h-pad}" x2="${w-pad}" y2="${h-pad}" stroke="#ddd"/>
    <line x1="${pad}" y1="${pad}" x2="${w-pad}" y2="${pad}" stroke="#eee"/>
    <text x="${pad}" y="18" font-size="13" fill="#222">${esc(title)}</text>
    <text x="4" y="${pad+4}" font-size="11" fill="#666">${topLabel}</text>
    <text x="4" y="${h-pad+4}" font-size="11" fill="#666">${bottomLabel}</text>
    <text x="${pad}" y="${h-8}" font-size="11" fill="#666">${esc(firstDate)}</text>
    <text x="${w-pad-70}" y="${h-8}" font-size="11" fill="#666">${esc(lastDate)}</text>
    ${paths}
  </svg><div class="legend">${legend}</div>`;
}
function isObj(x){ return x && typeof x === 'object' && !Array.isArray(x); }
function arr(x){ return Array.isArray(x) ? x : []; }
function finite(x, fallback=0){ const n = Number(x); return Number.isFinite(n) ? n : fallback; }
function firstValue(...xs){
  for(const x of xs){
    if(x !== undefined && x !== null && x !== '' && Number.isFinite(Number(x))) return Number(x);
  }
  return 0;
}
function firstObj(...xs){
  for(const x of xs){ if(isObj(x) && Object.keys(x).length) return x; }
  return {};
}
function firstArray(...xs){
  for(const x of xs){ if(Array.isArray(x) && x.length) return x; }
  return [];
}
function splitPart(m, name){ return isObj(m[name]) ? m[name] : {}; }
function btPart(split){ return isObj(split.backtest) ? split.backtest : {}; }
function backtestParts(x, m){
  const test = splitPart(m, 'test'), valid = splitPart(m, 'valid'), full = splitPart(m, 'full'), train = splitPart(m, 'train');
  const testBt = btPart(test), validBt = btPart(valid), fullBt = btPart(full), trainBt = btPart(train);
  const lo = firstObj(testBt.long_only, test.long_only, fullBt.long_only, full.long_only, validBt.long_only, valid.long_only, trainBt.long_only, train.long_only);
  const ls = firstObj(testBt.long_short, test.long_short, fullBt.long_short, full.long_short, validBt.long_short, valid.long_short, trainBt.long_short, train.long_short);
  const curve = firstArray(x.backtest_curve, testBt.curve, test.backtest_curve, fullBt.curve, full.backtest_curve, validBt.curve, valid.backtest_curve, trainBt.curve, train.backtest_curve);
  return {lo, ls, curve};
}
function metricRow(label, value, note){ return `<tr><td>${esc(label)}</td><td>${esc(value)}</td><td>${esc(note || '')}</td></tr>`; }
function renderSummaryTable(x, m){
  m = m || {};
  const parts = backtestParts(x, m);
  const test = splitPart(m, 'test');
  const wf = firstObj(x.walk_forward, m.walk_forward);
  const pk = firstObj(x.purged_kfold, m.purged_kfold);
  const retState = firstObj(x.lifecycle_return_state_diagnostics);
  const icState = firstObj(x.lifecycle_ic_state_diagnostics);
  const recent6 = firstObj(x.lifecycle_recent_6m);
  const recent12 = firstObj(x.lifecycle_recent_12m);
  const incremental = firstObj(x.incremental_evidence, m.incremental_evidence);
  const regime = firstObj(x.regime_evidence, m.regime_evidence);
  const searchPosterior = firstObj(x.posterior_search_evidence, m.posterior_search_evidence);
  const finalPosterior = firstObj(x.posterior_final_evidence, m.posterior_final_evidence);
  const validIncremental = firstObj(incremental.valid);
  const testIncremental = firstObj(incremental.test);
  const validRegime = firstObj(regime.valid);
  const temporalAudit = firstObj(firstObj(x.static_audit).causal_temporal_audit);
  const purgedEvidence = firstObj(x.purged_rank_migration_evidence);
  const searchPurgedEvidence = firstObj(x.search_purged_rank_migration_evidence);
  const candidateCscv = x.test_cscv_candidate_evidence_available ? pct(x.test_cscv_pbo_when_selected, 2) : '未形成候选级证据';
  const searchCscvPosterior = x.search_cscv_candidate_evidence_available ? pct(x.search_cscv_probability_overfit_above_half, 2) : '未形成候选级证据';
  const rows = [
    ['最终状态', reportStatus(x), x.diagnosis_cn || ''],
    ['生命周期状态', lifecycleCn(x.lifecycle_state), productionReasonCn(x)],
    ['生产资格', productionCn(x), productionReasonCn(x)],
    ['质量多样性分岛', x.quality_diversity_island || '未标注', '按经济域、程序架构和复杂度分岛，避免候选在单一结构上早熟收敛。'],
    ['验证残差 RankIC', num(firstValue(validIncremental.residual_rank_ic, x.valid_incremental_residual_rank_ic), 2), '因子与收益同时剔除训练期冻结基线后，在验证期剩余信息上的 RankIC。'],
    ['测试残差 RankIC', num(firstValue(testIncremental.residual_rank_ic, x.test_incremental_residual_rank_ic), 2), '封存测试期的增量信息，仅用于最终报告，不反馈搜索。'],
    ['验证组合边际 RankIC', num(firstValue(validIncremental.marginal_rank_ic_gain, x.valid_downstream_marginal_rank_ic_gain), 2), '候选加入冻结基线组合后，验证期 RankIC 的净增量。'],
    ['测试组合边际 RankIC', num(firstValue(testIncremental.marginal_rank_ic_gain, x.test_downstream_marginal_rank_ic_gain), 2), '封存测试期组合贡献，仅用于最终审计。'],
    ['验证状态正向广度', pct(firstValue(validRegime.posterior_positive_breadth, x.valid_regime_positive_breadth), 2), '训练期定义的趋势与风险状态中，RankIC 为正后验概率的平均值。'],
    ['最弱状态90%下界', num(firstValue(validRegime.worst_state_lower_90, x.valid_worst_regime_lower_90), 2), '最弱市场状态的后验保守下界，用于识别状态集中。'],
    ['训练验证联合后验', pct(firstValue(searchPosterior.joint_positive_probability, x.posterior_joint_positive_probability), 2), '训练、验证、增量、组合、收益兑现与状态广度的几何联合概率。'],
    ['最终联合后验', pct(firstValue(finalPosterior.joint_positive_probability, x.posterior_final_joint_positive_probability), 2), '加入封存测试信号、残差、组合贡献与收益兑现后的报告概率。'],
    ['时序窗口审计', temporalAudit.mode || '未使用时序算子', temporalAudit.mode === 'sparse_research_observation_lag' ? '当前是分层抽样探针，窗口按历史观测计数，不能当作连续月度生产结果。' : '连续月度窗口只使用当前及过去观测。'],
    ['部署置信度', pct(x.lifecycle_deployment_confidence, 2), '最近6月相对此前24月的收益、IC与联合变化概率合成值。'],
    ['建议权重倍数', pct(x.lifecycle_recommended_weight_multiplier, 2), '连续风险缩放建议，不是收益承诺。'],
    ['联合衰减概率', pct(x.lifecycle_joint_deterioration_probability, 2), '收益与RankIC同时弱于历史状态的经验贝叶斯证据。'],
    ['近期收益为正概率', pct(retState.positive_probability, 2), '最近6月收益经历史弱先验收缩后的正值概率。'],
    ['近期IC为正概率', pct(icState.positive_probability, 2), '最近6月RankIC经历史弱先验收缩后的正值概率。'],
    ['最近6月多空年化', pct(recent6.annual_return, 2), '用于识别尾部兑现与状态迁移，不单独作为硬阈值。'],
    ['最近12月多空年化', pct(recent12.annual_return, 2), '连续月度生命周期观察窗口。'],
    ['测试期 RankIC', num(firstValue(test.rank_ic, x.test_rank_ic), 2), '样本外横截面排序相关，越稳定越好。'],
    ['Newey-West t值', num(x.test_rank_ic_t_newey_west, 2), '对IC时序自相关和异方差做稳健修正。'],
    ['多空年化收益', pct(firstValue(parts.ls.annual_return, x.test_long_short_annual_return), 2), 'Top组减Bottom组、扣除双边交易成本后的收益。'],
    ['多空夏普', num(firstValue(parts.ls.sharpe, x.test_long_short_sharpe), 2), '衡量多空组合风险调整后收益。'],
    ['多空最大回撤', pct(firstValue(parts.ls.max_drawdown, x.test_long_short_max_drawdown), 2), '净值曲线相对历史高点的最大跌幅。'],
    ['多头超额年化', pct(firstValue(parts.lo.excess_annual_return, x.test_long_excess_annual_return), 2), 'Top组相对基准的年化超额收益。'],
    ['信息比率', num(firstValue(parts.lo.information_ratio, x.test_long_information_ratio), 2), '多头超额收益相对跟踪误差的稳定性。'],
    ['DSR置信度', pct(x.test_long_short_deflated_sharpe_confidence, 2), '按有效独立试验数修正后的多空夏普置信度。'],
    ['有效独立试验数', num(x.effective_multiple_testing_trials, 2), '由候选收益相关结构估算，不直接使用名义候选数。'],
    ['PBO代理', pct(x.pbo_proxy, 2), 'Purged折内排序迁移推断的回测过拟合风险。'],
    ['候选级CSCV PBO', candidateCscv, '仅在该候选被CSCV选中时报告，未选中不伪造为0。'],
    ['搜索CSCV后验风险', searchCscvPosterior, '在训练与验证互补分块中，候选被选中后样本外排名低于中位数的 Beta-Binomial 后验概率；测试集不参与。'],
    ['父代外前沿相关', num(reportCorr(x), 2), '相对可靠前沿代表、排除协同因子已声明父代后的最大绝对相关，决定新颖性；普通因子仍比较全部其他候选。'],
    ['父代相关性（仅披露）', x.lineage_novelty_exemption_applied ? num(x.max_abs_corr_to_declared_parents, 2) : '不适用', '协同因子与自身组成父代的最大绝对相关，仅用于血缘审计，不替代残差增量、组合边际贡献或多重检验。'],
    ['全池父代外最大相关', num(x.global_max_abs_corr_excluding_declared_parents ?? x.global_max_abs_corr_to_other_factor, 2), '对全部候选计算并排除已声明父代后的最大绝对相关，用于检查前沿筛选是否遗漏非父代重复。'],
    ['隔离折相对排名', num(purgedEvidence.relative_oos_rank_percentile ?? x.purged_oos_rank_percentile_mean, 2), '候选在冻结样本外时间块中相对其他候选的平均 IC 排名；该值保留为风险惩罚，不再单独否决独立补充因子。'],
    ['隔离折绝对补充存活', purgedEvidence.absolute_complement_survival_pass ? '通过' : '未通过', purgedEvidence.absolute_complement_survival_pass ? '正 IC 折占比、训练验证后验、残差增量与组合边际同时存活。' : '相对排名偏低且至少一项绝对折外、残差或组合贡献证据不足。'],
    ['搜索期隔离折结论', purgedDecisionCn(searchPurgedEvidence.decision), '仅使用训练与验证区间；测试集不参与该结论。'],
    ['滚动正IC占比', pct(firstValue(wf.positive_rate, wf.positive_test_ic_ratio, x.walk_positive_ratio), 2), '冻结训练映射后的验证年份中RankIC为正的比例。'],
    ['隔离K折正IC占比', pct(firstValue(pk.positive_rate, pk.positive_test_ic_ratio, x.purged_positive_ratio), 2), '剔除相邻调仓期后仍然为正的折叠比例。']
  ];
  document.getElementById('summaryTable').innerHTML = `<div class="table-scroll"><table class="metric-table"><thead><tr><th>指标</th><th>数值</th><th>解释</th></tr></thead><tbody>${rows.map(r=>metricRow(r[0], r[1], r[2])).join('')}</tbody></table></div>`;
}
function annualRows(annual){
  if(Array.isArray(annual)) return annual.map(r => ({year:String(r.year ?? r.date ?? ''), rank_ic:firstValue(r.rank_ic, r.ic), group_spread:firstValue(r.group_spread, r.spread), coverage:firstValue(r.coverage, 1), long_return:firstValue(r.long_return, r.long_annual_return, r.annual_return), benchmark_return:firstValue(r.benchmark_return, r.bench_return)})).filter(r => r.year);
  if(isObj(annual)) return Object.entries(annual).map(([year, r]) => ({year, rank_ic:firstValue(r.rank_ic, r.ic), group_spread:firstValue(r.group_spread, r.spread), coverage:firstValue(r.coverage, 1), long_return:firstValue(r.long_return, r.annual_return), benchmark_return:firstValue(r.benchmark_return, r.bench_return)}));
  return [];
}
function annualCurveFromMetrics(annual){ return annualRows(annual).map(r => ({date:r.year, rank_ic:r.rank_ic, group_spread:r.group_spread, coverage:r.coverage})); }
function renderICChartFromFactor(x, m){
  const wf = firstObj(x.walk_forward, (m||{}).walk_forward);
  let curve = firstArray(x.ic_series, splitPart(m||{}, 'test').ic_series, splitPart(m||{}, 'full').ic_series).map(r => ({date:r.date || r.year || '', rank_ic:firstValue(r.rank_ic, r.ic)}));
  let series = [{key:'rank_ic', label:'RankIC', color:'#2f5f9f'}];
  if(!curve.length && Array.isArray(wf.windows) && wf.windows.length){
    curve = wf.windows.map((r,i)=>({date:r.test_period || r.test || String(i+1), rank_ic:firstValue(r.test_rank_ic, r.test_ic), train_ic:firstValue(r.train_rank_ic, r.train_ic)}));
    series = [{key:'train_ic', label:'训练 RankIC', color:'#58724f'}, {key:'rank_ic', label:'测试 RankIC', color:'#2f5f9f'}];
  }
  if(!curve.length){
    curve = annualCurveFromMetrics(firstArray(x.annual_summary, (m||{}).annual, splitPart(m||{}, 'test').annual_summary, splitPart(m||{}, 'full').annual_summary));
    series = [{key:'rank_ic', label:'年度 RankIC', color:'#2f5f9f'}, {key:'group_spread', label:'分组收益差', color:'#b7791f'}];
  }
  renderLineChart('icChart', curve, series, 'IC 时序', false);
}
function heatColor(v){ const n = finite(v, 0); if(n >= 0.06) return '#245d4d'; if(n >= 0.03) return '#4f8b74'; if(n >= 0.00) return '#dcece5'; if(n >= -0.03) return '#f4dfd8'; return '#c45a4a'; }
function renderAnnualHeatmap(annual){
  const rows = annualRows(annual);
  if(!rows.length){ document.getElementById('annualHeatmap').innerHTML = '<div class="empty">暂无年度热力数据；新任务完成后会展示年度 RankIC、分组收益差和覆盖率。</div>'; return; }
  const cells = rows.map(r => `<div class="heat-row"><b>${esc(r.year)}</b>${['rank_ic','group_spread','coverage'].map(k => `<span class="heat-cell" style="background:${heatColor(k==='coverage' ? r[k]-0.9 : r[k])}" title="${esc(k)}">${k==='coverage' ? pct(r[k],2) : num(r[k],2)}</span>`).join('')}</div>`).join('');
  document.getElementById('annualHeatmap').innerHTML = `<div class="heat-row"><b>年份</b><span>RankIC</span><span>分组收益差</span><span>覆盖率</span></div><div class="heatmap">${cells}</div><div class="hint">颜色越深代表该年度信号越强；红色代表衰减或负贡献。</div>`;
}
function reportName(x){ return x.chinese_name || x.name || x.factor || '未命名因子'; }
function reportAccepted(x){ return Boolean(x.accepted) || x.status === 'accepted' || ['both','market_neutral','long_only'].includes(x.accepted_type); }
function reportStatus(x){ return x.accepted_type_cn || (reportAccepted(x) ? '通过' : '未通过'); }
function reportScore(x){ return x.reward_score ?? x.reward ?? x.composite_score ?? 0; }
function reportCorr(x){ return x.redundancy_max_abs_corr ?? x.max_abs_corr_to_other_factor ?? x.redundancy ?? 0; }
function metricCn(k){
  const m = {train_ic:'训练 IC', valid_ic:'验证 IC', test_ic:'测试 IC', test_group_spread:'测试分组收益差', valid_monotonicity:'验证单调性', walk_forward_ic:'滚动样本外 IC', walk_forward_stability:'滚动稳定性', purged_kfold_ic:'隔离 K 折 IC', purged_kfold_stability:'隔离 K 折稳定性', long_only_excess_return:'多头超额收益', long_only_information_ratio:'多头信息比率', market_neutral_sharpe:'市场中性夏普', market_neutral_return:'市场中性收益', market_neutral_win_rate:'市场中性胜率', market_neutral_gate_bonus:'市场中性通过奖励', long_only_gate_bonus:'多头通过奖励', novelty_penalty:'新颖性惩罚', redundancy:'冗余相关', sealed_search_evidence_probability:'封存搜索证据概率', test_signal_probability:'测试信号概率', test_incremental_residual_probability:'测试残差增量概率', test_downstream_synergy_probability:'测试组合贡献概率', test_economic_realization_probability:'测试收益兑现概率', joint_positive_probability:'联合后验概率', posterior_log_odds_utility:'后验赔率效用', novelty_log_prior:'新颖性先验', drawdown_log_prior:'回撤先验'};
  return m[k] || k;
}
function statusBadge(label, ok){ return `<div class="acceptance-pill ${ok ? 'pass':'fail'}"><b>${esc(label)}</b><span>${ok ? '通过':'未通过'}</span></div>`; }
function typeFormula(latex){
  const box = document.getElementById('formula');
  if(!latex){ box.innerHTML = '<div class="empty">暂无因子公式。</div>'; return; }
  box.innerHTML = `<div class="math-display">\\\\[${esc(latex)}\\\\]</div><details class="formula-source"><summary>查看 LaTeX 源码</summary><code>${esc(latex)}</code></details>`;
  if(window.MathJax && window.MathJax.typesetPromise){ window.MathJax.typesetPromise([box]).catch(()=>{}); }
}
function renderCandidateTable(){
  const target = document.getElementById('candidateTable');
  if(!currentReports.length){ target.innerHTML = '<div class="empty">暂无候选因子。</div>'; return; }
  const selected = Number(document.getElementById('factorSelect').value || 0);
  const cards = currentReports.map((x,i)=>`<div class="factor-card ${i===selected?'active':''}" onclick="document.getElementById('factorSelect').value='${i}';renderFactor(${i});">
    <h4>${i+1}. ${esc(reportName(x))}</h4>
    <div class="status"><span class="pill ${reportAccepted(x)?'good':'bad'}">${esc(reportStatus(x))}</span><span class="pill ${x.lifecycle_production_ready?'good':''}">${esc(productionCn(x))}</span><span class="pill">${esc(cnChannel(x.channel))}</span></div>
    <div class="kv"><span>测试RankIC<b>${num(x.test_rank_ic,2)}</b></span><span>验证残差IC<b>${num(x.valid_incremental_residual_rank_ic,2)}</b></span><span>多空年化<b>${pct(x.test_long_short_annual_return,2)}</b></span><span>最终联合后验<b>${pct(x.posterior_final_joint_positive_probability,2)}</b></span></div>
    <p class="muted">${esc(lifecycleCn(x.lifecycle_state))} · ${esc(x.diagnosis_cn || '')}</p>
  </div>`).join('');
  target.innerHTML = `<h3>候选因子池</h3><div class="factor-cards">${cards}</div>`;
}
function foldCards(title, rows){ rows = rows || []; if(!rows.length) return `<div class="empty">${esc(title)}暂无明细。</div>`; return `<div class="fold-cards">${rows.map((r,i)=>`<div class="fold-card"><span>${esc(title)} ${i+1}</span><b>${num(r.test_rank_ic ?? r.test_ic,2)}</b><small>训练 IC ${num(r.train_rank_ic ?? r.train_ic,2)}</small></div>`).join('')}</div>`; }
function renderAttributionCards(breakdown){ const rows = Object.entries(breakdown||{}).sort((a,b)=>Math.abs(Number(b[1]))-Math.abs(Number(a[1]))).slice(0,8); if(!rows.length) return '<div class="empty">暂无评分拆解。</div>'; return `<div class="cards">${rows.map(([k,v])=>`<div class="metric"><b>${esc(metricCn(k))}</b><span>${num(v,2)}</span></div>`).join('')}</div>`; }
function renderFactor(i){
  const x = currentReports[i];
  if(!x){ clearResultPanels(); return; }
  const m = x.metrics || {};
  const parts = backtestParts(x, m);
  document.getElementById('factorSelect').innerHTML = currentReports.map((r,idx)=>`<option value="${idx}" ${idx===i?'selected':''}>${idx+1}. ${esc(reportName(r))} [${esc(reportStatus(r))}]</option>`).join('');
  document.getElementById('factorSelect').onchange = () => renderFactor(Number(document.getElementById('factorSelect').value || 0));
  document.getElementById('factorMeta').innerHTML = [['编号', i+1], ['搜索通道', cnChannel(x.channel)], ['结构分岛', x.quality_diversity_island || '未标注'], ['统计状态', reportStatus(x)], ['生命周期', lifecycleCn(x.lifecycle_state)], ['生产资格', productionCn(x)], ['部署置信度', pct(x.lifecycle_deployment_confidence,2)], ['复杂度', num(x.complexity,2)], ['数据域', x.data_scope || (x.data_fields||[]).join(' x ')]].map(v=>`<span class="pill">${esc(v[0])}：${esc(v[1])}</span>`).join('');
  document.getElementById('construction').textContent = `${x.construction || ''} 经济假设：${x.hypothesis || ''}`;
  typeFormula(x.latex_formula || x.formula || '');
  renderSummaryTable(x, m);
  const inc = firstObj(x.incremental_evidence, m.incremental_evidence);
  const reg = firstObj(x.regime_evidence, m.regime_evidence);
  const searchPost = firstObj(x.posterior_search_evidence, m.posterior_search_evidence);
  const finalPost = firstObj(x.posterior_final_evidence, m.posterior_final_evidence);
  const incValid = firstObj(inc.valid), incTest = firstObj(inc.test), regValid = firstObj(reg.valid);
  document.getElementById('metricCards').innerHTML = `<div class="acceptance-strip">${statusBadge('搜索可靠性', !!x.search_reliability_pass)}${statusBadge('后验研究证据', !!x.posterior_search_pass)}${statusBadge('独立增量', firstValue(incValid.residual_posterior?.positive_probability, 0) > 0.5)}${statusBadge('市场中性', !!x.market_neutral_pass || x.accepted_type==='market_neutral' || x.accepted_type==='both')}${statusBadge('多头增强', !!x.long_only_pass || x.accepted_type==='long_only' || x.accepted_type==='both')}${statusBadge('新颖性约束', x.novelty_pass !== false)}${statusBadge('生命周期健康', x.lifecycle_state==='healthy')}${statusBadge('生产就绪', !!x.lifecycle_production_ready)}</div><div class="cards"><div class="metric"><b>测试RankIC</b><span>${num(x.test_rank_ic,2)}</span></div><div class="metric"><b>验证残差RankIC</b><span>${num(firstValue(incValid.residual_rank_ic,x.valid_incremental_residual_rank_ic),2)}</span></div><div class="metric"><b>测试残差RankIC</b><span>${num(firstValue(incTest.residual_rank_ic,x.test_incremental_residual_rank_ic),2)}</span></div><div class="metric"><b>验证组合边际</b><span>${num(firstValue(incValid.marginal_rank_ic_gain,x.valid_downstream_marginal_rank_ic_gain),2)}</span></div><div class="metric"><b>多空年化收益</b><span>${pct(parts.ls.annual_return ?? x.test_long_short_annual_return,2)}</span></div><div class="metric"><b>多空夏普</b><span>${num(parts.ls.sharpe ?? x.test_long_short_sharpe,2)}</span></div><div class="metric"><b>状态正向广度</b><span>${pct(firstValue(regValid.posterior_positive_breadth,x.valid_regime_positive_breadth),2)}</span></div><div class="metric"><b>最弱状态下界</b><span>${num(firstValue(regValid.worst_state_lower_90,x.valid_worst_regime_lower_90),2)}</span></div><div class="metric"><b>训练验证联合后验</b><span>${pct(firstValue(searchPost.joint_positive_probability,x.posterior_joint_positive_probability),2)}</span></div><div class="metric"><b>最终联合后验</b><span>${pct(firstValue(finalPost.joint_positive_probability,x.posterior_final_joint_positive_probability),2)}</span></div><div class="metric"><b>DSR置信度</b><span>${pct(x.test_long_short_deflated_sharpe_confidence,2)}</span></div><div class="metric"><b>部署置信度</b><span>${pct(x.lifecycle_deployment_confidence,2)}</span></div></div>`;  const splits = ['train','valid','test','full'];
  const splitName = {train:'训练期', valid:'验证期', test:'测试期', full:'全窗口'};
  document.getElementById('splitTable').innerHTML = `<div class="result-band">${splits.map(s=>{ const z=m[s]||{}, bt=z.backtest||{}, lo=bt.long_only||z.long_only||{}, ls=bt.long_short||z.long_short||{}; return `<div class="metric"><b>${splitName[s]}</b><span>${num(z.rank_ic,2)}</span><small>分组差 ${pct(z.group_spread,2)} · 多头 ${pct(lo.annual_return,2)} · 多空 ${pct(ls.annual_return,2)}</small></div>`; }).join('')}</div>`;
  const groupsObj = x.group_returns || (m.test||{}).group_returns || (m.full||{}).group_returns || [];
  const groups = Array.isArray(groupsObj) ? groupsObj.map(Number) : Object.values(groupsObj).map(Number);
  const minv = groups.length ? Math.min(...groups) : 0, maxv = groups.length ? Math.max(...groups) : 0;
  document.getElementById('groupBars').innerHTML = groups.length ? groups.map((g,idx)=>{ const width = maxv === minv ? 50 : 8 + 84 * (g - minv) / (maxv - minv); return `<div class="barrow"><span>第${idx+1}组</span><div class="track"><div class="fill" style="width:${width}%"></div></div><span>${pct(g,2)}</span></div>`; }).join('') : '<div class="empty">暂无分组收益数据。</div>';
  renderLineChart('navChart', parts.curve, [{key:'long_nav', label:'多头净值', color:'#1b4d89'}, {key:'benchmark_nav', label:'基准净值', color:'#777777'}, {key:'long_short_nav', label:'多空净值', color:'#2f7d57'}], '组合净值', false);
  renderLineChart('drawdownChart', parts.curve, [{key:'long_drawdown', label:'多头回撤', color:'#1b4d89'}, {key:'benchmark_drawdown', label:'基准回撤', color:'#777777'}, {key:'long_short_drawdown', label:'多空回撤', color:'#9d3b3b'}], '回撤', true);
  renderICChartFromFactor(x, m);
  const annualSource = firstArray(x.annual_summary, m.annual, (m.test||{}).annual_summary, (m.full||{}).annual_summary);
  renderAnnualHeatmap(annualSource);
  const annual = annualRows(annualSource);
  const curveByYear = {};
  parts.curve.forEach(point => { const year = String(point.date || '').slice(0,4); if(year) (curveByYear[year] ||= []).push(point); });
  annual.forEach(row => {
    const points = curveByYear[row.year] || [];
    const compound = key => points.length ? points.reduce((nav,p)=>nav * (1 + finite(p[key],0)), 1) - 1 : 0;
    if(points.length){ row.long_return = compound('long_return'); row.benchmark_return = compound('benchmark_return'); row.long_short_return = compound('long_short_return'); }
  });
  document.getElementById('annualTable').innerHTML = annual.length ? `<details class="compact-details"><summary>年度明细表</summary><div class="table-scroll"><table><thead><tr><th>年份</th><th>RankIC</th><th>分组收益差</th><th>多头收益</th><th>基准收益</th><th>多空收益</th></tr></thead><tbody>${annual.map(r=>`<tr><td>${esc(r.year)}</td><td>${num(r.rank_ic,2)}</td><td>${pct(r.group_spread,2)}</td><td>${pct(r.long_return,2)}</td><td>${pct(r.benchmark_return,2)}</td><td>${pct(r.long_short_return,2)}</td></tr>`).join('')}</tbody></table></div></details>` : '<div class="empty">暂无年度明细。</div>';
  const quick = x.quick_screen || m.quick_screen || m.quick || {};
  document.getElementById('quickScreen').innerHTML = `<div class="cards"><div class="metric"><b>是否通过</b><span>${quick.passed || x.quick_passed ? '通过' : '未通过'}</span></div><div class="metric"><b>覆盖率</b><span>${pct(quick.coverage,2)}</span></div><div class="metric"><b>粗排RankIC</b><span>${num(quick.rough_rank_ic,2)}</span></div><div class="metric"><b>IC为正月份占比</b><span>${pct(quick.ic_win_rate,2)}</span></div><div class="metric"><b>极端值占比</b><span>${pct(quick.extreme_ratio,2)}</span></div><div class="metric"><b>截面稳定度</b><span>${pct(quick.stability,2)}</span></div></div>`;
  const wf = x.walk_forward || m.walk_forward || {};
  document.getElementById('walkForward').innerHTML = `<div class="cards"><div class="metric"><b>平均测试 IC</b><span>${num(wf.mean_test_rank_ic ?? wf.mean_test_ic,2)}</span></div><div class="metric"><b>正 IC 窗口占比</b><span>${pct(wf.positive_rate ?? wf.positive_test_ic_ratio,2)}</span></div><div class="metric"><b>平均衰减</b><span>${num(wf.mean_decay,2)}</span></div><div class="metric"><b>窗口数</b><span>${num((wf.windows || []).length,2)}</span></div></div>${foldCards('滚动窗口', wf.windows)}`;
  const pk = x.purged_kfold || m.purged_kfold || {};
  document.getElementById('purgedKfold').innerHTML = `<div class="cards"><div class="metric"><b>平均测试 IC</b><span>${num(pk.mean_test_rank_ic ?? pk.mean_test_ic,2)}</span></div><div class="metric"><b>正 IC 折数</b><span>${pct(pk.positive_rate ?? pk.positive_test_ic_ratio,2)}</span></div><div class="metric"><b>平均衰减</b><span>${num(pk.mean_decay,2)}</span></div><div class="metric"><b>折数</b><span>${num((pk.folds || []).length,2)}</span></div></div>${foldCards('隔离折', pk.folds)}`;
  const attr = x.attribution || m.attribution || {};
  const attrRows = [];
  (attr.industry || []).forEach(r=>attrRows.push(`<tr><td>行业</td><td>${esc(r.bucket)}</td><td>${num(r.rank_ic,2)}</td><td>${num(r.rows,2)}</td></tr>`));
  (attr.size || []).forEach(r=>attrRows.push(`<tr><td>市值分层</td><td>${esc(r.bucket)}</td><td>${num(r.rank_ic,2)}</td><td>${num(r.rows,2)}</td></tr>`));
  (attr.liquidity || []).forEach(r=>attrRows.push(`<tr><td>流动性分层</td><td>${esc(r.bucket)}</td><td>${num(r.rank_ic,2)}</td><td>${num(r.rows,2)}</td></tr>`));
  document.getElementById('attributionTable').innerHTML = attrRows.length ? `<div class="table-scroll"><table><thead><tr><th>维度</th><th>分组</th><th>RankIC</th><th>样本数</th></tr></thead><tbody>${attrRows.join('')}</tbody></table></div>` : renderAttributionCards(x.reward_breakdown || x.score_breakdown || {});
  document.getElementById('scoreCards').innerHTML = renderAttributionCards(x.reward_breakdown || x.score_breakdown || {});
  const plan = x.mutation_plan || [];
  const conclusion = !reportAccepted(x)
    ? '该因子未完全通过统计门槛，进入失败归因与智能变异。'
    : x.lifecycle_production_ready
      ? '该因子通过统计门槛且生命周期健康，可进入受控生产复核。'
      : `该因子通过统计门槛，但当前为${lifecycleCn(x.lifecycle_state)}，仅保留研究资格并按部署置信度缩放。`;
  document.getElementById('diagnosis').innerHTML = `<b>综合结论：</b>${esc(conclusion)}<br><b>通过类型：</b>${esc(reportStatus(x))}<br><b>训练验证研究证据：</b>联合后验 ${pct(firstValue(searchPost.joint_positive_probability,x.posterior_joint_positive_probability),2)}；验证残差 RankIC ${num(firstValue(incValid.residual_rank_ic,x.valid_incremental_residual_rank_ic),2)}；验证组合边际 ${num(firstValue(incValid.marginal_rank_ic_gain,x.valid_downstream_marginal_rank_ic_gain),2)}；状态正向广度 ${pct(firstValue(regValid.posterior_positive_breadth,x.valid_regime_positive_breadth),2)}。<br><b>封存测试报告：</b>最终联合后验 ${pct(firstValue(finalPost.joint_positive_probability,x.posterior_final_joint_positive_probability),2)}；测试残差 RankIC ${num(firstValue(incTest.residual_rank_ic,x.test_incremental_residual_rank_ic),2)}。测试证据不进入父代选择。<br><b>生命周期：</b>${esc(lifecycleCn(x.lifecycle_state))}；联合衰减概率 ${pct(x.lifecycle_joint_deterioration_probability,2)}；部署置信度 ${pct(x.lifecycle_deployment_confidence,2)}。<br><b>搜索归因：</b>${esc(x.search_diagnosis_cn || '')}<br><b>最终归因：</b>${esc(x.diagnosis_cn || '')}<br><b>因子血缘：</b>${esc(lineageSummary(x))}${plan.length?`<h3>智能变异方向</h3><ul>${plan.map(p=>`<li>${esc(p)}</li>`).join('')}</ul>`:''}`;
  renderCandidateTable();
}
function renderJob(d){
  d = d || {};
  const status = d.status || 'failed';
  const progressIndex = flowIndexFromProgress(d);
  const bar = document.getElementById('pbar');
  renderFlow(progressIndex);
  bar.className = 'progress-fill';
  bar.style.width = `${Math.max(4, Math.min(100, progressIndex / defaultFlow.length * 100))}%`;

  if(status === 'failed'){
    bar.style.width = '100%';
    document.getElementById('runline').innerHTML = `<span class="pill bad">任务失败</span><span class="pill">${esc(d.id || '')}</span><span>${esc(d.progress || d.message || '')}</span><div class="error-text">${esc(d.error || d.message || '未返回错误详情。')}</div>`;
    return;
  }
  if(status === 'queued' || status === 'running'){
    document.getElementById('runline').innerHTML = `<span class="pill good">${status === 'queued' ? '已排队' : '运行中'}</span><span class="pill">任务：${esc(d.id || '')}</span><span class="pill">耗时：${fmtElapsed(d.elapsed_seconds)}</span><span>${esc(d.progress || '')}</span>`;
    return;
  }
  if(status !== 'done'){
    document.getElementById('runline').textContent = d.progress || '等待任务状态。';
    return;
  }

  const result = d.result || {};
  const initialAudit = result.initial_llm_generation_audit || {};
  const trainAudit = (result.split_audit || {}).train || {};
  const validAudit = (result.split_audit || {}).valid || {};
  const testAudit = (result.split_audit || {}).test || {};
  const runtime = result.runtime_audit || {};
  const panelCache = result.panel_cache_audit || runtime.panel_cache || {};
  currentReports = arr(result.factor_reports);
  bar.style.width = '100%';
  renderFlow(defaultFlow.length);
  document.getElementById('runline').innerHTML = [
    ['模型版本', result.model_version || result.run_id || '未知'],
    ['任务', d.id || result.run_id || ''],
    ['耗时', fmtElapsed(d.elapsed_seconds)],
    ['样本', num(result.rows, 2)],
    ['调仓月', num(result.months, 2)],
    ['候选', num(result.candidate_count, 2)],
    ['统计通过', num(result.accepted_count, 2)],
    ['生产就绪', num(result.production_ready_count, 2)],
    ['生产确认', num(result.production_confirmed_count, 2)],
    ['GPT有效槽位', `${num(initialAudit.valid_candidate_count ?? result.initial_llm_candidate_count, 2)}/${num(initialAudit.requested_candidate_count || result.initial_llm_candidate_count || 1, 2)}`],
    ['GPT生成', fmtElapsed(runtime.llm_generation_seconds)],
    ['数据构建', fmtElapsed(runtime.panel_build_seconds)],
    ['面板缓存', cacheStatusCn(panelCache.status)],
    ['候选池', fmtElapsed(Number(runtime.initial_pool_build_seconds || 0) + Number(runtime.expanded_pool_build_seconds || 0))],
    ['候选检验', fmtElapsed(runtime.candidate_evaluation_seconds)],
    ['训练/验证/测试月', `${num(trainAudit.months,2)} / ${num(validAudit.months,2)} / ${num(testAudit.months,2)}`]
  ].map(v=>`<span class="pill good">${esc(v[0])}：${esc(v[1])}</span>`).join('') + `<div class="hint">停止原因：${esc(stopReasonCn(result.stop_reason))}</div>`;
  if(currentReports.length){
    renderFactor(0);
  } else {
    clearResultPanels();
    document.getElementById('diagnosis').textContent = '任务完成，但没有可展示的候选因子。';
  }
}
async function poll(id){
  const r = await fetch(API_BASE + '/api/job/' + id);
  const d = await r.json();
  renderJob(d);
  if(d.status === 'queued' || d.status === 'running') setTimeout(()=>poll(id), 2500);
  else document.getElementById('start').disabled = false;
}
document.getElementById('start').onclick = async () => {
  if(!currentUser){
    document.getElementById('authMsg').textContent = '请先登录或注册后再开始挖掘。';
    return;
  }
  document.getElementById('start').disabled = true;
  clearResultPanels();
  document.getElementById('runline').textContent = '任务已提交，正在进入 GPT 候选生成和因子检验流水线。';
  renderFlow(1);
  document.getElementById('pbar').className = 'progress-fill';
  document.getElementById('pbar').style.width = '14%';
  const payload = {
    universe: document.getElementById('universe').value,
    max_months: document.getElementById('months').value,
    target_accepted: document.getElementById('target').value,
    max_candidates: document.getElementById('maxc').value,
    iterations: document.getElementById('iterations').value,
    budget_per_channel: document.getElementById('budget').value
  };
  const r = await fetch(API_BASE + '/api/job/start', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  const d = await r.json();
  if(!r.ok){ renderJob(d); document.getElementById('start').disabled=false; return; }
  renderJob(d);
  poll(d.id);
};
document.getElementById('loginBtn').onclick = () => authPost('/api/auth/login');
document.getElementById('registerBtn').onclick = () => authPost('/api/auth/register');
document.getElementById('logoutBtn').onclick = logout;
document.getElementById('refreshHistory').onclick = loadHistory;
document.getElementById('authPass').addEventListener('keydown', e => {
  if(e.key === 'Enter') authPost('/api/auth/login');
});
renderFlow(0);
loadStatus();
loadMe();
setInterval(loadStatus, 10000);
</script>
</body>
</html>
"""


@app.get("/api/status")
@app.get("/factor-mining/api/status")
def status():
    return jsonify(public_config())


@app.get("/api/auth/me")
@app.get("/factor-mining/api/auth/me")
def auth_me():
    user = current_user()
    return jsonify({"authenticated": bool(user), "user": user})


@app.post("/api/auth/register")
@app.post("/factor-mining/api/auth/register")
def auth_register():
    payload = request.get_json(silent=True) or {}
    username = normalize_username(payload.get("username"))
    password = str(payload.get("password") or "")
    if not username:
        return jsonify({"status": "failed", "message": "用户名需为3-32位字母、数字、下划线或横线。"}), 400
    if len(password) < 6:
        return jsonify({"status": "failed", "message": "密码至少6位。"}), 400
    salt, digest = hash_password(password)
    try:
        with state_conn() as conn:
            cur = conn.execute(
                "INSERT INTO users(username, password_hash, salt, created_at) VALUES (?, ?, ?, ?)",
                (username, digest, salt, time.strftime("%Y-%m-%d %H:%M:%S")),
            )
            uid = cur.lastrowid
    except sqlite3.IntegrityError:
        return jsonify({"status": "failed", "message": "用户名已存在。"}), 409
    session["user_id"] = uid
    return jsonify({"status": "ok", "user": {"id": uid, "username": username}})


@app.post("/api/auth/login")
@app.post("/factor-mining/api/auth/login")
def auth_login():
    payload = request.get_json(silent=True) or {}
    username = normalize_username(payload.get("username"))
    password = str(payload.get("password") or "")
    with state_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone() if username else None
    if not row or not verify_password(password, row["salt"], row["password_hash"]):
        return jsonify({"status": "failed", "message": "用户名或密码错误。"}), 401
    session["user_id"] = int(row["id"])
    return jsonify({"status": "ok", "user": {"id": row["id"], "username": row["username"]}})


@app.post("/api/auth/logout")
@app.post("/factor-mining/api/auth/logout")
def auth_logout():
    session.clear()
    return jsonify({"status": "ok"})


@app.get("/api/history")
@app.get("/factor-mining/api/history")
def history_list():
    user, err = require_user_json()
    if err:
        return err
    with state_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM job_history WHERE user_id=? ORDER BY id DESC LIMIT 50",
            (user["id"],),
        ).fetchall()
    personal = []
    for row in rows:
        summary = json.loads(row["summary_json"] or "{}")
        summary["source"] = "account"
        personal.append(summary)
    return jsonify({"status": "ok", "account_history": personal, "server_runs": server_run_summaries(50)})


@app.get("/api/history/<job_id>")
@app.get("/factor-mining/api/history/<job_id>")
def history_detail(job_id):
    user, err = require_user_json()
    if err:
        return err
    with state_conn() as conn:
        row = conn.execute(
            "SELECT * FROM job_history WHERE user_id=? AND job_id=?",
            (user["id"], job_id),
        ).fetchone()
    server_result = read_server_run(job_id)
    elapsed_seconds = float(row["elapsed_seconds"] or 0.0) if row else 0.0
    if row and row["result_json"]:
        if server_result:
            return jsonify({"status": "ok", "job_id": job_id, "source": "account_server_run", "elapsed_seconds": elapsed_seconds, "result": server_result})
        return jsonify({"status": "ok", "job_id": job_id, "source": "account", "elapsed_seconds": elapsed_seconds, "result": json.loads(row["result_json"])})
    if server_result:
        return jsonify({"status": "ok", "job_id": job_id, "source": "server_run", "elapsed_seconds": elapsed_seconds, "result": server_result})
    return jsonify({"status": "failed", "message": "历史记录不存在。"}), 404


@app.get("/api/history/<job_id>/stock")
@app.get("/factor-mining/api/history/<job_id>/stock")
def history_stock_diagnostics(job_id):
    user, err = require_user_json()
    if err:
        return err
    source = read_server_run_source(job_id)
    if not source:
        return jsonify({"status": "failed", "message": "该历史任务没有可查询的服务端诊断文件。"}), 404
    run_dir, _result_file, result = source
    diagnostics_map = dict(result.get("stock_diagnostics") or {})
    factor = str(request.args.get("factor") or next(iter(diagnostics_map), ""))
    diagnostics = diagnostics_map.get(factor)
    if not isinstance(diagnostics, dict):
        return jsonify({"status": "failed", "message": "该因子尚未生成个股诊断。"}), 404
    code = str(request.args.get("code") or "").strip().upper()
    clean_code = "".join(ch for ch in code if ch.isalnum() or ch in {".", "_", "-"})
    if not code or clean_code != code:
        return jsonify({"status": "failed", "message": "股票代码格式无效。"}), 400
    runtime_name = str(diagnostics.get("runtime_file") or "")
    if not runtime_name or Path(runtime_name).name != runtime_name:
        return jsonify({"status": "failed", "message": "诊断运行时文件引用无效。"}), 409
    runtime_path = (run_dir / runtime_name).resolve()
    if runtime_path.parent != run_dir.resolve() or not runtime_path.exists():
        return jsonify({"status": "failed", "message": "诊断运行时文件不存在。"}), 404
    model_script = Path(os.environ.get("FACTOR_MINING_SCRIPT", str(DEFAULT_MINER))).resolve()
    diagnostics_script = Path(
        os.environ.get("FACTOR_DIAGNOSTICS_SCRIPT", str(model_script.with_name("factor_diagnostics.py")))
    ).resolve()
    try:
        module = load_diagnostics_module(diagnostics_script)
        payload = module.stock_payload(
            runtime_path,
            diagnostics,
            code,
            str(request.args.get("universe") or "ALL_A").upper(),
            str(request.args.get("frequency") or "W").upper(),
        )
    except KeyError:
        return jsonify({"status": "failed", "message": "该股票不在诊断运行时样本中。"}), 404
    except Exception as exc:
        return jsonify({"status": "failed", "message": f"个股诊断读取失败：{str(exc)[-500:]}"}), 500
    return jsonify({"status": "ok", "job_id": job_id, "factor": factor, "result": payload})


@app.post("/api/job/start")
@app.post("/factor-mining/api/job/start")
def start_job():
    user, err = require_user_json()
    if err:
        return err
    ip = client_ip()
    if not check_rate_limit(ip):
        return jsonify({"status": "failed", "progress": "触发频率限制，每小时最多5次。"}), 429
    payload = request.get_json(silent=True) or {}
    job = create_job(payload, user)
    with job_lock:
        running = [x for x in jobs.values() if x["status"] in {"queued", "running"}]
        if running:
            response = {"status": "failed", "progress": "已有任务正在运行，请稍后再试。"}
            if running[0].get("user_id") == user["id"]:
                response["active_job"] = running[0]["id"]
            return jsonify(response), 429
        jobs[job["id"]] = job
        upsert_history(job)
    thread = threading.Thread(target=run_job, args=(job["id"],), daemon=True)
    thread.start()
    return jsonify(public_job_view(job))


@app.get("/api/job/<job_id>")
@app.get("/factor-mining/api/job/<job_id>")
def get_job(job_id):
    user, err = require_user_json()
    if err:
        return err
    with job_lock:
        job = jobs.get(job_id)
        if not job or job.get("user_id") != user["id"]:
            return jsonify({"status": "failed", "progress": "任务不存在"}), 404
        return jsonify(public_job_view(job))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=PORT, threaded=True)
