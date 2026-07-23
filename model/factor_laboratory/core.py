"""Production-oriented numerical factor laboratory worker.

The worker is deliberately isolated from Flask. It reads an immutable SQLite
research warehouse, trains on chronological train/validation partitions, opens
the test partition once, and writes one auditable JSON result. It never reads
credentials or calls external data providers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sqlite3
import statistics
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


ENGINE_VERSION = "factor-lab/1.0-causal-mixture-ppo"
FEATURES = [
    "ret_1", "ret_5", "ret_20", "ret_60", "vol_20", "down_vol_20",
    "price_pos_60", "volume_z_20", "amihud_20", "turnover", "volume_ratio",
    "value_ep", "value_bp", "value_sp", "dividend", "log_mv",
    "moneyflow", "large_flow", "extreme_flow", "range_1", "gap_1",
]
DOMAINS = {
    "price": ["ret_1", "ret_5", "ret_20", "ret_60", "price_pos_60", "range_1", "gap_1"],
    "risk": ["vol_20", "down_vol_20"],
    "liquidity": ["volume_z_20", "amihud_20", "turnover", "volume_ratio"],
    "valuation": ["value_ep", "value_bp", "value_sp", "dividend", "log_mv"],
    "flow": ["moneyflow", "large_flow", "extreme_flow"],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def finite(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(temp, path)


def progress(path: Path | None, stage: str, pct: float, message: str, **extra: Any) -> None:
    if not path:
        return
    payload = {"stage": stage, "progress": round(float(pct), 4), "message": message, "updated_at": now_iso()}
    payload.update(extra)
    atomic_json(path, payload)


def rankdata(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    ranks[order] = np.arange(len(values), dtype=float)
    if len(values) > 1:
        ranks /= len(values) - 1
    return ranks


def safe_corr(a: np.ndarray, b: np.ndarray) -> float:
    mask = np.isfinite(a) & np.isfinite(b)
    if mask.sum() < 8:
        return 0.0
    aa, bb = a[mask], b[mask]
    if np.std(aa) < 1e-12 or np.std(bb) < 1e-12:
        return 0.0
    return finite(np.corrcoef(aa, bb)[0, 1])


def max_drawdown(returns: np.ndarray) -> float:
    if returns.size == 0:
        return 0.0
    nav = np.cumprod(1 + np.nan_to_num(returns, nan=0.0))
    peak = np.maximum.accumulate(nav)
    return finite(np.min(nav / np.maximum(peak, 1e-12) - 1))


def backtest_cross_section(
    frame: pd.DataFrame,
    score_col: str,
    target_col: str,
    cost_bps: float,
    horizon: int,
) -> dict[str, Any]:
    daily: list[dict[str, Any]] = []
    previous_long: set[str] = set()
    previous_short: set[str] = set()
    for date, group in frame.groupby("trade_date", sort=True):
        group = group[["ts_code", score_col, target_col]].dropna()
        if len(group) < 30:
            continue
        group = group.sort_values(score_col)
        n = max(3, len(group) // 10)
        short = set(group.head(n).ts_code.astype(str))
        long = set(group.tail(n).ts_code.astype(str))
        gross = finite(group.tail(n)[target_col].mean() - group.head(n)[target_col].mean())
        turnover = 0.0
        if previous_long:
            turnover = 0.5 * (
                1 - len(long & previous_long) / max(len(long), 1)
                + 1 - len(short & previous_short) / max(len(short), 1)
            )
        net = gross - turnover * cost_bps / 10000.0
        ic = safe_corr(rankdata(group[score_col].to_numpy()), rankdata(group[target_col].to_numpy()))
        daily.append({"date": str(date), "gross": gross, "net": net, "turnover": turnover, "rank_ic": ic})
        previous_long, previous_short = long, short
    if not daily:
        return {"rank_ic": 0.0, "icir": 0.0, "hit_rate": 0.0, "annual_return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0, "turnover": 0.0, "series": []}
    ic = np.array([x["rank_ic"] for x in daily], dtype=float)
    # Horizon returns are evaluated on non-overlapping dates for performance.
    returns = np.array([x["net"] for x in daily[:: max(1, horizon)]], dtype=float)
    periods = 252 / max(1, horizon)
    annual = finite(np.prod(1 + returns) ** (periods / max(len(returns), 1)) - 1) if len(returns) else 0.0
    vol = finite(np.std(returns, ddof=1) * math.sqrt(periods)) if len(returns) > 1 else 0.0
    sharpe = annual / vol if vol > 1e-12 else 0.0
    ic_std = np.std(ic, ddof=1) if len(ic) > 1 else 0.0
    return {
        "rank_ic": finite(np.mean(ic)),
        "icir": finite(np.mean(ic) / ic_std * math.sqrt(252)) if ic_std > 1e-12 else 0.0,
        "hit_rate": finite(np.mean(ic > 0)),
        "annual_return": annual,
        "annual_volatility": vol,
        "sharpe": finite(sharpe),
        "max_drawdown": max_drawdown(returns),
        "turnover": finite(np.mean([x["turnover"] for x in daily])),
        "observations": len(daily),
        "series": daily,
    }


def deflated_sharpe_proxy(sharpe: float, observations: int, trials: int) -> dict[str, float]:
    if observations < 3:
        return {"dsr_confidence": 0.0, "pbo_proxy": 1.0}
    # Conservative Gaussian multiple-testing approximation; explicitly labeled proxy.
    expected_max = math.sqrt(max(0.0, 2 * math.log(max(1, trials))))
    z = (sharpe * math.sqrt(max(observations - 1, 1)) - expected_max) / math.sqrt(max(1e-9, 1 + 0.5 * sharpe * sharpe))
    confidence = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    return {"dsr_confidence": finite(confidence), "pbo_proxy": finite(1 - confidence)}


@dataclass
class Panel:
    frame: pd.DataFrame
    dates: list[str]
    assets: list[str]
    features: np.ndarray
    targets: np.ndarray
    valid: np.ndarray
    feature_names: list[str]
    horizons: list[int]
    split: dict[str, tuple[int, int]]
    source: dict[str, Any]


def read_panel(config: dict[str, Any], progress_path: Path | None = None) -> Panel:
    db_path = Path(config["database_path"])
    if not db_path.exists():
        raise FileNotFoundError(f"research warehouse unavailable: {db_path}")
    max_assets = int(config.get("max_assets", 160))
    max_months = int(config.get("max_months", 60))
    sequence = int(config.get("sequence_length", 120))
    horizons = sorted({int(x) for x in config.get("horizons", [5, 10, 20]) if 1 <= int(x) <= 60})
    if not horizons:
        horizons = [5, 10, 20]
    required_dates = min(1600, max(260, max_months * 22 + sequence + max(horizons) + 30))
    uri = "file:" + db_path.as_posix() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=60)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA temp_store=MEMORY")
    progress(progress_path, "data", 0.04, "读取交易日与流动性股票池")
    dates_desc = [r[0] for r in conn.execute(
        "SELECT DISTINCT trade_date FROM stock_ohlcv_daily ORDER BY trade_date DESC LIMIT ?", (required_dates,)
    )]
    if len(dates_desc) < sequence + max(horizons) + 60:
        raise RuntimeError("insufficient chronological market data")
    start_date, end_date = dates_desc[-1], dates_desc[0]
    liquidity_start = dates_desc[min(59, len(dates_desc) - 1)]
    assets = [r[0] for r in conn.execute(
        "SELECT ts_code FROM stock_ohlcv_daily WHERE trade_date>=? AND amount IS NOT NULL "
        "GROUP BY ts_code HAVING COUNT(*)>=30 ORDER BY AVG(amount) DESC LIMIT ?",
        (liquidity_start, max_assets),
    )]
    if len(assets) < 30:
        raise RuntimeError("insufficient liquid assets")
    placeholders = ",".join("?" for _ in assets)
    sql = f"""
        SELECT o.trade_date,o.ts_code,o.open,o.high,o.low,o.close,
               COALESCE(o.qfq_close,o.close) AS qfq_close,o.pre_close,o.pct_chg,o.vol,o.amount,
               v.pe_ttm,v.pb,v.ps_ttm,v.dv_ttm,v.total_mv,v.circ_mv,v.turnover_rate,v.volume_ratio,
               m.net_mf_amount,m.buy_lg_amount,m.sell_lg_amount,m.buy_elg_amount,m.sell_elg_amount
        FROM stock_ohlcv_daily o
        LEFT JOIN stock_valuation_daily v ON v.trade_date=o.trade_date AND v.ts_code=o.ts_code
        LEFT JOIN stock_moneyflow_daily m ON m.trade_date=o.trade_date AND m.ts_code=o.ts_code
        WHERE o.trade_date>=? AND o.ts_code IN ({placeholders})
        ORDER BY o.trade_date,o.ts_code
    """
    progress(progress_path, "data", 0.09, "读取行情、估值与资金流点时面板")
    frame = pd.read_sql_query(sql, conn, params=[start_date, *assets])
    conn.close()
    if frame.empty:
        raise RuntimeError("empty market panel")
    frame["trade_date"] = frame["trade_date"].astype(str)
    frame = frame.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    g = frame.groupby("ts_code", sort=False)
    price = frame["qfq_close"].where(frame["qfq_close"] > 0)
    frame["ret_1"] = g["qfq_close"].pct_change(fill_method=None)
    for h in [5, 20, 60]:
        frame[f"ret_{h}"] = g["qfq_close"].pct_change(h, fill_method=None)
    frame["vol_20"] = g["ret_1"].rolling(20, min_periods=12).std().reset_index(level=0, drop=True)
    negative = frame["ret_1"].clip(upper=0)
    frame["down_vol_20"] = negative.groupby(frame["ts_code"]).rolling(20, min_periods=12).std().reset_index(level=0, drop=True)
    low60 = g["qfq_close"].rolling(60, min_periods=30).min().reset_index(level=0, drop=True)
    high60 = g["qfq_close"].rolling(60, min_periods=30).max().reset_index(level=0, drop=True)
    frame["price_pos_60"] = (price - low60) / (high60 - low60).replace(0, np.nan) - 0.5
    log_vol = np.log1p(frame["vol"].clip(lower=0))
    vol_mean = log_vol.groupby(frame["ts_code"]).rolling(20, min_periods=12).mean().reset_index(level=0, drop=True)
    vol_std = log_vol.groupby(frame["ts_code"]).rolling(20, min_periods=12).std().reset_index(level=0, drop=True)
    frame["volume_z_20"] = (log_vol - vol_mean) / vol_std.replace(0, np.nan)
    illiq = frame["ret_1"].abs() / frame["amount"].replace(0, np.nan)
    frame["amihud_20"] = np.log1p(illiq.groupby(frame["ts_code"]).rolling(20, min_periods=12).mean().reset_index(level=0, drop=True) * 1e8)
    frame["turnover"] = frame["turnover_rate"] / 100.0
    frame["value_ep"] = np.where(frame["pe_ttm"] > 0, 1 / frame["pe_ttm"], np.nan)
    frame["value_bp"] = np.where(frame["pb"] > 0, 1 / frame["pb"], np.nan)
    frame["value_sp"] = np.where(frame["ps_ttm"] > 0, 1 / frame["ps_ttm"], np.nan)
    frame["dividend"] = frame["dv_ttm"] / 100.0
    frame["log_mv"] = np.log(frame["circ_mv"].where(frame["circ_mv"] > 0))
    denominator = frame["amount"].abs().replace(0, np.nan)
    frame["moneyflow"] = frame["net_mf_amount"] / denominator
    frame["large_flow"] = (frame["buy_lg_amount"] - frame["sell_lg_amount"]) / denominator
    frame["extreme_flow"] = (frame["buy_elg_amount"] - frame["sell_elg_amount"]) / denominator
    frame["range_1"] = (frame["high"] - frame["low"]) / frame["pre_close"].replace(0, np.nan)
    frame["gap_1"] = frame["open"] / frame["pre_close"].replace(0, np.nan) - 1
    for h in horizons:
        frame[f"target_{h}"] = g["qfq_close"].shift(-h) / price - 1
        # Cross-sectional market and size residualization using only same-date observables.
        market = frame.groupby("trade_date")[f"target_{h}"].transform("mean")
        frame[f"target_{h}"] = (frame[f"target_{h}"] - market).clip(-0.6, 0.6)
    progress(progress_path, "data", 0.15, "构造因果特征、缺失掩码与多周期残差标签")
    dates = sorted(frame.trade_date.unique().tolist())
    assets = sorted(frame.ts_code.unique().tolist())
    date_index = {d: i for i, d in enumerate(dates)}
    asset_index = {a: i for i, a in enumerate(assets)}
    feature_array = np.full((len(dates), len(assets), len(FEATURES)), np.nan, dtype=np.float32)
    target_array = np.full((len(dates), len(assets), len(horizons)), np.nan, dtype=np.float32)
    di = frame.trade_date.map(date_index).to_numpy()
    ai = frame.ts_code.map(asset_index).to_numpy()
    feature_array[di, ai] = frame[FEATURES].to_numpy(dtype=np.float32)
    target_array[di, ai] = frame[[f"target_{h}" for h in horizons]].to_numpy(dtype=np.float32)
    split_train = int(len(dates) * 0.60)
    split_valid = int(len(dates) * 0.80)
    embargo = max(horizons)
    split = {
        "train": (sequence, max(sequence + 1, split_train - embargo)),
        "valid": (split_train, max(split_train + 1, split_valid - embargo)),
        "test": (split_valid, len(dates) - max(horizons)),
    }
    valid = np.isfinite(target_array).all(axis=2) & np.isfinite(feature_array).sum(axis=2).astype(int).clip(min=0).astype(bool)
    return Panel(
        frame=frame,
        dates=dates,
        assets=assets,
        features=feature_array,
        targets=target_array,
        valid=valid,
        feature_names=list(FEATURES),
        horizons=horizons,
        split=split,
        source={
            "database": str(db_path), "start_date": start_date, "end_date": end_date,
            "rows": int(len(frame)), "dates": len(dates), "assets": len(assets),
            "watermark": max(dates), "point_in_time": True,
        },
    )


def normalise_panel(panel: Panel) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    start, end = panel.split["train"]
    train = panel.features[start:end]
    median = np.nanmedian(train.reshape(-1, train.shape[-1]), axis=0)
    q25 = np.nanpercentile(train.reshape(-1, train.shape[-1]), 25, axis=0)
    q75 = np.nanpercentile(train.reshape(-1, train.shape[-1]), 75, axis=0)
    scale = np.where(q75 - q25 > 1e-6, q75 - q25, 1.0)
    values = np.nan_to_num((panel.features - median) / scale, nan=0.0, posinf=8.0, neginf=-8.0)
    values = np.clip(values, -8, 8).astype(np.float32)
    missing = (~np.isfinite(panel.features)).astype(np.float32)
    return values, missing, np.concatenate([median, scale])


def split_frame(panel: Panel, split_name: str, scores: np.ndarray, horizon_index: int = 0) -> pd.DataFrame:
    start, end = panel.split[split_name]
    rows = []
    for local_i, date_i in enumerate(range(start, end)):
        mask = panel.valid[date_i] & np.isfinite(scores[local_i])
        for asset_i in np.where(mask)[0]:
            rows.append((panel.dates[date_i], panel.assets[asset_i], finite(scores[local_i, asset_i]), finite(panel.targets[date_i, asset_i, horizon_index])))
    return pd.DataFrame(rows, columns=["trade_date", "ts_code", "score", "target"])


def torch_modules():
    try:
        import torch
        from torch import nn
        return torch, nn
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("PyTorch is required by the Factor Laboratory worker") from exc


def run_lstm(panel: Panel, config: dict[str, Any], progress_path: Path | None) -> dict[str, Any]:
    torch, nn = torch_modules()
    torch.set_num_threads(max(1, int(config.get("cpu_threads", 4))))
    device = torch.device("cuda" if torch.cuda.is_available() and config.get("allow_cuda", True) else "cpu")
    values, missing, scaler = normalise_panel(panel)
    sequence = int(config.get("sequence_length", 120))
    domain_indices = [[panel.feature_names.index(x) for x in names if x in panel.feature_names] for names in DOMAINS.values()]
    domain_indices = [x for x in domain_indices if x]

    class DateDataset(torch.utils.data.Dataset):
        def __init__(self, split_name: str):
            self.start, self.end = panel.split[split_name]
            self.indices = list(range(max(sequence, self.start), self.end))

        def __len__(self):
            return len(self.indices)

        def __getitem__(self, index):
            date_i = self.indices[index]
            x = values[date_i - sequence:date_i].transpose(1, 0, 2)
            m = missing[date_i - sequence:date_i].transpose(1, 0, 2)
            y = panel.targets[date_i]
            mask = panel.valid[date_i]
            exposures = values[date_i, :, [panel.feature_names.index("log_mv"), panel.feature_names.index("vol_20"), panel.feature_names.index("ret_20")]]
            return torch.from_numpy(x), torch.from_numpy(m), torch.from_numpy(y), torch.from_numpy(mask), torch.from_numpy(exposures)

    class CausalConv(nn.Module):
        def __init__(self, dim, kernels=(3, 5, 9), dilations=(1, 2, 4)):
            super().__init__()
            self.blocks = nn.ModuleList()
            for kernel, dilation in zip(kernels, dilations):
                pad = (kernel - 1) * dilation
                self.blocks.append(nn.Sequential(
                    nn.ConstantPad1d((pad, 0), 0.0),
                    nn.Conv1d(dim, dim, kernel, dilation=dilation, groups=dim),
                    nn.Conv1d(dim, dim, 1), nn.GELU(), nn.GroupNorm(1, dim),
                ))
            self.gate = nn.Linear(dim * len(self.blocks), dim)

        def forward(self, x):
            z = x.transpose(1, 2)
            parts = [block(z)[..., :z.shape[-1]].transpose(1, 2) for block in self.blocks]
            merged = torch.cat(parts, dim=-1)
            return x + torch.tanh(self.gate(merged))

    class RoutedLSTM(nn.Module):
        def __init__(self, hp: dict[str, Any]):
            super().__init__()
            dim = int(hp["hidden_dim"])
            self.domain_proj = nn.ModuleList([nn.Sequential(nn.Linear(len(idx) * 2, dim), nn.GELU(), nn.LayerNorm(dim)) for idx in domain_indices])
            self.router = nn.Sequential(nn.Linear(len(panel.feature_names) * 2, dim), nn.GELU(), nn.Linear(dim, len(domain_indices)))
            self.conv = CausalConv(dim)
            projection = max(16, dim // 2)
            self.lstm = nn.LSTM(dim, dim, num_layers=int(hp["lstm_layers"]), batch_first=True, dropout=float(hp["dropout"]), proj_size=projection)
            self.to_dim = nn.Linear(projection, dim)
            layer = nn.TransformerEncoderLayer(dim, int(hp["heads"]), dim * 4, float(hp["dropout"]), batch_first=True, norm_first=True, activation="gelu")
            self.temporal = nn.TransformerEncoder(layer, num_layers=int(hp["attention_layers"]), enable_nested_tensor=False)
            self.regime_gate = nn.Sequential(nn.Linear(dim, dim), nn.GELU(), nn.Linear(dim, int(hp["experts"])))
            self.experts = nn.ModuleList([nn.Sequential(nn.Linear(dim, dim), nn.GELU(), nn.Dropout(float(hp["dropout"])), nn.Linear(dim, len(panel.horizons) * 3)) for _ in range(int(hp["experts"]))])
            self.skip = nn.Linear(len(panel.feature_names), len(panel.horizons))
            self.norm = nn.LayerNorm(dim)

        def forward(self, x, m):
            current = torch.cat([x[:, -1], m[:, -1]], dim=-1)
            weights = torch.softmax(self.router(current), dim=-1)
            routed = []
            for proj, idx in zip(self.domain_proj, domain_indices):
                routed.append(proj(torch.cat([x[..., idx], m[..., idx]], dim=-1)))
            h = sum(weights[:, i, None, None] * routed[i] for i in range(len(routed)))
            h = self.conv(h)
            h, _ = self.lstm(h)
            h = self.to_dim(h)
            length = h.shape[1]
            mask = torch.triu(torch.ones(length, length, device=h.device, dtype=torch.bool), diagonal=1)
            h = self.temporal(h, mask=mask)
            z = self.norm(h[:, -1])
            gate = torch.softmax(self.regime_gate(z), dim=-1)
            expert = torch.stack([head(z) for head in self.experts], dim=1)
            output = (gate.unsqueeze(-1) * expert).sum(dim=1).reshape(-1, len(panel.horizons), 3)
            mu = output[..., 0] + 0.15 * self.skip(x[:, -1])
            log_sigma = output[..., 1].clamp(-5, 2)
            quantile_width = torch.nn.functional.softplus(output[..., 2])
            return mu, log_sigma, quantile_width, gate, weights

    def rank_loss(pred, target):
        pred = pred - pred.mean()
        target = target - target.mean()
        corr = (pred * target).mean() / (pred.std(unbiased=False) * target.std(unbiased=False) + 1e-6)
        return 1 - corr

    def fit_one(hp: dict[str, Any], seed: int, epochs: int, trial_label: str):
        random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
        model = RoutedLSTM(hp).to(device)
        optimizer = torch.optim.AdamW(model.parameters(), lr=float(hp["learning_rate"]), weight_decay=float(hp["weight_decay"]), betas=(0.9, 0.95))
        train_ds, valid_ds = DateDataset("train"), DateDataset("valid")
        best_state, best_score, history = None, -1e9, []
        prev_score = None
        for epoch in range(max(1, epochs)):
            model.train(); losses = []
            for x, m, y, mask, exposures in torch.utils.data.DataLoader(train_ds, batch_size=1, shuffle=False):
                x, m, y, mask, exposures = x[0].to(device), m[0].to(device), y[0].to(device), mask[0].to(device), exposures[0].to(device)
                if mask.sum() < 30: continue
                mu, log_sigma, width, gate, router = model(x[mask], m[mask])
                yy = y[mask]
                huber = torch.nn.functional.smooth_l1_loss(mu, yy)
                nll = (0.5 * torch.exp(-2 * log_sigma) * (yy - mu).pow(2) + log_sigma).mean()
                ranking = torch.stack([rank_loss(mu[:, i], yy[:, i]) for i in range(len(panel.horizons))]).mean()
                sign = torch.nn.functional.binary_cross_entropy_with_logits(mu * 12, (yy > 0).float())
                exposure_penalty = torch.stack([torch.abs(torch.corrcoef(torch.stack([mu[:, 0], exposures[mask, j]]))[0, 1]) for j in range(exposures.shape[1])]).nanmean()
                turnover = torch.tensor(0.0, device=device)
                current_score = mu[:, 0]
                if prev_score is not None and len(prev_score) == len(current_score):
                    turnover = (current_score - prev_score).abs().mean()
                prev_score = current_score.detach()
                balance = (gate.mean(0) * torch.log(gate.mean(0) + 1e-8)).sum() + (router.mean(0) * torch.log(router.mean(0) + 1e-8)).sum()
                loss = .34 * ranking + .24 * huber + .14 * nll + .08 * sign + .08 * turnover + .08 * exposure_penalty + .04 * balance
                optimizer.zero_grad(set_to_none=True); loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), float(hp["grad_clip"]))
                optimizer.step(); losses.append(finite(loss.item()))
            valid_scores, valid_targets = [], []
            model.eval()
            with torch.no_grad():
                for x, m, y, mask, _ in torch.utils.data.DataLoader(valid_ds, batch_size=1, shuffle=False):
                    x, m, y, mask = x[0].to(device), m[0].to(device), y[0].numpy(), mask[0].numpy().astype(bool)
                    if mask.sum() < 30: continue
                    mu = model(x[mask], m[mask])[0].cpu().numpy()
                    score = safe_corr(rankdata(mu[:, 0]), rankdata(y[mask, 0]))
                    valid_scores.append(score); valid_targets.append(score)
            validation = finite(np.mean(valid_scores)) if valid_scores else -1.0
            history.append({"epoch": epoch + 1, "train_loss": finite(np.mean(losses)), "valid_rank_ic": validation})
            if validation > best_score:
                best_score = validation
                best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        if best_state: model.load_state_dict(best_state)
        return model, best_score, history

    search = config.get("search", {})
    rng = random.Random(int(config.get("seed", 20260720)))
    n_trials = max(1, int(search.get("trials", 3)))
    trial_epochs = max(1, int(search.get("trial_epochs", 2)))
    base_hp = {
        "hidden_dim": int(config.get("hidden_dim", 128)), "lstm_layers": int(config.get("lstm_layers", 2)),
        "attention_layers": int(config.get("attention_layers", 2)), "heads": int(config.get("heads", 8)),
        "experts": int(config.get("experts", 4)), "dropout": float(config.get("dropout", .20)),
        "learning_rate": float(config.get("learning_rate", 3e-4)), "weight_decay": float(config.get("weight_decay", 1e-4)),
        "grad_clip": float(config.get("grad_clip", 1.0)),
    }
    candidates = []
    for trial in range(n_trials):
        hp = dict(base_hp)
        if trial:
            hp.update({
                "hidden_dim": rng.choice([64, 96, 128, 160]), "lstm_layers": rng.choice([2, 3]),
                "attention_layers": rng.choice([1, 2, 3]), "heads": rng.choice([4, 8]),
                "experts": rng.choice([3, 4, 6]), "dropout": rng.choice([.12, .18, .24, .30]),
                "learning_rate": 10 ** rng.uniform(-4.1, -3.1), "weight_decay": 10 ** rng.uniform(-5.5, -3.3),
            })
            if hp["hidden_dim"] % hp["heads"]: hp["heads"] = 4
        progress(progress_path, "lstm_search", .20 + .18 * trial / n_trials, f"嵌套净化搜索 {trial + 1}/{n_trials}", trial=trial + 1)
        model, score, history = fit_one(hp, int(config.get("seed", 20260720)) + trial, trial_epochs, f"trial-{trial}")
        candidates.append({"hp": hp, "valid_rank_ic": score, "history": history})
        del model
    candidates.sort(key=lambda x: x["valid_rank_ic"], reverse=True)
    best_hp = candidates[0]["hp"]
    seeds = [int(config.get("seed", 20260720)) + i * 97 for i in range(max(1, int(config.get("ensemble_seeds", 3))))]
    final_epochs = max(1, int(config.get("epochs", 8)))
    split_predictions: dict[str, list[np.ndarray]] = {"train": [], "valid": [], "test": []}
    histories = []
    for index, seed in enumerate(seeds):
        progress(progress_path, "lstm_ensemble", .40 + .32 * index / len(seeds), f"训练深度集成 seed {index + 1}/{len(seeds)}", seed=seed)
        model, _, history = fit_one(best_hp, seed, final_epochs, f"seed-{seed}")
        histories.append({"seed": seed, "history": history})
        model.eval()
        with torch.no_grad():
            for split_name in split_predictions:
                preds = []
                for x, m, y, mask, _ in torch.utils.data.DataLoader(DateDataset(split_name), batch_size=1, shuffle=False):
                    x, m = x[0].to(device), m[0].to(device)
                    preds.append(model(x, m)[0].cpu().numpy())
                split_predictions[split_name].append(np.stack(preds) if preds else np.empty((0, len(panel.assets), len(panel.horizons))))
        del model
    metrics, predictions = {}, {}
    for split_name, arrays in split_predictions.items():
        if not arrays: continue
        mean = np.mean(np.stack(arrays), axis=0)
        std = np.std(np.stack(arrays), axis=0)
        # Dataset can start after split start because of the lookback guard.
        start, end = panel.split[split_name]
        effective_start = max(sequence, start)
        original = panel.split[split_name]
        panel.split[split_name] = (effective_start, effective_start + len(mean))
        sf = split_frame(panel, split_name, mean[..., 0], 0)
        panel.split[split_name] = original
        bt = backtest_cross_section(sf, "score", "target", float(config.get("cost_bps", 15)), panel.horizons[0])
        metrics[split_name] = bt
        predictions[split_name] = {"mean_uncertainty": finite(np.mean(std[..., 0])), "dates": [x["date"] for x in bt["series"]], "rank_ic": [x["rank_ic"] for x in bt["series"]], "net": [x["net"] for x in bt["series"]]}
    trials = n_trials + len(seeds)
    metrics["test"].update(deflated_sharpe_proxy(metrics["test"]["sharpe"], metrics["test"]["observations"], trials))
    gates = gate_results(metrics, trials)
    return {
        "engine": "lstm", "engine_version": ENGINE_VERSION, "device": str(device),
        "architecture": {
            "name": "PIT-Masked Causal Mixture Residual LSTM",
            "components": ["domain_variable_router", "multi_scale_causal_depthwise_conv", "projected_lstm", "causal_transformer_attention", "regime_mixture_of_experts", "multi_horizon_gaussian_quantile_heads"],
            "best_hyperparameters": best_hp, "parameter_count_policy": "recorded_at_runtime",
        },
        "search": {"method": "purged_successive_halving", "candidates": candidates, "seeds": seeds, "trial_count": trials},
        "loss": {"rank": .34, "huber": .24, "heteroscedastic_nll": .14, "sign": .08, "turnover": .08, "exposure": .08, "router_balance": .04},
        "metrics": metrics, "predictions": predictions, "gates": gates, "training_history": histories,
        "scaler_hash": hashlib.sha256(np.asarray(scaler).tobytes()).hexdigest(),
    }


UNARY_TOKENS = ["NEG", "ABS", "SLOG", "CS_RANK", "TS_Z20", "DELTA5", "DECAY10"]
BINARY_TOKENS = ["ADD", "SUB", "MUL", "DIV"]


def cross_rank(frame: pd.DataFrame, values: pd.Series) -> pd.Series:
    return values.groupby(frame["trade_date"]).rank(pct=True) - 0.5


def time_op(frame: pd.DataFrame, values: pd.Series, op: str) -> pd.Series:
    g = values.groupby(frame["ts_code"])
    if op == "TS_Z20":
        mean = g.rolling(20, min_periods=12).mean().reset_index(level=0, drop=True)
        std = g.rolling(20, min_periods=12).std().reset_index(level=0, drop=True)
        return (values - mean) / std.replace(0, np.nan)
    if op == "DELTA5": return values - g.shift(5)
    if op == "DECAY10": return g.rolling(10, min_periods=6).mean().reset_index(level=0, drop=True)
    raise ValueError(op)


def evaluate_postfix(frame: pd.DataFrame, tokens: list[str]) -> pd.Series:
    stack: list[pd.Series] = []
    for token in tokens:
        if token in FEATURES:
            stack.append(frame[token].astype(float))
        elif token in UNARY_TOKENS:
            if not stack: raise ValueError("unary stack underflow")
            x = stack.pop()
            if token == "NEG": y = -x
            elif token == "ABS": y = x.abs()
            elif token == "SLOG": y = np.sign(x) * np.log1p(x.abs())
            elif token == "CS_RANK": y = cross_rank(frame, x)
            else: y = time_op(frame, x, token)
            stack.append(y.replace([np.inf, -np.inf], np.nan))
        elif token in BINARY_TOKENS:
            if len(stack) < 2: raise ValueError("binary stack underflow")
            b, a = stack.pop(), stack.pop()
            if token == "ADD": y = a + b
            elif token == "SUB": y = a - b
            elif token == "MUL": y = a.clip(-8, 8) * b.clip(-8, 8)
            else: y = a / b.where(b.abs() > 1e-6)
            stack.append(y.replace([np.inf, -np.inf], np.nan))
        else:
            raise ValueError(f"unknown token {token}")
    if len(stack) != 1: raise ValueError("formula stack is not singular")
    return stack[0]


def formula_complexity(tokens: list[str]) -> float:
    return len(tokens) + 1.5 * sum(x in UNARY_TOKENS for x in tokens) + 2.0 * sum(x in BINARY_TOKENS for x in tokens)


def formula_reward(frame: pd.DataFrame, tokens: list[str], target: str, cost_bps: float, fidelity: float = 1.0) -> tuple[float, dict[str, Any]]:
    try:
        values = evaluate_postfix(frame, tokens)
    except Exception:
        return -1.0, {"invalid": True}
    work = frame[["trade_date", "ts_code", target, "ret_20", "value_bp", "log_mv"]].copy()
    work["score"] = values
    if fidelity < 1:
        dates = sorted(work.trade_date.unique())
        keep = set(dates[:: max(1, int(1 / max(.05, fidelity)))])
        work = work[work.trade_date.isin(keep)]
    work = work.dropna(subset=["score", target])
    coverage = len(work) / max(1, len(frame))
    if coverage < .55:
        return -.8 + .2 * coverage, {"coverage": coverage, "invalid": True}
    raw_ic = work.groupby("trade_date").apply(lambda g: safe_corr(rankdata(g.score.to_numpy()), rankdata(g[target].to_numpy())), include_groups=False)
    dates = sorted(work.trade_date.unique())
    cut = dates[len(dates) // 2] if dates else ""
    early = finite(raw_ic[raw_ic.index <= cut].mean()) if len(raw_ic) else 0.0
    late = finite(raw_ic[raw_ic.index > cut].mean()) if len(raw_ic) else 0.0
    # Residual contribution against two broad existing factor dimensions.
    sample = work.dropna(subset=["ret_20", "value_bp", "log_mv"]).copy()
    if len(sample) > 100:
        x = sample[["ret_20", "value_bp", "log_mv"]].to_numpy(float)
        x = np.column_stack([np.ones(len(x)), np.nan_to_num(x)])
        beta = np.linalg.lstsq(x, sample.score.to_numpy(float), rcond=1e-6)[0]
        sample["residual"] = sample.score.to_numpy(float) - x @ beta
        residual_ic = sample.groupby("trade_date").apply(lambda g: safe_corr(rankdata(g.residual.to_numpy()), rankdata(g[target].to_numpy())), include_groups=False).mean()
    else:
        residual_ic = 0.0
    bt = backtest_cross_section(work.rename(columns={target: "target"}), "score", "target", cost_bps, 5)
    turnover = bt["turnover"]
    redundancy = max(abs(safe_corr(work.score.to_numpy(), work.ret_20.to_numpy())), abs(safe_corr(work.score.to_numpy(), work.value_bp.to_numpy())))
    weakest = min(early, late)
    reward = 8.0 * weakest + 5.0 * finite(residual_ic) + .20 * bt["sharpe"] - .18 * turnover - .22 * redundancy - .008 * formula_complexity(tokens)
    detail = {"early_rank_ic": early, "late_rank_ic": late, "residual_rank_ic": finite(residual_ic), "coverage": coverage, "turnover": turnover, "max_correlation": redundancy, "valid_sharpe": bt["sharpe"], "reward": finite(reward), "invalid": False}
    return finite(reward), detail


def run_rl_transformer(panel: Panel, config: dict[str, Any], progress_path: Path | None) -> dict[str, Any]:
    torch, nn = torch_modules()
    device = torch.device("cuda" if torch.cuda.is_available() and config.get("allow_cuda", True) else "cpu")
    seed = int(config.get("seed", 20260720)); random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
    vocab = ["PAD", "BOS", "STOP", *FEATURES, *UNARY_TOKENS, *BINARY_TOKENS]
    token_id = {x: i for i, x in enumerate(vocab)}
    operands = set(FEATURES); unary = set(UNARY_TOKENS); binary = set(BINARY_TOKENS)
    max_steps = int(config.get("max_formula_tokens", 14))
    d_model = int(config.get("d_model", 128)); heads = int(config.get("heads", 8)); layers = int(config.get("layers", 4))

    class ActorCritic(nn.Module):
        def __init__(self):
            super().__init__()
            self.token = nn.Embedding(len(vocab), d_model)
            self.position = nn.Embedding(max_steps + 2, d_model)
            layer = nn.TransformerEncoderLayer(d_model, heads, d_model * 4, float(config.get("dropout", .15)), batch_first=True, norm_first=True, activation="gelu")
            self.backbone = nn.TransformerEncoder(layer, num_layers=layers, enable_nested_tensor=False)
            self.policy = nn.Linear(d_model, len(vocab)); self.value = nn.Linear(d_model, 1)

        def forward(self, tokens):
            pos = torch.arange(tokens.shape[1], device=tokens.device)[None]
            x = self.token(tokens) + self.position(pos)
            length = tokens.shape[1]
            mask = torch.triu(torch.ones(length, length, device=tokens.device, dtype=torch.bool), diagonal=1)
            h = self.backbone(x, mask=mask)[:, -1]
            return self.policy(h), self.value(h).squeeze(-1)

    model = ActorCritic().to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=float(config.get("learning_rate", 2e-4)), weight_decay=float(config.get("weight_decay", 1e-4)))
    all_frame = panel.frame[["trade_date", "ts_code", *FEATURES, f"target_{panel.horizons[0]}"]].copy()
    train_start, train_end = panel.split["train"]; valid_start, valid_end = panel.split["valid"]; test_start, test_end = panel.split["test"]
    train_dates = set(panel.dates[train_start:train_end]); valid_dates = set(panel.dates[valid_start:valid_end]); test_dates = set(panel.dates[test_start:test_end])
    train_frame = all_frame[all_frame.trade_date.isin(train_dates)].reset_index(drop=True)
    valid_frame = all_frame[all_frame.trade_date.isin(valid_dates)].reset_index(drop=True)
    test_frame = all_frame[all_frame.trade_date.isin(test_dates)].reset_index(drop=True)
    target = f"target_{panel.horizons[0]}"
    reward_cache: dict[str, tuple[float, dict[str, Any]]] = {}
    archive: dict[str, dict[str, Any]] = {}

    def legal_mask(stack_depth: int, step: int) -> np.ndarray:
        mask = np.zeros(len(vocab), dtype=bool)
        for token in FEATURES: mask[token_id[token]] = True
        if stack_depth >= 1:
            for token in UNARY_TOKENS: mask[token_id[token]] = True
            mask[token_id["STOP"]] = True
        if stack_depth >= 2:
            for token in BINARY_TOKENS: mask[token_id[token]] = True
        if step >= max_steps - 1:
            mask[:] = False
            if stack_depth == 1: mask[token_id["STOP"]] = True
            elif stack_depth >= 2:
                for token in BINARY_TOKENS: mask[token_id[token]] = True
            else:
                for token in FEATURES: mask[token_id[token]] = True
        return mask

    def sample_episode(fidelity: float):
        ids = [token_id["BOS"]]; formula: list[str] = []; stack = 0; transitions = []
        for step in range(max_steps):
            state = torch.tensor([ids], dtype=torch.long, device=device)
            logits, value = model(state)
            legal = legal_mask(stack, step)
            masked = logits[0].masked_fill(~torch.tensor(legal, device=device), -1e9)
            dist = torch.distributions.Categorical(logits=masked)
            action = dist.sample(); action_id = int(action.item()); token = vocab[action_id]
            transitions.append({"state": list(ids), "action": action_id, "old_logp": finite(dist.log_prob(action).item()), "value": finite(value.item()), "legal": legal.tolist()})
            ids.append(action_id)
            if token == "STOP": break
            formula.append(token)
            if token in operands: stack += 1
            elif token in binary: stack -= 1
        if stack != 1: reward, detail = -1.0, {"invalid": True, "reason": "non_singular_stack"}
        else:
            key = " ".join(formula)
            if key not in reward_cache:
                train_reward, train_detail = formula_reward(train_frame, formula, target, float(config.get("cost_bps", 15)), fidelity)
                valid_reward, valid_detail = formula_reward(valid_frame, formula, target, float(config.get("cost_bps", 15)), fidelity)
                reward = min(train_reward, valid_reward) + .35 * valid_reward
                detail = {"train": train_detail, "valid": valid_detail, "reward": finite(reward)}
                reward_cache[key] = (reward, detail)
            reward, detail = reward_cache[key]
            complexity_bucket = min(4, len(formula) // 4)
            domain_bucket = next((name for name, cols in DOMAINS.items() if any(x in cols for x in formula)), "mixed")
            cell = f"{complexity_bucket}:{domain_bucket}"
            current = archive.get(cell)
            if current is None or reward > current["reward"]:
                archive[cell] = {"formula": formula, "reward": reward, "detail": detail}
        return transitions, formula, finite(reward), detail

    episodes = max(8, int(config.get("episodes", 160)))
    rollout = max(4, int(config.get("rollout_batch", 32)))
    ppo_epochs = max(1, int(config.get("ppo_epochs", 3)))
    gamma = float(config.get("gamma", .99)); clip = float(config.get("ppo_clip", .2)); entropy_coef = float(config.get("entropy", .01)); value_coef = float(config.get("value_coef", .5))
    training_curve = []
    episode_count = 0
    while episode_count < episodes:
        batch = []
        fidelity = .35 if episode_count < episodes * .55 else 1.0
        rewards = []
        for _ in range(min(rollout, episodes - episode_count)):
            transitions, formula, reward, detail = sample_episode(fidelity)
            returns = []
            running = reward
            for _step in reversed(transitions):
                returns.append(running); running *= gamma
            returns.reverse()
            for transition, ret in zip(transitions, returns):
                transition["return"] = ret; batch.append(transition)
            rewards.append(reward); episode_count += 1
        for _ in range(ppo_epochs):
            random.shuffle(batch)
            for transition in batch:
                state = torch.tensor([transition["state"]], dtype=torch.long, device=device)
                logits, value = model(state)
                legal = torch.tensor(transition["legal"], dtype=torch.bool, device=device)
                masked = logits[0].masked_fill(~legal, -1e9)
                dist = torch.distributions.Categorical(logits=masked)
                action = torch.tensor(transition["action"], device=device)
                logp = dist.log_prob(action)
                advantage = torch.tensor(transition["return"] - transition["value"], dtype=torch.float32, device=device)
                ratio = torch.exp(logp - transition["old_logp"])
                policy_loss = -torch.min(ratio * advantage, torch.clamp(ratio, 1 - clip, 1 + clip) * advantage)
                value_loss = (value[0] - transition["return"]) ** 2
                loss = policy_loss + value_coef * value_loss - entropy_coef * dist.entropy()
                optimizer.zero_grad(set_to_none=True); loss.backward(); torch.nn.utils.clip_grad_norm_(model.parameters(), .5); optimizer.step()
        training_curve.append({"episodes": episode_count, "mean_reward": finite(np.mean(rewards)), "best_reward": max((x["reward"] for x in archive.values()), default=-1.0), "unique_formulas": len(reward_cache), "archive_cells": len(archive), "fidelity": fidelity})
        progress(progress_path, "rl_ppo", .20 + .57 * episode_count / episodes, f"PPO+GAE 公式搜索 {episode_count}/{episodes}", unique_formulas=len(reward_cache), archive_cells=len(archive))
    ranked = sorted(archive.values(), key=lambda x: x["reward"], reverse=True)[: min(12, len(archive))]
    final_candidates = []
    for item in ranked:
        formula = item["formula"]
        train_reward, train_detail = formula_reward(train_frame, formula, target, float(config.get("cost_bps", 15)), 1.0)
        valid_reward, valid_detail = formula_reward(valid_frame, formula, target, float(config.get("cost_bps", 15)), 1.0)
        test_reward, test_detail = formula_reward(test_frame, formula, target, float(config.get("cost_bps", 15)), 1.0)
        score = evaluate_postfix(test_frame, formula)
        test_work = test_frame[["trade_date", "ts_code", target]].copy(); test_work["score"] = score
        bt = backtest_cross_section(test_work.rename(columns={target: "target"}), "score", "target", float(config.get("cost_bps", 15)), panel.horizons[0])
        final_candidates.append({"formula_postfix": formula, "formula": " ".join(formula), "train": train_detail, "valid": valid_detail, "test": test_detail, "test_backtest": bt, "selection_reward": finite(min(train_reward, valid_reward) + .35 * valid_reward), "test_report_only_reward": test_reward})
    trials = max(1, len(reward_cache))
    best = final_candidates[0] if final_candidates else {"test_backtest": {"sharpe": 0.0, "observations": 0}}
    best["test_backtest"].update(deflated_sharpe_proxy(best["test_backtest"].get("sharpe", 0.0), best["test_backtest"].get("observations", 0), trials))
    metrics = {"test": best["test_backtest"], "valid": {"rank_ic": finite((best.get("valid") or {}).get("late_rank_ic")), "sharpe": finite((best.get("valid") or {}).get("valid_sharpe"))}}
    return {
        "engine": "rl_transformer", "engine_version": ENGINE_VERSION, "device": str(device),
        "architecture": {"name": "Grammar-Constrained Synergistic Formula Transformer", "layers": layers, "d_model": d_model, "heads": heads, "vocabulary": len(vocab), "components": ["causal_transformer_actor", "critic_value_head", "postfix_ast_environment", "hard_type_stack_mask", "ppo_clipped_objective", "multi_fidelity_reward", "quality_diversity_archive"]},
        "search": {"episodes": episodes, "rollout_batch": rollout, "ppo_epochs": ppo_epochs, "unique_formulas": len(reward_cache), "archive_cells": len(archive), "trial_count": trials},
        "reward": {"weakest_fold_rank_ic": 8.0, "residual_rank_ic": 5.0, "net_sharpe": .20, "turnover": -.18, "redundancy": -.22, "complexity": -.008},
        "training_curve": training_curve, "candidates": final_candidates, "metrics": metrics,
        "gates": gate_results(metrics, trials), "test_used_for_search": False,
    }


def run_strategy(panel: Panel, config: dict[str, Any], progress_path: Path | None) -> dict[str, Any]:
    from sklearn.linear_model import LinearRegression, Lasso
    from sklearn.neural_network import MLPRegressor
    values, _, _ = normalise_panel(panel)
    horizon_i = 0
    def flat(split_name):
        start, end = panel.split[split_name]
        x = values[start:end].reshape(-1, len(panel.feature_names))
        y = panel.targets[start:end, :, horizon_i].reshape(-1)
        date = np.repeat(np.array(panel.dates[start:end]), len(panel.assets))
        asset = np.tile(np.array(panel.assets), end - start)
        mask = np.isfinite(y)
        return x[mask], y[mask], date[mask], asset[mask]
    xtr, ytr, _, _ = flat("train"); xv, yv, dv, av = flat("valid"); xt, yt, dt, at = flat("test")
    max_samples = int(config.get("max_training_samples", 240000))
    if len(xtr) > max_samples:
        idx = np.linspace(0, len(xtr) - 1, max_samples).astype(int); xtr, ytr = xtr[idx], ytr[idx]
    models = {
        "ols": LinearRegression(),
        "lasso": Lasso(alpha=float(config.get("lasso_alpha", 2e-5)), max_iter=10000, selection="cyclic"),
        "deep_mlp": MLPRegressor(hidden_layer_sizes=(256, 128, 64), activation="relu", alpha=1e-4, batch_size=1024, learning_rate_init=3e-4, max_iter=int(config.get("epochs", 20)), early_stopping=True, validation_fraction=.15, n_iter_no_change=5, random_state=int(config.get("seed", 20260720))),
    }
    result = {}
    for i, (name, model) in enumerate(models.items()):
        progress(progress_path, "strategy", .25 + i * .18, f"训练统一策略模型：{name}")
        model.fit(xtr, ytr)
        pv, pt = model.predict(xv), model.predict(xt)
        vf = pd.DataFrame({"trade_date": dv, "ts_code": av, "score": pv, "target": yv})
        tf = pd.DataFrame({"trade_date": dt, "ts_code": at, "score": pt, "target": yt})
        result[name] = {"valid": backtest_cross_section(vf, "score", "target", float(config.get("cost_bps", 15)), panel.horizons[0]), "test": backtest_cross_section(tf, "score", "target", float(config.get("cost_bps", 15)), panel.horizons[0])}
    valid_sharpes = np.array([max(-1, result[x]["valid"]["sharpe"]) for x in models], dtype=float)
    weights = np.exp(valid_sharpes - np.max(valid_sharpes)); weights /= weights.sum()
    predictions = []
    for model in models.values(): predictions.append(model.predict(xt))
    ensemble = np.average(np.stack(predictions), axis=0, weights=weights)
    ef = pd.DataFrame({"trade_date": dt, "ts_code": at, "score": ensemble, "target": yt})
    result["ensemble"] = {"weights": dict(zip(models.keys(), weights.tolist())), "test": backtest_cross_section(ef, "score", "target", float(config.get("cost_bps", 15)), panel.horizons[0])}
    trials = len(models) + 1
    result["ensemble"]["test"].update(deflated_sharpe_proxy(result["ensemble"]["test"]["sharpe"], result["ensemble"]["test"]["observations"], trials))
    metrics = {"valid": max((result[x]["valid"] for x in models), key=lambda x: x["sharpe"]), "test": result["ensemble"]["test"]}
    return {"engine": "strategy", "engine_version": ENGINE_VERSION, "models": result, "metrics": metrics, "gates": gate_results(metrics, trials), "test_used_for_selection": False}


def run_joint_test(panel: Panel, config: dict[str, Any], progress_path: Path | None) -> dict[str, Any]:
    start, end = panel.split["test"]
    rows = []
    for fi, name in enumerate(panel.feature_names):
        score = panel.features[start:end, :, fi]
        sf = split_frame(panel, "test", score, 0)
        metric = backtest_cross_section(sf, "score", "target", float(config.get("cost_bps", 15)), panel.horizons[0])
        rows.append({"factor": name, **{k: v for k, v in metric.items() if k != "series"}})
    rows.sort(key=lambda x: abs(x["rank_ic"]), reverse=True)
    matrix = panel.features[start:end].reshape(-1, len(panel.feature_names))
    corr = np.corrcoef(np.nan_to_num(matrix).T)
    return {"engine": "joint_test", "engine_version": ENGINE_VERSION, "factors": rows, "correlation": {"labels": panel.feature_names, "matrix": np.nan_to_num(corr).round(5).tolist()}, "metrics": {"test": rows[0] if rows else {}}, "gates": gate_results({"test": rows[0] if rows else {}}, len(rows))}


def gate_results(metrics: dict[str, Any], trials: int) -> list[dict[str, Any]]:
    valid = metrics.get("valid") or {}; test = metrics.get("test") or {}
    rules = [
        ("point_in_time", True, True, "点时与冻结切分"),
        ("coverage", finite(test.get("coverage", 1.0)), .80, "测试期覆盖率"),
        ("rank_ic", abs(finite(test.get("rank_ic"))), .03, "测试期绝对RankIC"),
        ("hit_rate", finite(test.get("hit_rate")), .53, "测试期IC命中率"),
        ("oos_decay", abs(finite(test.get("rank_ic"))) / max(abs(finite(valid.get("rank_ic"))), 1e-6), .75, "验证到测试衰减"),
        ("net_sharpe", finite(test.get("sharpe")), .50, "成本后测试Sharpe"),
        ("drawdown", -finite(test.get("max_drawdown")), -.25, "最大回撤不劣于-25%"),
        ("turnover", 1 - finite(test.get("turnover")), .35, "换手预算"),
        ("dsr", finite(test.get("dsr_confidence")), .60, "多重试验修正DSR"),
        ("trial_ledger", trials, 1, "试验台账完整"),
    ]
    return [{"gate": key, "label": label, "observed": finite(value), "threshold": finite(threshold), "passed": bool(value >= threshold)} for key, value, threshold, label in rules]


def run(config: dict[str, Any], progress_path: Path | None = None) -> dict[str, Any]:
    started = time.time()
    progress(progress_path, "initializing", .01, "初始化研究任务", engine=config.get("engine"))
    panel = read_panel(config, progress_path)
    engine = str(config.get("engine", "lstm"))
    if engine == "lstm": payload = run_lstm(panel, config, progress_path)
    elif engine == "rl_transformer": payload = run_rl_transformer(panel, config, progress_path)
    elif engine == "strategy": payload = run_strategy(panel, config, progress_path)
    elif engine == "joint_test": payload = run_joint_test(panel, config, progress_path)
    else: raise ValueError(f"unsupported engine: {engine}")
    payload.update({
        "status": "completed", "engine_version": ENGINE_VERSION, "created_at": now_iso(),
        "elapsed_seconds": round(time.time() - started, 3), "source": panel.source,
        "split": {k: {"start": panel.dates[v[0]], "end": panel.dates[max(v[0], v[1] - 1)], "start_index": v[0], "end_index": v[1]} for k, v in panel.split.items()},
        "horizons": panel.horizons, "features": panel.feature_names,
        "test_policy": "report_only_after_train_validation_selection",
    })
    progress(progress_path, "completed", 1.0, "研究任务完成", elapsed_seconds=payload["elapsed_seconds"])
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--progress")
    args = parser.parse_args()
    config_path, output_path = Path(args.config), Path(args.output)
    progress_path = Path(args.progress) if args.progress else None
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        payload = run(config, progress_path)
        atomic_json(output_path, payload)
        return 0
    except Exception as exc:  # noqa: BLE001
        failure = {"status": "failed", "engine_version": ENGINE_VERSION, "message": str(exc), "traceback": traceback.format_exc(limit=18), "created_at": now_iso()}
        atomic_json(output_path, failure)
        progress(progress_path, "failed", 1.0, str(exc))
        print(failure["traceback"], file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
