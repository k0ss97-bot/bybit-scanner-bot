from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time


HISTORY_SCHEMA_VERSION = "scanner-history-v5.1-measurement"
REVIEW_SCHEMA_VERSION = "exact-post-target-v1-15-30-60-240"
REVIEW_METRICS_VERSION = "bybit-bid-ask-v1"


class HistoryStore:
    def __init__(self, path: str = "data/scanner.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._last_cleanup_ts = 0
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS market_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scanner TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    ts INTEGER NOT NULL,
                    price REAL NOT NULL,
                    open_interest REAL NOT NULL,
                    futures_cvd REAL NOT NULL,
                    spot_cvd REAL NOT NULL,
                    funding REAL NOT NULL,
                    turnover_24h REAL NOT NULL,
                    new_futures_trades INTEGER NOT NULL,
                    new_spot_trades INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_market_snapshots_symbol_ts
                ON market_snapshots(symbol, ts)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_market_snapshots_scanner_symbol_ts
                ON market_snapshots(scanner, symbol, ts)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_market_snapshots_ts
                ON market_snapshots(ts)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_type TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    ts INTEGER NOT NULL,
                    price REAL NOT NULL,
                    open_interest_change_pct REAL NOT NULL,
                    futures_cvd_change_pct REAL NOT NULL,
                    futures_cvd_delta_usdt REAL NOT NULL,
                    spot_cvd_change_pct REAL NOT NULL,
                    spot_cvd_delta_usdt REAL NOT NULL,
                    price_change_pct REAL NOT NULL,
                    payload TEXT NOT NULL,
                    model_version TEXT NOT NULL DEFAULT '',
                    settings_snapshot TEXT NOT NULL DEFAULT '{}',
                    market_observed_ts INTEGER NOT NULL DEFAULT 0,
                    decision_ts INTEGER NOT NULL DEFAULT 0,
                    telegram_sent_ts INTEGER NOT NULL DEFAULT 0,
                    market_price REAL NOT NULL DEFAULT 0,
                    entry_quote_ts INTEGER NOT NULL DEFAULT 0,
                    entry_bid REAL NOT NULL DEFAULT 0,
                    entry_ask REAL NOT NULL DEFAULT 0,
                    entry_price REAL NOT NULL DEFAULT 0,
                    entry_spread_bps REAL NOT NULL DEFAULT 0,
                    entry_quote_status TEXT NOT NULL DEFAULT 'legacy',
                    execution_venue TEXT NOT NULL DEFAULT '',
                    detection_source TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL DEFAULT '',
                    score INTEGER NOT NULL DEFAULT 0,
                    turnover_24h REAL NOT NULL DEFAULT 0,
                    confirmation_age_seconds INTEGER NOT NULL DEFAULT 0,
                    cvd_complete INTEGER NOT NULL DEFAULT 0,
                    confirmation_cvd_complete INTEGER NOT NULL DEFAULT 0,
                    cvd_coverage_seconds INTEGER NOT NULL DEFAULT 0,
                    confirmation_cvd_coverage_seconds INTEGER NOT NULL DEFAULT 0,
                    build_commit TEXT NOT NULL DEFAULT '',
                    config_hash TEXT NOT NULL DEFAULT '',
                    schema_version TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS signal_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id INTEGER NOT NULL,
                    horizon_minutes INTEGER NOT NULL,
                    reviewed_ts INTEGER NOT NULL,
                    price_at_review REAL NOT NULL,
                    move_pct REAL NOT NULL,
                    max_favorable_pct REAL NOT NULL,
                    max_adverse_pct REAL NOT NULL,
                    target_ts INTEGER NOT NULL DEFAULT 0,
                    price_ts INTEGER NOT NULL DEFAULT 0,
                    lag_seconds INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'legacy',
                    missing_reason TEXT NOT NULL DEFAULT '',
                    execution_venue TEXT NOT NULL DEFAULT '',
                    UNIQUE(signal_id, horizon_minutes)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_signal_reviews_signal_id
                ON signal_reviews(signal_id)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS signal_price_snapshots (
                    signal_id INTEGER NOT NULL,
                    ts INTEGER NOT NULL,
                    price REAL NOT NULL,
                    venue TEXT NOT NULL DEFAULT '',
                    bid REAL NOT NULL DEFAULT 0,
                    ask REAL NOT NULL DEFAULT 0,
                    quote_status TEXT NOT NULL DEFAULT 'legacy',
                    UNIQUE(signal_id, ts)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_signal_price_snapshots_signal_ts
                ON signal_price_snapshots(signal_id, ts)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS signal_entry_scenarios (
                    signal_id INTEGER NOT NULL,
                    delay_seconds INTEGER NOT NULL,
                    target_ts INTEGER NOT NULL,
                    quote_ts INTEGER NOT NULL DEFAULT 0,
                    bid REAL NOT NULL DEFAULT 0,
                    ask REAL NOT NULL DEFAULT 0,
                    spread_bps REAL NOT NULL DEFAULT 0,
                    lag_seconds INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    UNIQUE(signal_id, delay_seconds)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_signal_entry_scenarios_signal
                ON signal_entry_scenarios(signal_id, delay_seconds)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS watchlist_candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scanner TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    ts INTEGER NOT NULL,
                    score INTEGER NOT NULL,
                    price REAL NOT NULL,
                    passed_checks TEXT NOT NULL,
                    missing_checks TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_watchlist_candidates_scanner_ts
                ON watchlist_candidates(scanner, ts)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_watchlist_candidates_scanner_symbol_ts
                ON watchlist_candidates(scanner, symbol, ts)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_watchlist_candidates_ts
                ON watchlist_candidates(ts)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scanner_evaluations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scanner TEXT NOT NULL,
                    source TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    ts INTEGER NOT NULL,
                    source_rank INTEGER NOT NULL,
                    selected INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    price REAL NOT NULL,
                    turnover_24h REAL NOT NULL,
                    price_growth_lookback_pct REAL NOT NULL,
                    drawdown_from_high_pct REAL NOT NULL,
                    oi_change_pct REAL NOT NULL,
                    cvd_delta_usdt REAL NOT NULL,
                    price_change_window_pct REAL NOT NULL,
                    funding_rate REAL NOT NULL,
                    passed_checks TEXT NOT NULL,
                    missing_checks TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    model_version TEXT NOT NULL DEFAULT '',
                    UNIQUE(scanner, symbol)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_scanner_evaluations_scanner_status_rank
                ON scanner_evaluations(scanner, status, source_rank)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_scanner_evaluations_ts
                ON scanner_evaluations(ts)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scanner_evaluation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scanner TEXT NOT NULL,
                    source TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    ts INTEGER NOT NULL,
                    bucket_ts INTEGER NOT NULL,
                    source_rank INTEGER NOT NULL,
                    selected INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    price REAL NOT NULL,
                    turnover_24h REAL NOT NULL,
                    price_growth_lookback_pct REAL NOT NULL,
                    drawdown_from_high_pct REAL NOT NULL,
                    oi_change_pct REAL NOT NULL,
                    cvd_delta_usdt REAL NOT NULL,
                    price_change_window_pct REAL NOT NULL,
                    funding_rate REAL NOT NULL,
                    passed_checks TEXT NOT NULL,
                    missing_checks TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    UNIQUE(scanner, symbol, bucket_ts)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_scanner_evaluation_history_scanner_ts
                ON scanner_evaluation_history(scanner, ts)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_scanner_evaluation_history_symbol_ts
                ON scanner_evaluation_history(symbol, ts)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dump_symbol_cooldowns (
                    symbol TEXT PRIMARY KEY,
                    ts INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    score INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_symbol_cooldowns (
                    symbol TEXT PRIMARY KEY,
                    ts INTEGER NOT NULL,
                    signal_type TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS app_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            self._ensure_column(
                conn,
                table="signals",
                column="model_version",
                definition="TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn,
                table="signals",
                column="settings_snapshot",
                definition="TEXT NOT NULL DEFAULT '{}'",
            )
            signal_columns = {
                "market_observed_ts": "INTEGER NOT NULL DEFAULT 0",
                "decision_ts": "INTEGER NOT NULL DEFAULT 0",
                "telegram_sent_ts": "INTEGER NOT NULL DEFAULT 0",
                "market_price": "REAL NOT NULL DEFAULT 0",
                "entry_quote_ts": "INTEGER NOT NULL DEFAULT 0",
                "entry_bid": "REAL NOT NULL DEFAULT 0",
                "entry_ask": "REAL NOT NULL DEFAULT 0",
                "entry_price": "REAL NOT NULL DEFAULT 0",
                "entry_spread_bps": "REAL NOT NULL DEFAULT 0",
                "entry_quote_status": "TEXT NOT NULL DEFAULT 'legacy'",
                "execution_venue": "TEXT NOT NULL DEFAULT ''",
                "detection_source": "TEXT NOT NULL DEFAULT ''",
                "mode": "TEXT NOT NULL DEFAULT ''",
                "score": "INTEGER NOT NULL DEFAULT 0",
                "turnover_24h": "REAL NOT NULL DEFAULT 0",
                "confirmation_age_seconds": "INTEGER NOT NULL DEFAULT 0",
                "cvd_complete": "INTEGER NOT NULL DEFAULT 0",
                "confirmation_cvd_complete": "INTEGER NOT NULL DEFAULT 0",
                "cvd_coverage_seconds": "INTEGER NOT NULL DEFAULT 0",
                "confirmation_cvd_coverage_seconds": "INTEGER NOT NULL DEFAULT 0",
                "build_commit": "TEXT NOT NULL DEFAULT ''",
                "config_hash": "TEXT NOT NULL DEFAULT ''",
                "schema_version": "TEXT NOT NULL DEFAULT ''",
            }
            for column, definition in signal_columns.items():
                self._ensure_column(
                    conn,
                    table="signals",
                    column=column,
                    definition=definition,
                )
            review_columns = {
                "target_ts": "INTEGER NOT NULL DEFAULT 0",
                "price_ts": "INTEGER NOT NULL DEFAULT 0",
                "lag_seconds": "INTEGER NOT NULL DEFAULT 0",
                "status": "TEXT NOT NULL DEFAULT 'legacy'",
                "missing_reason": "TEXT NOT NULL DEFAULT ''",
                "execution_venue": "TEXT NOT NULL DEFAULT ''",
            }
            for column, definition in review_columns.items():
                self._ensure_column(
                    conn,
                    table="signal_reviews",
                    column=column,
                    definition=definition,
                )
            snapshot_columns = {
                "venue": "TEXT NOT NULL DEFAULT ''",
                "bid": "REAL NOT NULL DEFAULT 0",
                "ask": "REAL NOT NULL DEFAULT 0",
                "quote_status": "TEXT NOT NULL DEFAULT 'legacy'",
            }
            for column, definition in snapshot_columns.items():
                self._ensure_column(
                    conn,
                    table="signal_price_snapshots",
                    column=column,
                    definition=definition,
                )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_signals_model_version_ts
                ON signals(model_version, ts)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_signal_price_snapshots_venue_ts
                ON signal_price_snapshots(signal_id, venue, ts)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_signal_reviews_status
                ON signal_reviews(status, horizon_minutes)
                """
            )
            self._ensure_column(
                conn,
                table="scanner_evaluations",
                column="model_version",
                definition="TEXT NOT NULL DEFAULT ''",
            )
            self._migrate_signal_reviews(conn)
            self._migrate_review_metrics(conn)
            conn.execute(
                """
                INSERT OR REPLACE INTO app_meta (key, value)
                VALUES ('history_schema_version', ?)
                """,
                (HISTORY_SCHEMA_VERSION,),
            )

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection,
        *,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _migrate_signal_reviews(self, conn: sqlite3.Connection) -> None:
        row = conn.execute(
            "SELECT value FROM app_meta WHERE key = ?",
            ("signal_reviews_version",),
        ).fetchone()
        current_version = row[0] if row else ""
        if current_version == REVIEW_SCHEMA_VERSION:
            return

        conn.execute(
            """
            INSERT OR REPLACE INTO app_meta (key, value)
            VALUES (?, ?)
            """,
            ("signal_reviews_version", REVIEW_SCHEMA_VERSION),
        )

    def _migrate_review_metrics(self, conn: sqlite3.Connection) -> None:
        row = conn.execute(
            "SELECT value FROM app_meta WHERE key = ?",
            ("review_metrics_version",),
        ).fetchone()
        if row and row[0] == REVIEW_METRICS_VERSION:
            return

        conn.execute(
            """
            UPDATE signal_reviews
            SET max_favorable_pct = MAX(0, max_favorable_pct),
                max_adverse_pct = MAX(0, max_adverse_pct)
            """
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO app_meta (key, value)
            VALUES (?, ?)
            """,
            ("review_metrics_version", REVIEW_METRICS_VERSION),
        )

    def cleanup_old_data(
        self,
        *,
        now: int | None = None,
        snapshot_retention_days: int = 7,
        watchlist_retention_days: int = 7,
        min_interval_seconds: int = 3600,
    ) -> int:
        now = now or int(time.time())
        if now - self._last_cleanup_ts < min_interval_seconds:
            return 0

        self._last_cleanup_ts = now
        deleted = 0
        with self._connect() as conn:
            if snapshot_retention_days > 0:
                snapshot_cutoff = now - snapshot_retention_days * 24 * 60 * 60
                cursor = conn.execute(
                    "DELETE FROM market_snapshots WHERE ts < ?",
                    (snapshot_cutoff,),
                )
                deleted += cursor.rowcount if cursor.rowcount != -1 else 0
                cursor = conn.execute(
                    "DELETE FROM signal_price_snapshots WHERE ts < ?",
                    (snapshot_cutoff,),
                )
                deleted += cursor.rowcount if cursor.rowcount != -1 else 0
            if watchlist_retention_days > 0:
                watchlist_cutoff = now - watchlist_retention_days * 24 * 60 * 60
                cursor = conn.execute(
                    "DELETE FROM watchlist_candidates WHERE ts < ?",
                    (watchlist_cutoff,),
                )
                deleted += cursor.rowcount if cursor.rowcount != -1 else 0
                cursor = conn.execute(
                    "DELETE FROM scanner_evaluations WHERE ts < ?",
                    (watchlist_cutoff,),
                )
                deleted += cursor.rowcount if cursor.rowcount != -1 else 0
                cursor = conn.execute(
                    "DELETE FROM scanner_evaluation_history WHERE ts < ?",
                    (watchlist_cutoff,),
                )
                deleted += cursor.rowcount if cursor.rowcount != -1 else 0

        return deleted

    def get_app_meta(self, key: str, default: str = "") -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM app_meta WHERE key = ?",
                (key,),
            ).fetchone()
        return str(row[0]) if row is not None else default

    def set_app_meta(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO app_meta (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def claim_app_event(self, key: str, *, ts: int, cooldown_seconds: int) -> bool:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT value FROM app_meta WHERE key = ?",
                (key,),
            ).fetchone()
            try:
                previous_ts = int(row[0]) if row is not None else 0
            except (TypeError, ValueError):
                previous_ts = 0
            if ts - previous_ts < max(0, cooldown_seconds):
                return False
            conn.execute(
                """
                INSERT INTO app_meta (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, str(ts)),
            )
            return True

    def record_snapshot(
        self,
        *,
        scanner: str,
        symbol: str,
        ts: int,
        price: float,
        open_interest: float,
        futures_cvd: float,
        spot_cvd: float,
        funding: float,
        turnover_24h: float,
        new_futures_trades: int,
        new_spot_trades: int,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO market_snapshots (
                    scanner, symbol, ts, price, open_interest, futures_cvd,
                    spot_cvd, funding, turnover_24h, new_futures_trades,
                    new_spot_trades
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scanner,
                    symbol,
                    ts,
                    price,
                    open_interest,
                    futures_cvd,
                    spot_cvd,
                    funding,
                    turnover_24h,
                    new_futures_trades,
                    new_spot_trades,
                ),
            )

    def record_signal(
        self,
        *,
        signal_type: str,
        symbol: str,
        price: float,
        open_interest_change_pct: float,
        futures_cvd_change_pct: float,
        futures_cvd_delta_usdt: float,
        spot_cvd_change_pct: float,
        spot_cvd_delta_usdt: float,
        price_change_pct: float,
        payload: str,
        model_version: str = "",
        settings_snapshot: str = "{}",
        market_observed_ts: int = 0,
        decision_ts: int = 0,
        telegram_sent_ts: int = 0,
        market_price: float = 0.0,
        entry_quote_ts: int = 0,
        entry_bid: float = 0.0,
        entry_ask: float = 0.0,
        entry_price: float = 0.0,
        entry_spread_bps: float = 0.0,
        entry_quote_status: str = "legacy",
        execution_venue: str = "",
        detection_source: str = "",
        mode: str = "",
        score: int = 0,
        turnover_24h: float = 0.0,
        confirmation_age_seconds: int = 0,
        cvd_complete: bool = False,
        confirmation_cvd_complete: bool = False,
        cvd_coverage_seconds: int = 0,
        confirmation_cvd_coverage_seconds: int = 0,
        build_commit: str = "",
        config_hash: str = "",
        schema_version: str = HISTORY_SCHEMA_VERSION,
        ts: int | None = None,
    ) -> int:
        ts = ts or int(time.time())
        market_price = market_price if market_price > 0 else price
        entry_price = entry_price if entry_price > 0 else price
        entry_bid = entry_bid if entry_bid > 0 else entry_price
        entry_ask = entry_ask if entry_ask > 0 else entry_price
        entry_quote_ts = entry_quote_ts or ts
        telegram_sent_ts = telegram_sent_ts or ts
        market_observed_ts = market_observed_ts or ts
        decision_ts = decision_ts or ts
        columns = (
            "signal_type",
            "symbol",
            "ts",
            "price",
            "open_interest_change_pct",
            "futures_cvd_change_pct",
            "futures_cvd_delta_usdt",
            "spot_cvd_change_pct",
            "spot_cvd_delta_usdt",
            "price_change_pct",
            "payload",
            "model_version",
            "settings_snapshot",
            "market_observed_ts",
            "decision_ts",
            "telegram_sent_ts",
            "market_price",
            "entry_quote_ts",
            "entry_bid",
            "entry_ask",
            "entry_price",
            "entry_spread_bps",
            "entry_quote_status",
            "execution_venue",
            "detection_source",
            "mode",
            "score",
            "turnover_24h",
            "confirmation_age_seconds",
            "cvd_complete",
            "confirmation_cvd_complete",
            "cvd_coverage_seconds",
            "confirmation_cvd_coverage_seconds",
            "build_commit",
            "config_hash",
            "schema_version",
        )
        values = (
            signal_type,
            symbol,
            ts,
            price,
            open_interest_change_pct,
            futures_cvd_change_pct,
            futures_cvd_delta_usdt,
            spot_cvd_change_pct,
            spot_cvd_delta_usdt,
            price_change_pct,
            payload,
            model_version,
            settings_snapshot,
            market_observed_ts,
            decision_ts,
            telegram_sent_ts,
            market_price,
            entry_quote_ts,
            entry_bid,
            entry_ask,
            entry_price,
            entry_spread_bps,
            entry_quote_status,
            execution_venue,
            detection_source,
            mode,
            score,
            turnover_24h,
            confirmation_age_seconds,
            int(cvd_complete),
            int(confirmation_cvd_complete),
            cvd_coverage_seconds,
            confirmation_cvd_coverage_seconds,
            build_commit,
            config_hash,
            schema_version,
        )
        with self._connect() as conn:
            cursor = conn.execute(
                f"INSERT INTO signals ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})",
                values,
            )
            signal_id = int(cursor.lastrowid)
            snapshot_price = entry_ask if entry_quote_status == "ok" else entry_price
            conn.execute(
                """
                INSERT OR IGNORE INTO signal_price_snapshots (
                    signal_id, ts, price, venue, bid, ask, quote_status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    entry_quote_ts,
                    snapshot_price,
                    execution_venue,
                    entry_bid,
                    entry_ask,
                    entry_quote_status,
                ),
            )
            return signal_id

    def record_pending_signal_prices(
        self,
        *,
        signal_type: str,
        prices: dict[str, float],
        ts: int,
        max_horizon_minutes: int = 240,
    ) -> int:
        if not prices:
            return 0
        cutoff = ts - max_horizon_minutes * 60
        inserted = 0
        with self._connect() as conn:
            signals = conn.execute(
                """
                SELECT id, symbol
                FROM signals
                WHERE signal_type = ?
                  AND ts >= ?
                  AND entry_quote_status != 'ok'
                """,
                (signal_type, cutoff),
            ).fetchall()
            for signal_id, stored_symbol in signals:
                bare_symbol = str(stored_symbol).split(":", 1)[-1]
                price = prices.get(bare_symbol)
                if price is None or price <= 0:
                    continue
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO signal_price_snapshots (signal_id, ts, price)
                    VALUES (?, ?, ?)
                    """,
                    (signal_id, ts, price),
                )
                if cursor.rowcount == 1:
                    inserted += 1
        return inserted

    def record_pending_signal_quotes(
        self,
        *,
        quotes: dict[str, tuple[float, float]],
        ts: int,
        venue: str = "BYBIT",
        signal_types: tuple[str, ...] = ("dump_binance", "dump_bybit"),
        model_version: str | None = None,
        max_horizon_minutes: int = 240,
    ) -> int:
        if not quotes or not signal_types:
            return 0
        cutoff = ts - max_horizon_minutes * 60
        placeholders = ", ".join("?" for _ in signal_types)
        query = f"""
            SELECT id, symbol
            FROM signals
            WHERE signal_type IN ({placeholders})
              AND ts >= ?
              AND execution_venue = ?
              AND entry_quote_status = 'ok'
        """
        params: list[object] = [*signal_types, cutoff, venue]
        if model_version is not None:
            query += " AND model_version = ?"
            params.append(model_version)

        inserted = 0
        with self._connect() as conn:
            signals = conn.execute(query, params).fetchall()
            for signal_id, stored_symbol in signals:
                bare_symbol = str(stored_symbol).split(":", 1)[-1]
                quote = quotes.get(bare_symbol)
                if quote is None:
                    continue
                bid, ask = quote
                if bid <= 0 or ask <= 0 or ask < bid:
                    continue
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO signal_price_snapshots (
                        signal_id, ts, price, venue, bid, ask, quote_status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'ok')
                    """,
                    (signal_id, ts, ask, venue, bid, ask),
                )
                if cursor.rowcount == 1:
                    inserted += 1
        return inserted

    def record_entry_quote_scenario(
        self,
        *,
        signal_id: int,
        delay_seconds: int,
        target_ts: int,
        quote_ts: int = 0,
        bid: float = 0.0,
        ask: float = 0.0,
        spread_bps: float = 0.0,
        status: str,
        error: str = "",
    ) -> bool:
        lag_seconds = quote_ts - target_ts if quote_ts > 0 else 0
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO signal_entry_scenarios (
                    signal_id, delay_seconds, target_ts, quote_ts,
                    bid, ask, spread_bps, lag_seconds, status, error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    delay_seconds,
                    target_ts,
                    quote_ts,
                    bid,
                    ask,
                    spread_bps,
                    lag_seconds,
                    status,
                    error[:300],
                ),
            )
            if cursor.rowcount != 1:
                return False
            if status == "ok" and quote_ts > 0 and bid > 0 and ask >= bid:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO signal_price_snapshots (
                        signal_id, ts, price, venue, bid, ask, quote_status
                    )
                    VALUES (?, ?, ?, 'BYBIT', ?, ?, 'ok')
                    """,
                    (signal_id, quote_ts, ask, bid, ask),
                )
            return True

    def claim_dump_symbol_alert(
        self,
        *,
        symbol: str,
        ts: int,
        source: str,
        score: int,
        cooldown_minutes: int,
    ) -> tuple[bool, str | None, int | None, int | None]:
        cooldown_seconds = cooldown_minutes * 60
        if cooldown_seconds <= 0:
            return True, None, None, None

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute(
                """
                SELECT ts, source, score
                FROM dump_symbol_cooldowns
                WHERE symbol = ?
                """,
                (symbol,),
            ).fetchone()
            if previous is not None:
                previous_ts, previous_source, previous_score = previous
                if ts - int(previous_ts) < cooldown_seconds:
                    return False, str(previous_source), int(previous_ts), int(previous_score)

            conn.execute(
                """
                INSERT INTO dump_symbol_cooldowns (symbol, ts, source, score)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    ts = excluded.ts,
                    source = excluded.source,
                    score = excluded.score
                """,
                (symbol, ts, source, score),
            )
            return True, None, None, None

    def release_dump_symbol_alert(self, *, symbol: str, source: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM dump_symbol_cooldowns WHERE symbol = ? AND source = ?",
                (symbol, source),
            )

    def claim_telegram_symbol_alert(
        self,
        *,
        symbol: str,
        ts: int,
        signal_type: str,
        cooldown_minutes: int,
    ) -> tuple[bool, str | None, int | None]:
        cooldown_seconds = cooldown_minutes * 60
        if cooldown_seconds <= 0:
            return True, None, None

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute(
                """
                SELECT ts, signal_type
                FROM telegram_symbol_cooldowns
                WHERE symbol = ?
                """,
                (symbol,),
            ).fetchone()
            if previous is not None:
                previous_ts, previous_signal_type = previous
                if ts - int(previous_ts) < cooldown_seconds:
                    return False, str(previous_signal_type), int(previous_ts)

            conn.execute(
                """
                INSERT INTO telegram_symbol_cooldowns (symbol, ts, signal_type)
                VALUES (?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    ts = excluded.ts,
                    signal_type = excluded.signal_type
                """,
                (symbol, ts, signal_type),
            )
            return True, None, None

    def release_telegram_symbol_alert(self, *, symbol: str, ts: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM telegram_symbol_cooldowns WHERE symbol = ? AND ts = ?",
                (symbol, ts),
            )

    def update_signal_reviews(
        self,
        *,
        now: int | None = None,
        horizons_minutes: tuple[int, ...] = (15, 30, 60, 240),
        max_lag_seconds: int = 300,
    ) -> int:
        now = now or int(time.time())
        max_lag_seconds = max(0, max_lag_seconds)
        reviewed = 0
        with self._connect() as conn:
            signals = conn.execute(
                """
                SELECT
                    id, signal_type, symbol, ts, price, model_version,
                    entry_price, entry_quote_ts, entry_quote_status,
                    execution_venue, telegram_sent_ts
                FROM signals
                WHERE ts <= ?
                ORDER BY ts ASC
                """,
                (now - min(horizons_minutes) * 60,),
            ).fetchall()

            for (
                signal_id,
                signal_type,
                symbol,
                signal_ts,
                legacy_price,
                _model_version,
                stored_entry_price,
                entry_quote_ts,
                entry_quote_status,
                execution_venue,
                telegram_sent_ts,
            ) in signals:
                scanner = _scanner_for_signal_type(signal_type)
                if scanner is None:
                    continue
                entry_price = stored_entry_price if stored_entry_price > 0 else legacy_price
                review_start_ts = telegram_sent_ts or signal_ts
                series_start_ts = entry_quote_ts or review_start_ts
                exact_review = (
                    entry_quote_status == "ok"
                    and execution_venue == "BYBIT"
                    and entry_price > 0
                )

                for horizon_minutes in horizons_minutes:
                    target_ts = review_start_ts + horizon_minutes * 60
                    if target_ts > now:
                        continue
                    exists = conn.execute(
                        """
                        SELECT 1
                        FROM signal_reviews
                        WHERE signal_id = ? AND horizon_minutes = ?
                        """,
                        (signal_id, horizon_minutes),
                    ).fetchone()
                    if exists:
                        continue

                    if exact_review:
                        target_snapshot = conn.execute(
                            """
                            SELECT ts, price
                            FROM signal_price_snapshots
                            WHERE signal_id = ?
                              AND venue = ?
                              AND quote_status = 'ok'
                              AND ts >= ?
                              AND ts <= ?
                            ORDER BY ts ASC
                            LIMIT 1
                            """,
                            (
                                signal_id,
                                execution_venue,
                                target_ts,
                                target_ts + max_lag_seconds,
                            ),
                        ).fetchone()
                        if target_snapshot is None:
                            if now < target_ts + max_lag_seconds:
                                continue
                            conn.execute(
                                """
                                INSERT OR IGNORE INTO signal_reviews (
                                    signal_id, horizon_minutes, reviewed_ts,
                                    price_at_review, move_pct, max_favorable_pct,
                                    max_adverse_pct, target_ts, price_ts,
                                    lag_seconds, status, missing_reason,
                                    execution_venue
                                )
                                VALUES (?, ?, ?, 0, 0, 0, 0, ?, 0, 0, 'missing', ?, ?)
                                """,
                                (
                                    signal_id,
                                    horizon_minutes,
                                    now,
                                    target_ts,
                                    "no_bybit_quote_within_lag",
                                    execution_venue,
                                ),
                            )
                            reviewed += 1
                            continue

                        price_ts, price_at_review = target_snapshot
                        snapshots = conn.execute(
                            """
                            SELECT ts, price
                            FROM signal_price_snapshots
                            WHERE signal_id = ?
                              AND venue = ?
                              AND quote_status = 'ok'
                              AND ts >= ?
                              AND ts <= ?
                            ORDER BY ts ASC
                            """,
                            (
                                signal_id,
                                execution_venue,
                                series_start_ts,
                                price_ts,
                            ),
                        ).fetchall()
                        prices = [row[1] for row in snapshots] or [price_at_review]
                        move_pct, max_favorable_pct, max_adverse_pct = _review_metrics(
                            signal_type=signal_type,
                            entry_price=entry_price,
                            price_at_review=price_at_review,
                            prices=prices,
                        )
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO signal_reviews (
                                signal_id, horizon_minutes, reviewed_ts,
                                price_at_review, move_pct, max_favorable_pct,
                                max_adverse_pct, target_ts, price_ts,
                                lag_seconds, status, missing_reason,
                                execution_venue
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ok', '', ?)
                            """,
                            (
                                signal_id,
                                horizon_minutes,
                                now,
                                price_at_review,
                                move_pct,
                                max_favorable_pct,
                                max_adverse_pct,
                                target_ts,
                                price_ts,
                                price_ts - target_ts,
                                execution_venue,
                            ),
                        )
                        reviewed += 1
                        continue

                    snapshots = conn.execute(
                        """
                        SELECT ts, price
                        FROM signal_price_snapshots
                        WHERE signal_id = ? AND ts >= ? AND ts <= ?
                        ORDER BY ts ASC
                        """,
                        (signal_id, signal_ts, target_ts),
                    ).fetchall()
                    if not snapshots:
                        snapshots = conn.execute(
                            """
                            SELECT ts, price
                            FROM market_snapshots
                            WHERE scanner = ? AND symbol = ? AND ts >= ? AND ts <= ?
                            ORDER BY ts ASC
                            """,
                            (scanner, symbol, signal_ts, target_ts),
                        ).fetchall()
                    if not snapshots:
                        continue

                    price_ts, price_at_review = snapshots[-1]
                    prices = [row[1] for row in snapshots]
                    move_pct, max_favorable_pct, max_adverse_pct = _review_metrics(
                        signal_type=signal_type,
                        entry_price=entry_price,
                        price_at_review=price_at_review,
                        prices=prices,
                    )
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO signal_reviews (
                            signal_id, horizon_minutes, reviewed_ts, price_at_review,
                            move_pct, max_favorable_pct, max_adverse_pct,
                            target_ts, price_ts, lag_seconds, status,
                            missing_reason, execution_venue
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'legacy', '', ?)
                        """,
                        (
                            signal_id,
                            horizon_minutes,
                            now,
                            price_at_review,
                            move_pct,
                            max_favorable_pct,
                            max_adverse_pct,
                            target_ts,
                            price_ts,
                            target_ts - price_ts,
                            execution_venue,
                        ),
                    )
                    reviewed += 1

        return reviewed

    def get_signal_stats(self, model_version: str | None = None) -> list[tuple]:
        where = "WHERE r.status IN ('ok', 'legacy')"
        params: tuple[object, ...] = ()
        if model_version is not None:
            where += " AND s.model_version = ?"
            params = (model_version,)
        with self._connect() as conn:
            return conn.execute(
                f"""
                SELECT
                    s.signal_type,
                    r.horizon_minutes,
                    COUNT(*) AS total,
                    AVG(r.move_pct) AS avg_move_pct,
                    AVG(r.max_favorable_pct) AS avg_max_favorable_pct,
                    AVG(r.max_adverse_pct) AS avg_max_adverse_pct,
                    SUM(CASE WHEN r.move_pct > 0 THEN 1 ELSE 0 END) AS positive_count
                FROM signal_reviews r
                JOIN signals s ON s.id = r.signal_id
                {where}
                GROUP BY s.signal_type, r.horizon_minutes
                ORDER BY s.signal_type, r.horizon_minutes
                """,
                params,
            ).fetchall()

    def get_review_quality(self, model_version: str | None = None) -> list[tuple]:
        where = ""
        params: tuple[object, ...] = ()
        if model_version is not None:
            where = "WHERE s.model_version = ?"
            params = (model_version,)
        with self._connect() as conn:
            return conn.execute(
                f"""
                SELECT
                    r.status,
                    COUNT(*) AS total,
                    AVG(CASE WHEN r.status = 'ok' THEN r.lag_seconds END) AS avg_lag_seconds
                FROM signal_reviews r
                JOIN signals s ON s.id = r.signal_id
                {where}
                GROUP BY r.status
                ORDER BY r.status
                """,
                params,
            ).fetchall()

    def get_entry_scenario_stats(self, model_version: str | None = None) -> list[tuple]:
        where = "WHERE q.status = 'ok' AND s.entry_price > 0"
        params: tuple[object, ...] = ()
        if model_version is not None:
            where += " AND s.model_version = ?"
            params = (model_version,)
        with self._connect() as conn:
            return conn.execute(
                f"""
                SELECT
                    q.delay_seconds,
                    COUNT(*) AS total,
                    AVG(((s.entry_price - q.bid) / s.entry_price) * 100) AS avg_bid_drift_pct,
                    AVG(q.spread_bps) AS avg_spread_bps
                FROM signal_entry_scenarios q
                JOIN signals s ON s.id = q.signal_id
                {where}
                GROUP BY q.delay_seconds
                ORDER BY q.delay_seconds
                """,
                params,
            ).fetchall()

    def get_recent_signals(
        self,
        limit: int = 5,
        model_version: str | None = None,
    ) -> list[tuple]:
        where = ""
        params: list[object] = []
        if model_version is not None:
            where = "WHERE model_version = ?"
            params.append(model_version)
        params.append(limit)
        with self._connect() as conn:
            return conn.execute(
                f"""
                SELECT
                    id, signal_type, symbol, ts,
                    CASE WHEN entry_price > 0 THEN entry_price ELSE price END,
                    price_change_pct
                FROM signals
                {where}
                ORDER BY ts DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

    def get_market_snapshots(
        self,
        *,
        scanner: str,
        symbol: str,
        since_ts: int,
    ) -> list[tuple]:
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT ts, price, open_interest, futures_cvd, funding, turnover_24h
                FROM market_snapshots
                WHERE scanner = ? AND symbol = ? AND ts >= ?
                ORDER BY ts ASC
                """,
                (scanner, symbol, since_ts),
            ).fetchall()

    def record_watchlist_candidate(
        self,
        *,
        scanner: str,
        symbol: str,
        score: int,
        price: float,
        passed_checks: list[str],
        missing_checks: list[str],
        payload: str,
        ts: int | None = None,
        cooldown_seconds: int = 0,
    ) -> bool:
        ts = ts or int(time.time())
        with self._connect() as conn:
            if cooldown_seconds > 0:
                bucket_start = ts - (ts % cooldown_seconds)
                existing = conn.execute(
                    """
                    SELECT id, score
                    FROM watchlist_candidates
                    WHERE scanner = ? AND symbol = ? AND ts >= ? AND ts < ?
                    ORDER BY ts DESC
                    LIMIT 1
                    """,
                    (scanner, symbol, bucket_start, bucket_start + cooldown_seconds),
                ).fetchone()
                if existing:
                    if score <= existing[1]:
                        return False
                    conn.execute(
                        """
                        UPDATE watchlist_candidates
                        SET ts = ?, score = ?, price = ?, passed_checks = ?,
                            missing_checks = ?, payload = ?
                        WHERE id = ?
                        """,
                        (
                            ts,
                            score,
                            price,
                            ",".join(passed_checks),
                            ",".join(missing_checks),
                            payload,
                            existing[0],
                        ),
                    )
                    return True

            conn.execute(
                """
                INSERT INTO watchlist_candidates (
                    scanner, symbol, ts, score, price, passed_checks,
                    missing_checks, payload
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scanner,
                    symbol,
                    ts,
                    score,
                    price,
                    ",".join(passed_checks),
                    ",".join(missing_checks),
                    payload,
                ),
            )
        return True

    def record_scanner_evaluation(
        self,
        *,
        scanner: str,
        source: str,
        symbol: str,
        ts: int,
        source_rank: int,
        selected: bool,
        status: str,
        reason: str,
        score: int,
        price: float,
        turnover_24h: float,
        price_growth_lookback_pct: float = 0.0,
        drawdown_from_high_pct: float = 0.0,
        oi_change_pct: float = 0.0,
        cvd_delta_usdt: float = 0.0,
        price_change_window_pct: float = 0.0,
        funding_rate: float = 0.0,
        passed_checks: list[str] | None = None,
        missing_checks: list[str] | None = None,
        payload: str = "",
        model_version: str = "",
        snapshot_interval_seconds: int = 3600,
    ) -> None:
        passed = ",".join(passed_checks or [])
        missing = ",".join(missing_checks or [])
        snapshot_interval_seconds = max(60, snapshot_interval_seconds)
        bucket_ts = ts - (ts % snapshot_interval_seconds)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO scanner_evaluations (
                    scanner, source, symbol, ts, source_rank, selected, status,
                    reason, score, price, turnover_24h,
                    price_growth_lookback_pct, drawdown_from_high_pct,
                    oi_change_pct, cvd_delta_usdt, price_change_window_pct,
                    funding_rate, passed_checks, missing_checks, payload,
                    model_version
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scanner, symbol) DO UPDATE SET
                    source = excluded.source,
                    ts = excluded.ts,
                    source_rank = excluded.source_rank,
                    selected = excluded.selected,
                    status = excluded.status,
                    reason = excluded.reason,
                    score = excluded.score,
                    price = excluded.price,
                    turnover_24h = excluded.turnover_24h,
                    price_growth_lookback_pct = excluded.price_growth_lookback_pct,
                    drawdown_from_high_pct = excluded.drawdown_from_high_pct,
                    oi_change_pct = excluded.oi_change_pct,
                    cvd_delta_usdt = excluded.cvd_delta_usdt,
                    price_change_window_pct = excluded.price_change_window_pct,
                    funding_rate = excluded.funding_rate,
                    passed_checks = excluded.passed_checks,
                    missing_checks = excluded.missing_checks,
                    payload = excluded.payload,
                    model_version = excluded.model_version
                WHERE scanner_evaluations.ts <= excluded.ts - ?
                   OR scanner_evaluations.source != excluded.source
                   OR scanner_evaluations.source_rank != excluded.source_rank
                   OR scanner_evaluations.selected != excluded.selected
                   OR scanner_evaluations.status != excluded.status
                   OR scanner_evaluations.reason != excluded.reason
                   OR scanner_evaluations.score != excluded.score
                   OR scanner_evaluations.passed_checks != excluded.passed_checks
                   OR scanner_evaluations.missing_checks != excluded.missing_checks
                   OR scanner_evaluations.model_version != excluded.model_version
                """,
                (
                    scanner,
                    source,
                    symbol,
                    ts,
                    source_rank,
                    1 if selected else 0,
                    status,
                    reason,
                    score,
                    price,
                    turnover_24h,
                    price_growth_lookback_pct,
                    drawdown_from_high_pct,
                    oi_change_pct,
                    cvd_delta_usdt,
                    price_change_window_pct,
                    funding_rate,
                    passed,
                    missing,
                    payload,
                    model_version,
                    snapshot_interval_seconds,
                ),
            )
            conn.execute(
                """
                INSERT INTO scanner_evaluation_history (
                    scanner, source, symbol, ts, bucket_ts, source_rank,
                    selected, status, reason, score, price, turnover_24h,
                    price_growth_lookback_pct, drawdown_from_high_pct,
                    oi_change_pct, cvd_delta_usdt, price_change_window_pct,
                    funding_rate, passed_checks, missing_checks, model_version
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scanner, symbol, bucket_ts) DO UPDATE SET
                    source = excluded.source,
                    ts = excluded.ts,
                    source_rank = excluded.source_rank,
                    selected = excluded.selected,
                    status = excluded.status,
                    reason = excluded.reason,
                    score = excluded.score,
                    price = excluded.price,
                    turnover_24h = excluded.turnover_24h,
                    price_growth_lookback_pct = excluded.price_growth_lookback_pct,
                    drawdown_from_high_pct = excluded.drawdown_from_high_pct,
                    oi_change_pct = excluded.oi_change_pct,
                    cvd_delta_usdt = excluded.cvd_delta_usdt,
                    price_change_window_pct = excluded.price_change_window_pct,
                    funding_rate = excluded.funding_rate,
                    passed_checks = excluded.passed_checks,
                    missing_checks = excluded.missing_checks,
                    model_version = excluded.model_version
                WHERE excluded.score > scanner_evaluation_history.score
                   OR excluded.selected > scanner_evaluation_history.selected
                   OR (
                       excluded.status = 'signal'
                       AND scanner_evaluation_history.status != 'signal'
                   )
                   OR excluded.model_version != scanner_evaluation_history.model_version
                """,
                (
                    scanner,
                    source,
                    symbol,
                    ts,
                    bucket_ts,
                    source_rank,
                    1 if selected else 0,
                    status,
                    reason,
                    score,
                    price,
                    turnover_24h,
                    price_growth_lookback_pct,
                    drawdown_from_high_pct,
                    oi_change_pct,
                    cvd_delta_usdt,
                    price_change_window_pct,
                    funding_rate,
                    passed,
                    missing,
                    model_version,
                ),
            )

    def get_recent_scanner_evaluations(
        self,
        *,
        scanner: str | None = None,
        status: str | None = None,
        limit: int = 10,
    ) -> list[tuple]:
        conditions = []
        params: list[object] = []
        if scanner is not None:
            conditions.append("scanner = ?")
            params.append(scanner)
        if status is not None:
            conditions.append("status = ?")
            params.append(status)
        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)
        with self._connect() as conn:
            return conn.execute(
                f"""
                SELECT scanner, source, symbol, ts, source_rank, status, reason,
                       score, price, turnover_24h, missing_checks
                FROM scanner_evaluations
                {where_sql}
                ORDER BY ts DESC, source_rank ASC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()

    def get_trend_change(
        self,
        *,
        scanner: str,
        symbol: str,
        now: int,
        hours: int,
        min_coverage_ratio: float = 0.6,
    ) -> tuple[float, float] | None:
        """OI and price change over the last N hours from stored snapshots.

        Returns (oi_change_pct, price_change_pct) or None when the stored
        history covers less than min_coverage_ratio of the requested window.
        """
        since = now - hours * 3600
        with self._connect() as conn:
            first = conn.execute(
                """
                SELECT ts, open_interest, price
                FROM market_snapshots
                WHERE scanner = ? AND symbol = ? AND ts >= ?
                ORDER BY ts ASC
                LIMIT 1
                """,
                (scanner, symbol, since),
            ).fetchone()
            last = conn.execute(
                """
                SELECT ts, open_interest, price
                FROM market_snapshots
                WHERE scanner = ? AND symbol = ? AND ts >= ?
                ORDER BY ts DESC
                LIMIT 1
                """,
                (scanner, symbol, since),
            ).fetchone()

        if not first or not last:
            return None
        first_ts, first_oi, first_price = first
        last_ts, last_oi, last_price = last
        if last_ts - first_ts < hours * 3600 * min_coverage_ratio:
            return None
        return _pct_change(first_oi, last_oi), _pct_change(first_price, last_price)

    def get_recent_watchlist_candidates(self, limit: int = 10) -> list[tuple]:
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT scanner, symbol, ts, score, price, passed_checks, missing_checks
                FROM watchlist_candidates
                ORDER BY ts DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()


def _pct_change(old: float, new: float) -> float:
    if old == 0:
        return 100.0 if new > 0 else 0.0
    return ((new - old) / abs(old)) * 100


def _review_metrics(
    *,
    signal_type: str,
    entry_price: float,
    price_at_review: float,
    prices: list[float],
) -> tuple[float, float, float]:
    if entry_price == 0:
        return 0.0, 0.0, 0.0

    high = max(entry_price, max(prices))
    low = min(entry_price, min(prices))
    if signal_type in {"pump", "pump_exhaustion", "short_breakdown", "short_long_trap"} or signal_type.startswith("dump_"):
        move_pct = ((entry_price - price_at_review) / entry_price) * 100
        max_favorable_pct = ((entry_price - low) / entry_price) * 100
        max_adverse_pct = ((high - entry_price) / entry_price) * 100
        return move_pct, max_favorable_pct, max_adverse_pct

    move_pct = ((price_at_review - entry_price) / entry_price) * 100
    max_favorable_pct = ((high - entry_price) / entry_price) * 100
    max_adverse_pct = ((entry_price - low) / entry_price) * 100
    return move_pct, max_favorable_pct, max_adverse_pct


def _scanner_for_signal_type(signal_type: str) -> str | None:
    if signal_type in {"long", "long_accumulation", "long_breakout", "long_squeeze"}:
        return "long"
    if signal_type in {"pump", "pump_exhaustion", "short_breakdown", "short_long_trap"}:
        return "pump"
    if signal_type.startswith("dump_"):
        return signal_type
    return None
