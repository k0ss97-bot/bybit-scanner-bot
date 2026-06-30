from __future__ import annotations

from pathlib import Path
import sqlite3
import time


class HistoryStore:
    def __init__(self, path: str = "data/scanner.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
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
    ) -> None:
        ts = ts or int(time.time())
        with self._connect() as conn:
            conn.execute(
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

    def update_signal_reviews(
        self,
        *,
        now: int | None = None,
        horizons_minutes: tuple[int, ...] = (60, 240, 1440),
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
                        FROM market_snapshots
                        WHERE symbol = ? AND ts >= ? AND ts <= ?
                        ORDER BY ts ASC
                        """,
                        (symbol, signal_ts, target_ts),
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
    if signal_type in {"pump", "pump_exhaustion"}:
        move_pct = ((entry_price - price_at_review) / entry_price) * 100
        max_favorable_pct = ((entry_price - low) / entry_price) * 100
        max_adverse_pct = ((high - entry_price) / entry_price) * 100
        return move_pct, max_favorable_pct, max_adverse_pct

    move_pct = ((price_at_review - entry_price) / entry_price) * 100
    max_favorable_pct = ((high - entry_price) / entry_price) * 100
    max_adverse_pct = ((entry_price - low) / entry_price) * 100
    return move_pct, max_favorable_pct, max_adverse_pct
