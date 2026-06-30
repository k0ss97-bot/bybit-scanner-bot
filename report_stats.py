from __future__ import annotations

import os
from pathlib import Path
import sqlite3


def data_path(filename: str) -> str:
    data_dir = Path(os.getenv("DATA_DIR", "data"))
    return str(data_dir / filename)


def main() -> None:
    db_path = data_path("scanner.db")
    if not Path(db_path).exists():
        print(f"Database not found: {db_path}")
        return

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
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

    if not rows:
        print("No reviewed signals yet.")
        return

    print("Signal performance")
    for (
        signal_type,
        horizon_minutes,
        total,
        avg_move_pct,
        avg_max_favorable_pct,
        avg_max_adverse_pct,
        positive_count,
    ) in rows:
        win_rate = (positive_count / total) * 100 if total else 0
        print(
            f"{signal_type} {horizon_minutes}m: "
            f"signals={total}, "
            f"win_rate={win_rate:.1f}%, "
            f"avg_move={avg_move_pct:+.2f}%, "
            f"avg_best={avg_max_favorable_pct:+.2f}%, "
            f"avg_adverse={avg_max_adverse_pct:+.2f}%"
        )


if __name__ == "__main__":
    main()
