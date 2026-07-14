from __future__ import annotations

import os
from pathlib import Path

from dump_scanner import DUMP_MODEL_VERSION
from history import HistoryStore


def data_path(filename: str) -> str:
    data_dir = Path(os.getenv("DATA_DIR", "data"))
    return str(data_dir / filename)


def main() -> None:
    db_path = data_path("scanner.db")
    if not Path(db_path).exists():
        print(f"Database not found: {db_path}")
        return

    history = HistoryStore(db_path)
    rows = history.get_signal_stats(model_version=DUMP_MODEL_VERSION)

    if not rows:
        print("No reviewed signals yet.")
        return

    print(f"Signal performance: {DUMP_MODEL_VERSION}")
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
