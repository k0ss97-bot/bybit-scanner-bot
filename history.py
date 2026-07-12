from __future__ import annotations

from pathlib import Path
import sqlite3
import time


REVIEW_SCHEMA_VERSION = "scanner-filtered-v3-15-30-60-240"


class HistoryStore:
    def __init__(self, path: str = "data/scanner.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._last_cleanup_ts = 0
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)

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
                    payload TEXT NOT NULL
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
            self._migrate_signal_reviews(conn)

    def _migrate_signal_reviews(self, conn: sqlite3.Connection) -> None:
        row = conn.execute(
            "SELECT value FROM app_meta WHERE key = ?",
            ("signal_reviews_version",),
        ).fetchone()
        current_version = row[0] if row else ""
        if current_version == REVIEW_SCHEMA_VERSION:
            return

        conn.execute("DELETE FROM signal_reviews")
        conn.execute(
            """
            INSERT OR REPLACE INTO app_meta (key, value)
            VALUES (?, ?)
            """,
            ("signal_reviews_version", REVIEW_SCHEMA_VERSION),
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

        return deleted

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
        ts: int | None = None,
    ) -> int:
        ts = ts or int(time.time())
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO signals (
                    signal_type, symbol, ts, price, open_interest_change_pct,
                    futures_cvd_change_pct, futures_cvd_delta_usdt,
                    spot_cvd_change_pct, spot_cvd_delta_usdt,
                    price_change_pct, payload
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
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
                ),
            )
            signal_id = int(cursor.lastrowid)
            conn.execute(
                """
                INSERT OR IGNORE INTO signal_price_snapshots (signal_id, ts, price)
                VALUES (?, ?, ?)
                """,
                (signal_id, ts, price),
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
                WHERE signal_type = ? AND ts >= ?
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
    ) -> int:
        now = now or int(time.time())
        reviewed = 0
        with self._connect() as conn:
            signals = conn.execute(
                """
                SELECT id, signal_type, symbol, ts, price
                FROM signals
                WHERE ts <= ?
                ORDER BY ts ASC
                """,
                (now - min(horizons_minutes) * 60,),
            ).fetchall()

            for signal_id, signal_type, symbol, signal_ts, entry_price in signals:
                scanner = _scanner_for_signal_type(signal_type)
                if scanner is None:
                    continue

                for horizon_minutes in horizons_minutes:
                    target_ts = signal_ts + horizon_minutes * 60
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

                    price_at_review = snapshots[-1][1]
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
                            move_pct, max_favorable_pct, max_adverse_pct
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            signal_id,
                            horizon_minutes,
                            now,
                            price_at_review,
                            move_pct,
                            max_favorable_pct,
                            max_adverse_pct,
                        ),
                    )
                    reviewed += 1

        return reviewed

    def get_signal_stats(self) -> list[tuple]:
        with self._connect() as conn:
            return conn.execute(
                """
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
                GROUP BY s.signal_type, r.horizon_minutes
                ORDER BY s.signal_type, r.horizon_minutes
                """
            ).fetchall()

    def get_recent_signals(self, limit: int = 5) -> list[tuple]:
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT id, signal_type, symbol, ts, price, price_change_pct
                FROM signals
                ORDER BY ts DESC
                LIMIT ?
                """,
                (limit,),
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
                existing = conn.execute(
                    """
                    SELECT id
                    FROM watchlist_candidates
                    WHERE scanner = ? AND symbol = ? AND ts >= ?
                    ORDER BY ts DESC
                    LIMIT 1
                    """,
                    (scanner, symbol, ts - cooldown_seconds),
                ).fetchone()
                if existing:
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
    ) -> None:
        passed = ",".join(passed_checks or [])
        missing = ",".join(missing_checks or [])
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO scanner_evaluations (
                    scanner, source, symbol, ts, source_rank, selected, status,
                    reason, score, price, turnover_24h,
                    price_growth_lookback_pct, drawdown_from_high_pct,
                    oi_change_pct, cvd_delta_usdt, price_change_window_pct,
                    funding_rate, passed_checks, missing_checks, payload
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    payload = excluded.payload
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

    high = max(prices)
    low = min(prices)
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
