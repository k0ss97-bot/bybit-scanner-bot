from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from json import JSONDecodeError
from pathlib import Path


@dataclass
class Snapshot:
    ts: int
    oi: float
    cvd: float
    price: float
    funding: float
    turnover_24h: float
    spot_cvd: float = 0.0
    new_trades: int = 0
    new_spot_trades: int = 0
    cvd_generation: int = 0


@dataclass
class SymbolState:
    cumulative_cvd: float = 0.0
    cumulative_spot_cvd: float = 0.0
    seen_trade_ids: list[str] = field(default_factory=list)
    seen_spot_trade_ids: list[str] = field(default_factory=list)
    snapshots: list[Snapshot] = field(default_factory=list)
    last_alert_ts: int = 0
    last_alert_score: int = 0
    last_watchlist_ts: int = 0
    consecutive_matches: int = 0
    last_trade_id: str = ""
    last_trade_time_ms: int = 0
    cvd_generation: int = 0


class StateStore:
    def __init__(self, path: str = "state.json") -> None:
        self.path = Path(path)
        self.symbols: dict[str, SymbolState] = {}

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            text = self.path.read_text()
            if not text.strip():
                self._quarantine_bad_state("empty")
                return
            raw = json.loads(text)
        except (OSError, JSONDecodeError) as error:
            self._quarantine_bad_state(type(error).__name__)
            return

        for symbol, data in raw.get("symbols", {}).items():
            self.symbols[symbol] = SymbolState(
                cumulative_cvd=float(data.get("cumulative_cvd", 0)),
                cumulative_spot_cvd=float(data.get("cumulative_spot_cvd", 0)),
                seen_trade_ids=list(data.get("seen_trade_ids", [])),
                seen_spot_trade_ids=list(data.get("seen_spot_trade_ids", [])),
                snapshots=[Snapshot(**snap) for snap in data.get("snapshots", [])],
                last_alert_ts=int(data.get("last_alert_ts", 0)),
                last_alert_score=int(data.get("last_alert_score", 0)),
                last_watchlist_ts=int(data.get("last_watchlist_ts", 0)),
                consecutive_matches=int(data.get("consecutive_matches", 0)),
                last_trade_id=str(data.get("last_trade_id", "")),
                last_trade_time_ms=int(data.get("last_trade_time_ms", 0)),
                cvd_generation=int(data.get("cvd_generation", 0)),
            )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        raw = {"symbols": {symbol: asdict(state) for symbol, state in self.symbols.items()}}
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2))
        tmp_path.replace(self.path)

    def get_symbol(self, symbol: str) -> SymbolState:
        if symbol not in self.symbols:
            self.symbols[symbol] = SymbolState()
        return self.symbols[symbol]

    def _quarantine_bad_state(self, reason: str) -> None:
        backup_path = self.path.with_suffix(f"{self.path.suffix}.bad")
        try:
            self.path.replace(backup_path)
            print(f"State file reset: {self.path} was {reason}, backup={backup_path}", flush=True)
        except OSError:
            print(f"State file reset: {self.path} was {reason}", flush=True)
        self.symbols = {}
