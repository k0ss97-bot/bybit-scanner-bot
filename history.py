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
    ) -> None:
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
                    int(time.time()),
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
