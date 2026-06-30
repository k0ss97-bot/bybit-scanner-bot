from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import sys
import time


def data_path(filename: str) -> str:
    data_dir = Path(os.getenv("DATA_DIR", "data"))
    return str(data_dir / filename)


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    db_path = data_path("scanner.db")
    if not Path(db_path).exists():
        print(f"Database not found: {db_path}")
        return

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, signal_type, symbol, ts, price, price_change_pct
            FROM signals
            ORDER BY ts DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    if not rows:
        print("No signals yet.")
        return

    for signal_id, signal_type, symbol, ts, price, price_change_pct in rows:
        age_minutes = int((time.time() - ts) / 60)
        print(
            f"#{signal_id} {signal_type} {symbol}: "
            f"price={price:g}, window_price={price_change_pct:+.2f}%, "
            f"age={age_minutes}m"
        )


if __name__ == "__main__":
    main()
