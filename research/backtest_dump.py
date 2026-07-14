"""Event-level backtest for historical DUMP scanner databases.

The tool deliberately evaluates model generations separately. It does not replay the
current scanner from candles; it measures only decisions that were actually recorded
at the time, which avoids look-ahead bias in candidate selection.
"""

from __future__ import annotations

import argparse
import ast
from bisect import bisect_left
from collections import Counter, defaultdict
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import sqlite3
from statistics import mean, median, pstdev
from typing import Any, Iterable


@dataclass
class SignalEvent:
    signal_id: int
    signal_type: str
    symbol: str
    ts: int
    entry_price: float
    gross_return_pct: float
    max_favorable_pct: float
    max_adverse_pct: float
    model_version: str
    mode: str
    score: int
    window_minutes: int
    turnover_24h: float
    price_growth_pct: float
    drawdown_pct: float
    episode_id: int = 0


@dataclass
class SimulatedTrade:
    signal_id: int
    symbol: str
    entry_ts: int
    exit_ts: int
    gross_return_pct: float
    net_return_pct: float
    exit_reason: str
    model_version: str
    mode: str
    score: int
    episode_id: int
    path_usable: bool


@dataclass
class CandidateOutcome:
    candidate_id: int
    scanner: str
    symbol: str
    ts: int
    score: int
    missing_checks: str
    gross_return_pct: float
    net_return_pct: float
    matching_signal: bool


def current_signal_coverage(
    conn: sqlite3.Connection,
    *,
    model_version: str,
    horizon_minutes: int,
) -> dict[str, Any]:
    signal_columns = table_columns(conn, "signals")
    if "model_version" not in signal_columns:
        return {
            "signals": 0,
            "executable_quotes": 0,
            "reviewed": 0,
            "execution_coverage_pct": 0.0,
            "review_coverage_pct": 0.0,
        }

    total = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM signals
            WHERE signal_type LIKE 'dump_%' AND model_version = ?
            """,
            (model_version,),
        ).fetchone()[0]
    )
    executable = 0
    if {"entry_quote_status", "entry_bid", "execution_venue"} <= signal_columns:
        executable = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM signals
                WHERE signal_type LIKE 'dump_%'
                  AND model_version = ?
                  AND entry_quote_status = 'ok'
                  AND entry_bid > 0
                  AND execution_venue = 'BYBIT'
                """,
                (model_version,),
            ).fetchone()[0]
        )

    review_columns = table_columns(conn, "signal_reviews")
    review_filter = ""
    if "status" in review_columns:
        review_filter = "AND r.status IN ('ok', 'legacy')"
    reviewed = int(
        conn.execute(
            f"""
            SELECT COUNT(DISTINCT s.id)
            FROM signals s
            JOIN signal_reviews r ON r.signal_id = s.id
            WHERE s.signal_type LIKE 'dump_%'
              AND s.model_version = ?
              AND r.horizon_minutes = ?
              {review_filter}
            """,
            (model_version, horizon_minutes),
        ).fetchone()[0]
    )
    return {
        "signals": total,
        "executable_quotes": executable,
        "reviewed": reviewed,
        "execution_coverage_pct": executable / total * 100 if total else 0.0,
        "review_coverage_pct": reviewed / total * 100 if total else 0.0,
    }


def paper_observation(conn: sqlite3.Connection) -> dict[str, Any]:
    if not table_exists(conn, "paper_runtime"):
        return {
            "observation_days": 0.0,
            "heartbeat_count": 0,
            "quote_error_count": 0,
            "loop_error_count": 0,
        }
    row = conn.execute(
        """
        SELECT observation_start_ts, last_heartbeat_ts, heartbeat_count,
               quote_error_count, loop_error_count
        FROM paper_runtime
        ORDER BY last_heartbeat_ts DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return {
            "observation_days": 0.0,
            "heartbeat_count": 0,
            "quote_error_count": 0,
            "loop_error_count": 0,
        }
    observation_seconds = max(0, int(row[1]) - int(row[0]))
    return {
        "observation_days": observation_seconds / 86_400,
        "heartbeat_count": int(row[2]),
        "quote_error_count": int(row[3]),
        "loop_error_count": int(row[4]),
    }


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def payload_fields(payload: str) -> dict[str, Any]:
    try:
        node = ast.parse(payload, mode="eval").body
    except (SyntaxError, ValueError):
        return {}
    if not isinstance(node, ast.Call):
        return {}
    values: dict[str, Any] = {}
    for keyword in node.keywords:
        if keyword.arg is None:
            continue
        try:
            values[keyword.arg] = ast.literal_eval(keyword.value)
        except (ValueError, TypeError):
            continue
    return values


def bare_symbol(value: str) -> str:
    return value.split(":", 1)[-1]


def infer_model_version(fields: dict[str, Any], signal_type: str) -> str:
    explicit = str(fields.get("model_version") or "").strip()
    if explicit:
        return explicit
    window = int(fields.get("window_minutes") or 0)
    confirmation = str(fields.get("confirmation_source") or "").strip()
    suffix = "-cross" if confirmation else ""
    return f"legacy-{window or 'unknown'}m{suffix}-{signal_type}"


def load_events(
    conn: sqlite3.Connection,
    *,
    horizon_minutes: int,
) -> list[SignalEvent]:
    signal_columns = table_columns(conn, "signals")
    review_columns = table_columns(conn, "signal_reviews")
    optional_signal = {
        "model_version": "''",
        "mode": "''",
        "score": "0",
        "turnover_24h": "0",
        "entry_price": "0",
    }
    selections = [
        "s.id AS signal_id",
        "s.signal_type",
        "s.symbol",
        "s.ts",
        "s.price",
        "s.payload",
        "r.move_pct",
        "r.max_favorable_pct",
        "r.max_adverse_pct",
    ]
    for name, fallback in optional_signal.items():
        expression = f"s.{name}" if name in signal_columns else fallback
        selections.append(f"{expression} AS {name}")

    where = ["s.signal_type LIKE 'dump_%'", "r.horizon_minutes = ?"]
    if "status" in review_columns:
        where.append("r.status IN ('ok', 'legacy')")
    rows = conn.execute(
        f"""
        SELECT {', '.join(selections)}
        FROM signals s
        JOIN signal_reviews r ON r.signal_id = s.id
        WHERE {' AND '.join(where)}
        ORDER BY s.ts, s.id
        """,
        (horizon_minutes,),
    ).fetchall()

    events: list[SignalEvent] = []
    for row in rows:
        fields = payload_fields(str(row[5] or ""))
        stored_version = str(row[9] or "").strip()
        stored_entry = float(row[13] or 0)
        event = SignalEvent(
            signal_id=int(row[0]),
            signal_type=str(row[1]),
            symbol=bare_symbol(str(row[2])),
            ts=int(row[3]),
            entry_price=stored_entry if stored_entry > 0 else float(row[4]),
            gross_return_pct=float(row[6]),
            max_favorable_pct=max(0.0, float(row[7] or 0)),
            max_adverse_pct=max(0.0, float(row[8] or 0)),
            model_version=stored_version or infer_model_version(fields, str(row[1])),
            mode=str(row[10] or fields.get("mode") or "UNKNOWN"),
            score=int(row[11] or fields.get("signal_score") or 0),
            window_minutes=int(fields.get("window_minutes") or 0),
            turnover_24h=float(row[12] or fields.get("turnover_24h") or 0),
            price_growth_pct=float(fields.get("price_growth_lookback_pct") or 0),
            drawdown_pct=float(fields.get("drawdown_from_high_pct") or 0),
        )
        if event.entry_price > 0:
            events.append(event)
    return events


def deduplicate_events(events: Iterable[SignalEvent], cooldown_minutes: int) -> list[SignalEvent]:
    cooldown_seconds = max(0, cooldown_minutes) * 60
    latest: dict[str, int] = {}
    kept: list[SignalEvent] = []
    for event in sorted(events, key=lambda item: (item.ts, item.signal_id)):
        previous = latest.get(event.symbol)
        latest[event.symbol] = event.ts
        if previous is not None and event.ts - previous < cooldown_seconds:
            continue
        kept.append(event)
    return kept


def assign_episodes(events: list[SignalEvent], cluster_minutes: int) -> None:
    cluster_seconds = max(1, cluster_minutes) * 60
    episode_id = 0
    previous_ts: int | None = None
    for event in sorted(events, key=lambda item: (item.ts, item.signal_id)):
        if previous_ts is None or event.ts - previous_ts > cluster_seconds:
            episode_id += 1
        event.episode_id = episode_id
        previous_ts = event.ts


def load_price_paths(
    conn: sqlite3.Connection,
    signal_ids: Iterable[int],
) -> dict[int, list[tuple[int, float]]]:
    if not table_exists(conn, "signal_price_snapshots"):
        return {}
    ids = sorted(set(int(value) for value in signal_ids))
    paths: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"""
            SELECT signal_id, ts, price
            FROM signal_price_snapshots
            WHERE signal_id IN ({placeholders}) AND price > 0
            ORDER BY signal_id, ts
            """,
            chunk,
        ).fetchall()
        for signal_id, ts, price in rows:
            paths[int(signal_id)].append((int(ts), float(price)))
    return dict(paths)


def short_return(entry_price: float, exit_price: float) -> float:
    if entry_price <= 0:
        return 0.0
    return ((entry_price - exit_price) / entry_price) * 100


def usable_path(
    event: SignalEvent,
    raw_path: list[tuple[int, float]],
    horizon_minutes: int,
) -> list[tuple[int, float]]:
    end_ts = event.ts + horizon_minutes * 60
    points = [(event.ts, event.entry_price)]
    points.extend((ts, price) for ts, price in raw_path if event.ts < ts <= end_ts)
    deduped: dict[int, float] = {}
    for ts, price in points:
        if price > 0:
            deduped[ts] = price
    return sorted(deduped.items())


def simulate_event(
    event: SignalEvent,
    *,
    raw_path: list[tuple[int, float]],
    horizon_minutes: int,
    cost_pct: float,
    stop_loss_pct: float | None = None,
    take_profit_pct: float | None = None,
    trailing_activation_pct: float | None = None,
    trailing_distance_pct: float | None = None,
    require_path: bool = False,
) -> SimulatedTrade | None:
    path = usable_path(event, raw_path, horizon_minutes)
    path_is_usable = len(path) >= 2
    if require_path and not path_is_usable:
        return None

    best_return = 0.0
    trailing_active = False
    for ts, price in path[1:]:
        move = short_return(event.entry_price, price)
        best_return = max(best_return, move)
        if stop_loss_pct is not None and move <= -stop_loss_pct:
            gross = -stop_loss_pct
            return SimulatedTrade(
                event.signal_id,
                event.symbol,
                event.ts,
                ts,
                gross,
                gross - cost_pct,
                "stop",
                event.model_version,
                event.mode,
                event.score,
                event.episode_id,
                path_is_usable,
            )
        if take_profit_pct is not None and move >= take_profit_pct:
            gross = take_profit_pct
            return SimulatedTrade(
                event.signal_id,
                event.symbol,
                event.ts,
                ts,
                gross,
                gross - cost_pct,
                "target",
                event.model_version,
                event.mode,
                event.score,
                event.episode_id,
                path_is_usable,
            )
        if trailing_activation_pct is not None and best_return >= trailing_activation_pct:
            trailing_active = True
        if (
            trailing_active
            and trailing_distance_pct is not None
            and best_return - move >= trailing_distance_pct
        ):
            gross = move
            return SimulatedTrade(
                event.signal_id,
                event.symbol,
                event.ts,
                ts,
                gross,
                gross - cost_pct,
                "trailing",
                event.model_version,
                event.mode,
                event.score,
                event.episode_id,
                path_is_usable,
            )

    gross = event.gross_return_pct
    if stop_loss_pct is not None and not path_is_usable and event.max_adverse_pct >= stop_loss_pct:
        gross = -stop_loss_pct
    return SimulatedTrade(
        event.signal_id,
        event.symbol,
        event.ts,
        event.ts + horizon_minutes * 60,
        gross,
        gross - cost_pct,
        "time",
        event.model_version,
        event.mode,
        event.score,
        event.episode_id,
        path_is_usable,
    )


def wilson_interval(wins: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 0.0
    proportion = wins / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(
        proportion * (1 - proportion) / total + z * z / (4 * total * total)
    ) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def bootstrap_episode_mean(
    trades: list[SimulatedTrade],
    *,
    samples: int = 3000,
    seed: int = 42,
) -> tuple[float, float]:
    grouped: dict[int, list[float]] = defaultdict(list)
    for trade in trades:
        grouped[trade.episode_id].append(trade.net_return_pct)
    episode_returns = [mean(values) for values in grouped.values()]
    if not episode_returns:
        return 0.0, 0.0
    rng = random.Random(seed)
    means = []
    for _ in range(samples):
        draw = [rng.choice(episode_returns) for _ in episode_returns]
        means.append(mean(draw))
    means.sort()
    return means[int(samples * 0.025)], means[min(samples - 1, int(samples * 0.975))]


def trade_metrics(trades: list[SimulatedTrade]) -> dict[str, Any]:
    if not trades:
        return {
            "n": 0,
            "episodes": 0,
            "win_rate_pct": 0.0,
            "win_rate_ci_low_pct": 0.0,
            "win_rate_ci_high_pct": 0.0,
            "mean_net_pct": 0.0,
            "median_net_pct": 0.0,
            "profit_factor": 0.0,
            "bootstrap_mean_low_pct": 0.0,
            "bootstrap_mean_high_pct": 0.0,
            "mean_without_top5_pct": 0.0,
        }
    returns = [trade.net_return_pct for trade in trades]
    wins = sum(value > 0 for value in returns)
    positives = sum(value for value in returns if value > 0)
    negatives = abs(sum(value for value in returns if value < 0))
    low, high = wilson_interval(wins, len(returns))
    bootstrap_low, bootstrap_high = bootstrap_episode_mean(trades)
    trimmed = sorted(returns, reverse=True)[5:]
    return {
        "n": len(returns),
        "episodes": len({trade.episode_id for trade in trades}),
        "win_rate_pct": wins / len(returns) * 100,
        "win_rate_ci_low_pct": low * 100,
        "win_rate_ci_high_pct": high * 100,
        "mean_net_pct": mean(returns),
        "median_net_pct": median(returns),
        "std_net_pct": pstdev(returns),
        "profit_factor": positives / negatives if negatives > 0 else math.inf,
        "bootstrap_mean_low_pct": bootstrap_low,
        "bootstrap_mean_high_pct": bootstrap_high,
        "mean_without_top5_pct": mean(trimmed) if trimmed else 0.0,
        "path_coverage_pct": sum(trade.path_usable for trade in trades) / len(trades) * 100,
    }


def chronological_splits(trades: list[SimulatedTrade]) -> dict[str, list[SimulatedTrade]]:
    ordered = sorted(trades, key=lambda item: (item.entry_ts, item.signal_id))
    first = int(len(ordered) * 0.60)
    second = int(len(ordered) * 0.80)
    return {
        "train_60": ordered[:first],
        "validation_20": ordered[first:second],
        "test_20": ordered[second:],
    }


def grouped_metrics(
    trades: list[SimulatedTrade],
    key,
) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[SimulatedTrade]] = defaultdict(list)
    for trade in trades:
        groups[str(key(trade))].append(trade)
    return {name: trade_metrics(values) for name, values in sorted(groups.items())}


def simulate_portfolio(
    trades: list[SimulatedTrade],
    *,
    starting_equity: float,
    risk_per_trade_pct: float,
    stop_loss_pct: float,
    max_notional_pct: float,
    max_concurrent_positions: int,
    max_positions_per_episode: int,
) -> dict[str, Any]:
    equity = starting_equity
    peak = equity
    max_drawdown = 0.0
    active: list[tuple[int, float, int]] = []
    accepted = 0
    skipped = 0
    equity_points: list[tuple[int, float]] = []

    def settle(until_ts: int) -> None:
        nonlocal equity, peak, max_drawdown, active
        due = sorted((item for item in active if item[0] <= until_ts), key=lambda item: item[0])
        active = [item for item in active if item[0] > until_ts]
        for exit_ts, pnl, _episode_id in due:
            equity += pnl
            peak = max(peak, equity)
            drawdown = ((peak - equity) / peak * 100) if peak > 0 else 0.0
            max_drawdown = max(max_drawdown, drawdown)
            equity_points.append((exit_ts, equity))

    for trade in sorted(trades, key=lambda item: (item.entry_ts, item.signal_id)):
        settle(trade.entry_ts)
        same_episode = sum(item[2] == trade.episode_id for item in active)
        if (
            len(active) >= max_concurrent_positions
            or same_episode >= max_positions_per_episode
        ):
            skipped += 1
            continue
        risk_notional = equity * (risk_per_trade_pct / max(stop_loss_pct, 0.01))
        capped_notional = equity * max_notional_pct / 100
        notional = min(risk_notional, capped_notional)
        pnl = notional * trade.net_return_pct / 100
        active.append((trade.exit_ts, pnl, trade.episode_id))
        accepted += 1

    settle(10**18)
    return {
        "starting_equity": starting_equity,
        "ending_equity": equity,
        "return_pct": ((equity - starting_equity) / starting_equity * 100)
        if starting_equity > 0
        else 0.0,
        "max_drawdown_pct": max_drawdown,
        "accepted_trades": accepted,
        "skipped_capacity_or_cluster": skipped,
        "equity_points": equity_points,
    }


def split_checks(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def has_signal_near(
    signal_times: dict[str, list[int]],
    symbol: str,
    ts: int,
    seconds: int = 1800,
) -> bool:
    values = signal_times.get(symbol, [])
    index = bisect_left(values, ts)
    return index < len(values) and values[index] <= ts + seconds


def analyze_candidates(
    conn: sqlite3.Connection,
    *,
    horizon_minutes: int,
    cooldown_minutes: int,
    max_lag_seconds: int,
    cost_pct: float,
) -> list[CandidateOutcome]:
    if not table_exists(conn, "watchlist_candidates") or not table_exists(
        conn, "market_snapshots"
    ):
        return []
    signal_times: dict[str, list[int]] = defaultdict(list)
    for symbol, ts in conn.execute("SELECT symbol, ts FROM signals ORDER BY ts"):
        signal_times[bare_symbol(str(symbol))].append(int(ts))

    series: dict[tuple[str, str], list[tuple[int, float]]] = defaultdict(list)
    for scanner, symbol, ts, price in conn.execute(
        """
        SELECT scanner, symbol, ts, price
        FROM market_snapshots
        WHERE price > 0
        ORDER BY scanner, symbol, ts
        """
    ):
        series[(str(scanner), str(symbol))].append((int(ts), float(price)))
    timestamps = {key: [item[0] for item in values] for key, values in series.items()}

    cooldown_seconds = cooldown_minutes * 60
    latest: dict[tuple[str, str], int] = {}
    outcomes: list[CandidateOutcome] = []
    rows = conn.execute(
        """
        SELECT id, scanner, symbol, ts, score, price, missing_checks
        FROM watchlist_candidates
        WHERE price > 0
        ORDER BY ts, id
        """
    )
    for candidate_id, scanner, symbol, ts, score, entry_price, missing_checks in rows:
        key = (str(scanner), str(symbol))
        previous = latest.get(key)
        latest[key] = int(ts)
        if previous is not None and int(ts) - previous < cooldown_seconds:
            continue
        points = series.get(key, [])
        point_times = timestamps.get(key, [])
        target_ts = int(ts) + horizon_minutes * 60
        index = bisect_left(point_times, target_ts)
        if index >= len(points) or points[index][0] > target_ts + max_lag_seconds:
            continue
        exit_price = points[index][1]
        gross = short_return(float(entry_price), exit_price)
        raw_symbol = bare_symbol(str(symbol))
        outcomes.append(
            CandidateOutcome(
                candidate_id=int(candidate_id),
                scanner=str(scanner),
                symbol=raw_symbol,
                ts=int(ts),
                score=int(score),
                missing_checks=str(missing_checks or ""),
                gross_return_pct=gross,
                net_return_pct=gross - cost_pct,
                matching_signal=has_signal_near(signal_times, raw_symbol, int(ts)),
            )
        )
    return outcomes


def csv_write(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def fmt(value: float) -> str:
    return f"{value:+.2f}%"


def metric_line(name: str, metrics: dict[str, Any]) -> str:
    profit_factor = metrics.get("profit_factor", 0)
    pf_text = "inf" if math.isinf(profit_factor) else f"{profit_factor:.2f}"
    return (
        f"| {name} | {metrics['n']} | {metrics['episodes']} | "
        f"{metrics['win_rate_pct']:.1f}% | {fmt(metrics['mean_net_pct'])} | "
        f"{fmt(metrics['median_net_pct'])} | {pf_text} | "
        f"{fmt(metrics['bootstrap_mean_low_pct'])} ... "
        f"{fmt(metrics['bootstrap_mean_high_pct'])} |"
    )


def automation_gate(
    *,
    current_trades: list[SimulatedTrade],
    coverage: dict[str, Any],
    paper: dict[str, Any],
) -> dict[str, Any]:
    test_trades = chronological_splits(current_trades)["test_20"]
    test_metrics = trade_metrics(test_trades)
    checks = [
        {
            "name": "current_events",
            "passed": len(current_trades) >= 200,
            "actual": len(current_trades),
            "required": ">= 200 independent reviewed events",
        },
        {
            "name": "execution_coverage",
            "passed": float(coverage["execution_coverage_pct"]) >= 98,
            "actual": float(coverage["execution_coverage_pct"]),
            "required": ">= 98% executable Bybit entry quotes",
        },
        {
            "name": "review_coverage",
            "passed": float(coverage["review_coverage_pct"]) >= 98,
            "actual": float(coverage["review_coverage_pct"]),
            "required": ">= 98% exact 4h reviews",
        },
        {
            "name": "test_expectancy",
            "passed": test_metrics["n"] > 0 and test_metrics["mean_net_pct"] > 0,
            "actual": test_metrics["mean_net_pct"],
            "required": "> 0% on untouched chronological test split",
        },
        {
            "name": "test_cluster_lower_bound",
            "passed": (
                test_metrics["n"] > 0
                and test_metrics["bootstrap_mean_low_pct"] > 0
            ),
            "actual": test_metrics["bootstrap_mean_low_pct"],
            "required": "> 0% cluster-bootstrap 95% lower bound",
        },
        {
            "name": "test_profit_factor",
            "passed": test_metrics["n"] > 0 and test_metrics["profit_factor"] >= 1.2,
            "actual": test_metrics["profit_factor"],
            "required": ">= 1.20 on untouched test split",
        },
        {
            "name": "test_without_top5",
            "passed": (
                test_metrics["n"] > 5
                and test_metrics["mean_without_top5_pct"] > 0
            ),
            "actual": test_metrics["mean_without_top5_pct"],
            "required": "> 0% after removing five best test trades",
        },
        {
            "name": "paper_observation",
            "passed": float(paper["observation_days"]) >= 30,
            "actual": float(paper["observation_days"]),
            "required": ">= 30 continuous paper days",
        },
        {
            "name": "paper_loop_integrity",
            "passed": int(paper["loop_error_count"]) == 0,
            "actual": int(paper["loop_error_count"]),
            "required": "0 paper broker loop/reconciliation errors",
        },
    ]
    return {
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
        "test_metrics": test_metrics,
    }


def gate_line(check: dict[str, Any]) -> str:
    status = "PASS" if check["passed"] else "FAIL"
    actual = check["actual"]
    if isinstance(actual, float):
        actual_text = f"{actual:.3f}"
    else:
        actual_text = str(actual)
    return f"| {check['name']} | {status} | {actual_text} | {check['required']} |"


def build_report(
    *,
    db_path: Path,
    args: argparse.Namespace,
    raw_events: list[SignalEvent],
    events: list[SignalEvent],
    variants: dict[str, list[SimulatedTrade]],
    candidate_outcomes: list[CandidateOutcome],
    portfolio: dict[str, Any],
    coverage: dict[str, Any],
    paper: dict[str, Any],
    gate: dict[str, Any],
) -> str:
    cost_pct = total_cost_pct(args)
    lines = [
        "# DUMP backtest report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"Database: `{db_path}`",
        "",
        "## Method",
        "",
        f"- Horizon: {args.horizon_minutes} minutes.",
        f"- Same-symbol cooldown: {args.cooldown_minutes} minutes.",
        f"- Market episode gap: {args.cluster_minutes} minutes.",
        f"- Round-trip execution cost: {cost_pct:.3f}% ",
        f"  ({args.entry_fee_bps:g}+{args.exit_fee_bps:g} bps fees, "
        f"{args.entry_slippage_bps:g}+{args.exit_slippage_bps:g} bps slippage, "
        f"funding {args.funding_bps:g} bps).",
        "- Positive return means profit for SHORT.",
        "- Decisions are evaluated only if they were recorded at that time; model versions are not merged.",
        "",
        "## Coverage",
        "",
        f"- Reviewed DUMP messages: {len(raw_events)}.",
        f"- Independent same-symbol events: {len(events)}.",
        f"- Market episode clusters: {len({event.episode_id for event in events})}.",
        f"- Current model: `{args.current_model_version}`; total signals={coverage['signals']}, "
        f"reviewed={coverage['reviewed']}, executable entries={coverage['executable_quotes']}.",
        "",
        "## Strategy variants",
        "",
        "| Variant | N | Episodes | Win rate | Mean net | Median net | Profit factor | Cluster bootstrap 95% CI |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, trades in variants.items():
        lines.append(metric_line(name, trade_metrics(trades)))

    lines.extend(
        [
            "",
            "## Outlier dependence",
            "",
            "| Variant | Mean net | Mean after removing five best trades |",
            "|---|---:|---:|",
        ]
    )
    for name, trades in variants.items():
        metrics = trade_metrics(trades)
        lines.append(
            f"| {name} | {fmt(metrics['mean_net_pct'])} | "
            f"{fmt(metrics['mean_without_top5_pct'])} |"
        )

    baseline = variants["time_exit"]
    lines.extend(
        [
            "",
            "## Chronological stability: time exit",
            "",
            "| Split | N | Episodes | Win rate | Mean net | Median net | Profit factor | Cluster bootstrap 95% CI |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for name, split in chronological_splits(baseline).items():
        lines.append(metric_line(name, trade_metrics(split)))

    lines.extend(
        [
            "",
            "## Model generations",
            "",
            "| Model | N | Episodes | Win rate | Mean net | Median net | Profit factor | Cluster bootstrap 95% CI |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for name, metrics in grouped_metrics(baseline, lambda trade: trade.model_version).items():
        lines.append(metric_line(name, metrics))

    lines.extend(
        [
            "",
            "## OI modes",
            "",
            "| Mode | N | Episodes | Win rate | Mean net | Median net | Profit factor | Cluster bootstrap 95% CI |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for name, metrics in grouped_metrics(baseline, lambda trade: trade.mode).items():
        lines.append(metric_line(name, metrics))

    missed = [
        outcome
        for outcome in candidate_outcomes
        if not outcome.matching_signal and outcome.gross_return_pct >= args.missed_move_pct
    ]
    missing_reasons = Counter(
        reason for outcome in missed for reason in split_checks(outcome.missing_checks)
    )
    lines.extend(
        [
            "",
            "## Rejected candidates with measurable outcomes",
            "",
            f"- Independent candidates with a valid {args.horizon_minutes}m outcome: {len(candidate_outcomes)}.",
            f"- No nearby signal and later SHORT move >= {args.missed_move_pct:g}%: {len(missed)}.",
            "- Most common missing checks in those missed moves: "
            + (", ".join(f"{name}={count}" for name, count in missing_reasons.most_common(8)) or "none"),
            "",
            "## Risk-limited portfolio simulation",
            "",
            f"- Starting equity: {portfolio['starting_equity']:.2f} USDT.",
            f"- Ending equity: {portfolio['ending_equity']:.2f} USDT ({portfolio['return_pct']:+.2f}%).",
            f"- Maximum drawdown: {portfolio['max_drawdown_pct']:.2f}%.",
            f"- Accepted trades: {portfolio['accepted_trades']}; skipped by capacity/correlation: "
            f"{portfolio['skipped_capacity_or_cluster']}.",
            "",
            "## Automation gate",
            "",
            f"**Overall result: {'PASS' if gate['passed'] else 'FAIL'}**",
            "",
            "| Check | Result | Actual | Required |",
            "|---|---|---:|---|",
            "",
        ]
    )
    lines.pop()
    lines.extend(gate_line(check) for check in gate["checks"])
    lines.append("")
    lines.extend(
        [
            f"Paper observation: {paper['observation_days']:.2f} days, "
            f"heartbeats={paper['heartbeat_count']}, quote errors={paper['quote_error_count']}, "
            f"loop errors={paper['loop_error_count']}.",
            "",
            "A FAIL keeps live order placement blocked. Historical 15-minute generations are "
            "diagnostic evidence, not validation of the current hourly model.",
        ]
    )
    return "\n".join(lines) + "\n"


def total_cost_pct(args: argparse.Namespace) -> float:
    return (
        args.entry_fee_bps
        + args.exit_fee_bps
        + args.entry_slippage_bps
        + args.exit_slippage_bps
        + args.funding_bps
    ) / 100


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("research/results/dump"))
    parser.add_argument("--current-model-version", default="dump-v5.2-confirmation")
    parser.add_argument("--horizon-minutes", type=int, default=240)
    parser.add_argument("--cooldown-minutes", type=int, default=240)
    parser.add_argument("--cluster-minutes", type=int, default=30)
    parser.add_argument("--review-max-lag-seconds", type=int, default=300)
    parser.add_argument("--entry-fee-bps", type=float, default=5.5)
    parser.add_argument("--exit-fee-bps", type=float, default=5.5)
    parser.add_argument("--entry-slippage-bps", type=float, default=5.0)
    parser.add_argument("--exit-slippage-bps", type=float, default=5.0)
    parser.add_argument("--funding-bps", type=float, default=1.0)
    parser.add_argument("--stop-loss-pct", type=float, default=2.0)
    parser.add_argument("--take-profit-pct", type=float, default=3.0)
    parser.add_argument("--trailing-activation-pct", type=float, default=2.0)
    parser.add_argument("--trailing-distance-pct", type=float, default=1.5)
    parser.add_argument("--starting-equity", type=float, default=10_000)
    parser.add_argument("--risk-per-trade-pct", type=float, default=0.5)
    parser.add_argument("--max-notional-pct", type=float, default=25.0)
    parser.add_argument("--max-concurrent-positions", type=int, default=3)
    parser.add_argument("--max-positions-per-episode", type=int, default=1)
    parser.add_argument("--missed-move-pct", type=float, default=3.0)
    parser.add_argument("--skip-candidates", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db_path = args.db.resolve()
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA query_only=ON")
    try:
        raw_events = load_events(conn, horizon_minutes=args.horizon_minutes)
        events = deduplicate_events(raw_events, args.cooldown_minutes)
        assign_episodes(events, args.cluster_minutes)
        paths = load_price_paths(conn, (event.signal_id for event in events))
        cost_pct = total_cost_pct(args)
        variants = {
            "time_exit": [
                simulate_event(
                    event,
                    raw_path=paths.get(event.signal_id, []),
                    horizon_minutes=args.horizon_minutes,
                    cost_pct=cost_pct,
                )
                for event in events
            ],
            "stop_only": [
                simulate_event(
                    event,
                    raw_path=paths.get(event.signal_id, []),
                    horizon_minutes=args.horizon_minutes,
                    cost_pct=cost_pct,
                    stop_loss_pct=args.stop_loss_pct,
                )
                for event in events
            ],
            "target_stop_path_only": [
                simulate_event(
                    event,
                    raw_path=paths.get(event.signal_id, []),
                    horizon_minutes=args.horizon_minutes,
                    cost_pct=cost_pct,
                    stop_loss_pct=args.stop_loss_pct,
                    take_profit_pct=args.take_profit_pct,
                    require_path=True,
                )
                for event in events
            ],
            "trailing_path_only": [
                simulate_event(
                    event,
                    raw_path=paths.get(event.signal_id, []),
                    horizon_minutes=args.horizon_minutes,
                    cost_pct=cost_pct,
                    stop_loss_pct=args.stop_loss_pct,
                    trailing_activation_pct=args.trailing_activation_pct,
                    trailing_distance_pct=args.trailing_distance_pct,
                    require_path=True,
                )
                for event in events
            ],
        }
        variants = {
            name: [trade for trade in trades if trade is not None]
            for name, trades in variants.items()
        }
        candidates = [] if args.skip_candidates else analyze_candidates(
            conn,
            horizon_minutes=args.horizon_minutes,
            cooldown_minutes=args.cooldown_minutes,
            max_lag_seconds=args.review_max_lag_seconds,
            cost_pct=cost_pct,
        )
        coverage = current_signal_coverage(
            conn,
            model_version=args.current_model_version,
            horizon_minutes=args.horizon_minutes,
        )
        paper = paper_observation(conn)
    finally:
        conn.close()

    portfolio = simulate_portfolio(
        variants["stop_only"],
        starting_equity=args.starting_equity,
        risk_per_trade_pct=args.risk_per_trade_pct,
        stop_loss_pct=args.stop_loss_pct,
        max_notional_pct=args.max_notional_pct,
        max_concurrent_positions=args.max_concurrent_positions,
        max_positions_per_episode=args.max_positions_per_episode,
    )
    current_trades = [
        trade
        for trade in variants["time_exit"]
        if trade.model_version == args.current_model_version
    ]
    gate = automation_gate(
        current_trades=current_trades,
        coverage=coverage,
        paper=paper,
    )
    report = build_report(
        db_path=db_path,
        args=args,
        raw_events=raw_events,
        events=events,
        variants=variants,
        candidate_outcomes=candidates,
        portfolio=portfolio,
        coverage=coverage,
        paper=paper,
        gate=gate,
    )
    report_path = args.out_dir / "report.md"
    report_path.write_text(report, encoding="utf-8")
    csv_write(args.out_dir / "events.csv", (asdict(event) for event in events))
    for name, trades in variants.items():
        csv_write(args.out_dir / f"trades-{name}.csv", (asdict(trade) for trade in trades))
    csv_write(args.out_dir / "candidate-outcomes.csv", (asdict(row) for row in candidates))
    summary = {
        "database": str(db_path),
        "settings": vars(args) | {"db": str(args.db), "out_dir": str(args.out_dir)},
        "variants": {name: trade_metrics(trades) for name, trades in variants.items()},
        "portfolio": {key: value for key, value in portfolio.items() if key != "equity_points"},
        "candidate_outcomes": len(candidates),
        "current_model_coverage": coverage,
        "paper_observation": paper,
        "automation_gate": gate,
    }
    (args.out_dir / "summary.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(report_path)


if __name__ == "__main__":
    main()
