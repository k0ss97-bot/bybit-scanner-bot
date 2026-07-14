from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable, Iterator


@dataclass(frozen=True)
class PaperStrategy:
    key: str
    label: str
    trailing_enabled: bool
    settings_json: str


class PaperBroker:
    """Local paper execution only. This class has no authenticated exchange methods."""

    def __init__(self, path: str, settings) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.settings = settings
        self.enabled = bool(settings.paper_trading_enabled)
        self.strategies = self._build_strategies()
        runtime_source = "|".join(strategy.key for strategy in self.strategies)
        self.runtime_key = hashlib.sha256(runtime_source.encode("utf-8")).hexdigest()[:16]
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _strategy_settings(self, trailing_enabled: bool) -> dict[str, Any]:
        return {
            "version": "paper-v1",
            "side": "SHORT",
            "poll_interval_seconds": self.settings.paper_poll_interval_seconds,
            "quote_max_age_seconds": self.settings.dump_execution_quote_max_age_seconds,
            "starting_equity_usdt": self.settings.paper_starting_equity_usdt,
            "stop_loss_pct": self.settings.paper_stop_loss_pct,
            "max_holding_minutes": self.settings.paper_max_holding_minutes,
            "risk_per_trade_pct": self.settings.paper_risk_per_trade_pct,
            "max_notional_pct": self.settings.paper_max_notional_pct,
            "max_open_positions": self.settings.paper_max_open_positions,
            "episode_cooldown_minutes": self.settings.paper_episode_cooldown_minutes,
            "entry_fee_bps": self.settings.paper_entry_fee_bps,
            "exit_fee_bps": self.settings.paper_exit_fee_bps,
            "slippage_bps": self.settings.paper_slippage_bps,
            "funding_buffer_bps": self.settings.paper_funding_buffer_bps,
            "trailing_enabled": trailing_enabled,
            "trailing_activation_pct": (
                self.settings.paper_trailing_activation_pct if trailing_enabled else 0
            ),
            "trailing_distance_pct": (
                self.settings.paper_trailing_distance_pct if trailing_enabled else 0
            ),
        }

    def _build_strategies(self) -> tuple[PaperStrategy, ...]:
        strategies = []
        for label, trailing_enabled in (
            (
                f"STOP + выход через {self.settings.paper_max_holding_minutes} минут",
                False,
            ),
            (
                f"STOP + trailing, максимум {self.settings.paper_max_holding_minutes} минут",
                True,
            ),
        ):
            values = self._strategy_settings(trailing_enabled)
            settings_json = json.dumps(
                values,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            digest = hashlib.sha256(settings_json.encode("utf-8")).hexdigest()[:10]
            strategies.append(
                PaperStrategy(
                    key=f"paper-v1-{'trail' if trailing_enabled else 'time'}-{digest}",
                    label=label,
                    trailing_enabled=trailing_enabled,
                    settings_json=settings_json,
                )
            )
        return tuple(strategies)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_accounts (
                    strategy TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    settings_json TEXT NOT NULL,
                    starting_equity REAL NOT NULL,
                    equity REAL NOT NULL,
                    peak_equity REAL NOT NULL,
                    max_drawdown_pct REAL NOT NULL DEFAULT 0,
                    created_ts INTEGER NOT NULL,
                    updated_ts INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy TEXT NOT NULL,
                    signal_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    status TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    opened_ts INTEGER NOT NULL,
                    entry_quote_ts INTEGER NOT NULL,
                    entry_price REAL NOT NULL,
                    quantity REAL NOT NULL,
                    notional REAL NOT NULL,
                    stop_price REAL NOT NULL,
                    best_ask REAL NOT NULL,
                    trailing_activation_pct REAL NOT NULL,
                    trailing_distance_pct REAL NOT NULL,
                    max_holding_minutes INTEGER NOT NULL,
                    entry_fee_bps REAL NOT NULL,
                    exit_fee_bps REAL NOT NULL,
                    exit_slippage_bps REAL NOT NULL,
                    funding_buffer_bps REAL NOT NULL,
                    last_quote_ts INTEGER NOT NULL DEFAULT 0,
                    last_ask REAL NOT NULL DEFAULT 0,
                    closed_ts INTEGER NOT NULL DEFAULT 0,
                    exit_price REAL NOT NULL DEFAULT 0,
                    exit_reason TEXT NOT NULL DEFAULT '',
                    gross_pnl REAL NOT NULL DEFAULT 0,
                    fees REAL NOT NULL DEFAULT 0,
                    net_pnl REAL NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    UNIQUE(strategy, signal_id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_paper_positions_status_symbol
                ON paper_positions(status, symbol)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_runtime (
                    runtime_key TEXT PRIMARY KEY,
                    settings_json TEXT NOT NULL,
                    observation_start_ts INTEGER NOT NULL,
                    last_heartbeat_ts INTEGER NOT NULL,
                    heartbeat_count INTEGER NOT NULL DEFAULT 0,
                    quote_error_count INTEGER NOT NULL DEFAULT 0,
                    loop_error_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            now = int(time.time())
            for strategy in self.strategies:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO paper_accounts (
                        strategy, label, settings_json, starting_equity,
                        equity, peak_equity, created_ts, updated_ts
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        strategy.key,
                        strategy.label,
                        strategy.settings_json,
                        self.settings.paper_starting_equity_usdt,
                        self.settings.paper_starting_equity_usdt,
                        self.settings.paper_starting_equity_usdt,
                        now,
                        now,
                    ),
                )
            runtime_settings = json.dumps(
                [strategy.settings_json for strategy in self.strategies],
                ensure_ascii=True,
                separators=(",", ":"),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO paper_runtime (
                    runtime_key, settings_json, observation_start_ts,
                    last_heartbeat_ts
                )
                VALUES (?, ?, ?, ?)
                """,
                (self.runtime_key, runtime_settings, now, now),
            )

    def record_heartbeat(
        self,
        *,
        now: int | None = None,
        quote_errors: int = 0,
        loop_errors: int = 0,
    ) -> None:
        if not self.enabled:
            return
        heartbeat_ts = now or int(time.time())
        continuity_limit = max(3_600, self.settings.paper_poll_interval_seconds * 20)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT observation_start_ts, last_heartbeat_ts,
                       heartbeat_count, quote_error_count, loop_error_count
                FROM paper_runtime
                WHERE runtime_key = ?
                """,
                (self.runtime_key,),
            ).fetchone()
            if row is None:
                return
            gap = max(0, heartbeat_ts - int(row["last_heartbeat_ts"]))
            if gap > continuity_limit:
                observation_start_ts = heartbeat_ts
                heartbeat_count = 0
                quote_error_count = 0
                loop_error_count = 0
            else:
                observation_start_ts = int(row["observation_start_ts"])
                heartbeat_count = int(row["heartbeat_count"])
                quote_error_count = int(row["quote_error_count"])
                loop_error_count = int(row["loop_error_count"])
            conn.execute(
                """
                UPDATE paper_runtime
                SET observation_start_ts = ?, last_heartbeat_ts = ?,
                    heartbeat_count = ?, quote_error_count = ?,
                    loop_error_count = ?
                WHERE runtime_key = ?
                """,
                (
                    observation_start_ts,
                    heartbeat_ts,
                    heartbeat_count + 1,
                    quote_error_count + max(0, quote_errors),
                    loop_error_count + max(0, loop_errors),
                    self.runtime_key,
                ),
            )

    def open_signal(self, *, signal_id: int, signal, opened_ts: int) -> list[str]:
        if not self.enabled:
            return []
        bid = float(getattr(signal, "entry_bid", 0) or getattr(signal, "entry_price", 0))
        quote_ts = int(getattr(signal, "entry_quote_ts", opened_ts))
        if bid <= 0:
            return ["missing_entry_bid"]
        symbol = str(getattr(signal, "symbol", ""))
        results = []
        for strategy in self.strategies:
            results.append(
                self._open_for_strategy(
                    strategy=strategy,
                    signal_id=signal_id,
                    signal=signal,
                    symbol=symbol,
                    bid=bid,
                    quote_ts=quote_ts,
                    opened_ts=opened_ts,
                )
            )
        return results

    def _open_for_strategy(
        self,
        *,
        strategy: PaperStrategy,
        signal_id: int,
        signal,
        symbol: str,
        bid: float,
        quote_ts: int,
        opened_ts: int,
    ) -> str:
        entry_price = bid * (1 - self.settings.paper_slippage_bps / 10_000)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT status FROM paper_positions WHERE strategy = ? AND signal_id = ?",
                (strategy.key, signal_id),
            ).fetchone()
            if existing is not None:
                return str(existing["status"])
            account = conn.execute(
                "SELECT equity FROM paper_accounts WHERE strategy = ?",
                (strategy.key,),
            ).fetchone()
            if account is None:
                return "missing_account"
            open_count = conn.execute(
                "SELECT COUNT(*) FROM paper_positions WHERE strategy = ? AND status = 'open'",
                (strategy.key,),
            ).fetchone()[0]
            status = "open"
            if int(open_count) >= self.settings.paper_max_open_positions:
                status = "skipped_capacity"
            cooldown_cutoff = opened_ts - self.settings.paper_episode_cooldown_minutes * 60
            recent_episode = conn.execute(
                """
                SELECT 1 FROM paper_positions
                WHERE strategy = ?
                  AND status IN ('open', 'closed')
                  AND opened_ts >= ?
                LIMIT 1
                """,
                (strategy.key, cooldown_cutoff),
            ).fetchone()
            if recent_episode is not None:
                status = "skipped_correlated_episode"

            equity = float(account["equity"])
            if equity <= 0:
                status = "skipped_no_equity"
            risk_notional = equity * (
                self.settings.paper_risk_per_trade_pct
                / self.settings.paper_stop_loss_pct
            )
            max_notional = equity * self.settings.paper_max_notional_pct / 100
            notional = min(risk_notional, max_notional) if status == "open" else 0.0
            quantity = notional / entry_price if entry_price > 0 else 0.0
            stop_price = entry_price * (1 + self.settings.paper_stop_loss_pct / 100)
            conn.execute(
                """
                INSERT INTO paper_positions (
                    strategy, signal_id, symbol, side, status, model_version,
                    mode, score, opened_ts, entry_quote_ts, entry_price,
                    quantity, notional, stop_price, best_ask,
                    trailing_activation_pct, trailing_distance_pct,
                    max_holding_minutes, entry_fee_bps, exit_fee_bps,
                    exit_slippage_bps, funding_buffer_bps
                )
                VALUES (
                    ?, ?, ?, 'SHORT', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?
                )
                """,
                (
                    strategy.key,
                    signal_id,
                    symbol,
                    status,
                    str(getattr(signal, "model_version", "")),
                    str(getattr(signal, "mode", "")),
                    int(getattr(signal, "signal_score", 0)),
                    opened_ts,
                    quote_ts,
                    entry_price,
                    quantity,
                    notional,
                    stop_price,
                    entry_price,
                    (
                        self.settings.paper_trailing_activation_pct
                        if strategy.trailing_enabled
                        else 0
                    ),
                    (
                        self.settings.paper_trailing_distance_pct
                        if strategy.trailing_enabled
                        else 0
                    ),
                    self.settings.paper_max_holding_minutes,
                    self.settings.paper_entry_fee_bps,
                    self.settings.paper_exit_fee_bps,
                    self.settings.paper_slippage_bps,
                    self.settings.paper_funding_buffer_bps,
                ),
            )
            return status

    def open_symbols(self) -> list[str]:
        if not self.enabled:
            return []
        with self._connect() as conn:
            return [
                str(row[0])
                for row in conn.execute(
                    "SELECT DISTINCT symbol FROM paper_positions WHERE status = 'open'"
                )
            ]

    def update_open_positions(
        self,
        quote_provider: Callable[[str], Any],
        *,
        now: int | None = None,
    ) -> tuple[int, int]:
        if not self.enabled:
            return 0, 0
        observed_ts = now or int(time.time())
        updated = 0
        closed = 0
        quote_errors = 0
        for symbol in self.open_symbols():
            try:
                quote = quote_provider(symbol)
                quote_ts = int(
                    getattr(quote, "ts", 0)
                    or getattr(quote, "quote_ts", 0)
                    or observed_ts
                )
                age = max(0, observed_ts - quote_ts)
                if age > self.settings.dump_execution_quote_max_age_seconds:
                    raise RuntimeError(f"stale quote age={age}s")
                update_count, close_count = self._apply_quote(
                    symbol=symbol,
                    quote_ts=quote_ts,
                    raw_ask=float(getattr(quote, "ask_price")),
                    observed_ts=observed_ts,
                )
                updated += update_count
                closed += close_count
            except Exception as error:
                quote_errors += 1
                with self._connect() as conn:
                    conn.execute(
                        """
                        UPDATE paper_positions
                        SET last_error = ?
                        WHERE status = 'open' AND symbol = ?
                        """,
                        (str(error)[:300], symbol),
                    )
        self.record_heartbeat(now=observed_ts, quote_errors=quote_errors)
        return updated, closed

    def _apply_quote(
        self,
        *,
        symbol: str,
        quote_ts: int,
        raw_ask: float,
        observed_ts: int,
    ) -> tuple[int, int]:
        updated = 0
        closed = 0
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM paper_positions
                WHERE status = 'open' AND symbol = ?
                ORDER BY id
                """,
                (symbol,),
            ).fetchall()
        for row in rows:
            ask = raw_ask * (1 + float(row["exit_slippage_bps"]) / 10_000)
            best_ask = min(float(row["best_ask"]), ask)
            best_move_pct = (
                (float(row["entry_price"]) - best_ask) / float(row["entry_price"]) * 100
            )
            exit_reason = ""
            if ask >= float(row["stop_price"]):
                exit_reason = "stop"
            activation = float(row["trailing_activation_pct"])
            distance = float(row["trailing_distance_pct"])
            if not exit_reason and activation > 0 and best_move_pct >= activation:
                trailing_stop = best_ask * (1 + distance / 100)
                if ask >= trailing_stop:
                    exit_reason = "trailing"
            if (
                not exit_reason
                and observed_ts >= int(row["opened_ts"]) + int(row["max_holding_minutes"]) * 60
            ):
                exit_reason = "time"

            if exit_reason:
                if self._close_position(
                    position_id=int(row["id"]),
                    strategy=str(row["strategy"]),
                    quote_ts=quote_ts,
                    exit_price=ask,
                    exit_reason=exit_reason,
                    best_ask=best_ask,
                ):
                    closed += 1
            else:
                with self._connect() as conn:
                    cursor = conn.execute(
                        """
                        UPDATE paper_positions
                        SET best_ask = ?, last_quote_ts = ?, last_ask = ?, last_error = ''
                        WHERE id = ? AND status = 'open'
                        """,
                        (best_ask, quote_ts, ask, int(row["id"])),
                    )
                    updated += max(0, cursor.rowcount)
        return updated, closed

    def _close_position(
        self,
        *,
        position_id: int,
        strategy: str,
        quote_ts: int,
        exit_price: float,
        exit_reason: str,
        best_ask: float,
    ) -> bool:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM paper_positions WHERE id = ? AND status = 'open'",
                (position_id,),
            ).fetchone()
            if row is None:
                return False
            quantity = float(row["quantity"])
            entry_price = float(row["entry_price"])
            gross_pnl = quantity * (entry_price - exit_price)
            entry_fee = float(row["notional"]) * float(row["entry_fee_bps"]) / 10_000
            exit_fee = quantity * exit_price * float(row["exit_fee_bps"]) / 10_000
            funding_buffer = (
                float(row["notional"]) * float(row["funding_buffer_bps"]) / 10_000
            )
            fees = entry_fee + exit_fee + funding_buffer
            net_pnl = gross_pnl - fees
            cursor = conn.execute(
                """
                UPDATE paper_positions
                SET status = 'closed', closed_ts = ?, exit_price = ?,
                    exit_reason = ?, gross_pnl = ?, fees = ?, net_pnl = ?,
                    best_ask = ?, last_quote_ts = ?, last_ask = ?, last_error = ''
                WHERE id = ? AND status = 'open'
                """,
                (
                    quote_ts,
                    exit_price,
                    exit_reason,
                    gross_pnl,
                    fees,
                    net_pnl,
                    best_ask,
                    quote_ts,
                    exit_price,
                    position_id,
                ),
            )
            if cursor.rowcount != 1:
                return False
            account = conn.execute(
                "SELECT equity, peak_equity, max_drawdown_pct FROM paper_accounts WHERE strategy = ?",
                (strategy,),
            ).fetchone()
            equity = float(account["equity"]) + net_pnl
            peak = max(float(account["peak_equity"]), equity)
            drawdown = ((peak - equity) / peak * 100) if peak > 0 else 0.0
            conn.execute(
                """
                UPDATE paper_accounts
                SET equity = ?, peak_equity = ?, max_drawdown_pct = ?, updated_ts = ?
                WHERE strategy = ?
                """,
                (
                    equity,
                    peak,
                    max(float(account["max_drawdown_pct"]), drawdown),
                    quote_ts,
                    strategy,
                ),
            )
            return True

    def summary(self) -> list[dict[str, Any]]:
        strategy_keys = [strategy.key for strategy in self.strategies]
        placeholders = ",".join("?" for _ in strategy_keys)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    a.strategy, a.label, a.starting_equity, a.equity,
                    a.max_drawdown_pct,
                    SUM(CASE WHEN p.status = 'open' THEN 1 ELSE 0 END) AS open_count,
                    SUM(CASE WHEN p.status = 'closed' THEN 1 ELSE 0 END) AS closed_count,
                    SUM(CASE WHEN p.status LIKE 'skipped_%' THEN 1 ELSE 0 END) AS skipped_count,
                    SUM(CASE WHEN p.status = 'closed' AND p.net_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN p.status = 'closed' THEN p.net_pnl ELSE 0 END) AS net_pnl
                FROM paper_accounts a
                LEFT JOIN paper_positions p ON p.strategy = a.strategy
                WHERE a.strategy IN ({placeholders})
                GROUP BY a.strategy
                ORDER BY a.label
                """,
                strategy_keys,
            ).fetchall()
        return [dict(row) for row in rows]

    def runtime_summary(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT observation_start_ts, last_heartbeat_ts,
                       heartbeat_count, quote_error_count, loop_error_count
                FROM paper_runtime
                WHERE runtime_key = ?
                """,
                (self.runtime_key,),
            ).fetchone()
        if row is None:
            return {
                "observation_days": 0.0,
                "heartbeat_count": 0,
                "quote_error_count": 0,
                "loop_error_count": 0,
            }
        return {
            "observation_days": max(
                0,
                int(row["last_heartbeat_ts"]) - int(row["observation_start_ts"]),
            )
            / 86_400,
            "heartbeat_count": int(row["heartbeat_count"]),
            "quote_error_count": int(row["quote_error_count"]),
            "loop_error_count": int(row["loop_error_count"]),
        }

    def position_summary(self, *, limit: int = 6) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        strategy_keys = [strategy.key for strategy in self.strategies]
        placeholders = ",".join("?" for _ in strategy_keys)
        with self._connect() as conn:
            opened = conn.execute(
                f"""
                SELECT p.symbol, a.label, p.opened_ts, p.entry_price,
                       p.stop_price, p.best_ask, p.last_ask
                FROM paper_positions p
                JOIN paper_accounts a ON a.strategy = p.strategy
                WHERE p.strategy IN ({placeholders}) AND p.status = 'open'
                ORDER BY p.opened_ts DESC
                LIMIT ?
                """,
                [*strategy_keys, limit],
            ).fetchall()
            closed = conn.execute(
                f"""
                SELECT p.symbol, a.label, p.closed_ts, p.exit_reason, p.net_pnl
                FROM paper_positions p
                JOIN paper_accounts a ON a.strategy = p.strategy
                WHERE p.strategy IN ({placeholders}) AND p.status = 'closed'
                ORDER BY p.closed_ts DESC
                LIMIT ?
                """,
                [*strategy_keys, limit],
            ).fetchall()
        return [dict(row) for row in opened], [dict(row) for row in closed]


def format_paper_summary(broker: PaperBroker) -> str:
    if not broker.enabled:
        return "Paper trading отключен. Реальные ордера также не включены."
    runtime = broker.runtime_summary()
    lines = [
        "Paper trading: виртуальные сделки, реальные деньги не используются.\n"
        f"Непрерывное наблюдение: {runtime['observation_days']:.1f} дн., "
        f"проверок: {runtime['heartbeat_count']}, "
        f"ошибок котировок: {runtime['quote_error_count']}, "
        f"ошибок цикла: {runtime['loop_error_count']}."
    ]
    for row in broker.summary():
        closed = int(row["closed_count"] or 0)
        wins = int(row["wins"] or 0)
        win_rate = wins / closed * 100 if closed else 0.0
        return_pct = (
            (float(row["equity"]) - float(row["starting_equity"]))
            / float(row["starting_equity"])
            * 100
        )
        lines.append(
            "\n"
            f"{row['label']}\n"
            f"Баланс: {float(row['equity']):.2f} USDT ({return_pct:+.2f}%)\n"
            f"Открыто: {int(row['open_count'] or 0)}, закрыто: {closed}, "
            f"пропущено по риску: {int(row['skipped_count'] or 0)}\n"
            f"Win rate: {win_rate:.1f}%, max drawdown: "
            f"{float(row['max_drawdown_pct']):.2f}%"
        )
    opened, closed = broker.position_summary(limit=4)
    now = int(time.time())
    if opened:
        lines.append("\nОткрытые виртуальные позиции:")
        for row in opened:
            age_minutes = max(0, int((now - int(row["opened_ts"])) / 60))
            last_ask = float(row["last_ask"] or 0)
            last_text = f", ask {last_ask:g}" if last_ask > 0 else ""
            lines.append(
                f"{row['symbol']} | {row['label']}: вход {float(row['entry_price']):g}, "
                f"стоп {float(row['stop_price']):g}{last_text}, {age_minutes} мин."
            )
    if closed:
        reason_names = {"stop": "стоп", "trailing": "trailing", "time": "время"}
        lines.append("\nПоследние закрытые:")
        for row in closed:
            age_minutes = max(0, int((now - int(row["closed_ts"])) / 60))
            reason = reason_names.get(str(row["exit_reason"]), str(row["exit_reason"]))
            lines.append(
                f"{row['symbol']} | {row['label']}: {float(row['net_pnl']):+.2f} USDT, "
                f"{reason}, {age_minutes} мин назад."
            )
    lines.append("\nLIVE trading: заблокирован до прохождения automation gate.")
    return "\n".join(lines)
