from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path


@dataclass
class Snapshot:
    ts: int
    oi: float
    cvd: float
    spot_cvd: float = 0.0
    price: float
    funding: float
    turnover_24h: float
    new_trades: int = 0
    new_spot_trades: int = 0


@dataclass
class SymbolState:
    cumulative_cvd: float = 0.0
    cumulative_spot_cvd: float = 0.0
    seen_trade_ids: list[str] = field(default_factory=list)
    seen_spot_trade_ids: list[str] = field(default_factory=list)
    snapshots: list[Snapshot] = field(default_factory=list)
    last_alert_ts: int = 0
    consecutive_matches: int = 0


class StateStore:
    def __init__(self, path: str = "state.json") -> None:
        self.path = Path(path)
        self.symbols: dict[str, SymbolState] = {}

    def load(self) -> None:
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text())
        for symbol, data in raw.get("symbols", {}).items():
            self.symbols[symbol] = SymbolState(
                cumulative_cvd=float(data.get("cumulative_cvd", 0)),
                cumulative_spot_cvd=float(data.get("cumulative_spot_cvd", 0)),
                seen_trade_ids=list(data.get("seen_trade_ids", [])),
                seen_spot_trade_ids=list(data.get("seen_spot_trade_ids", [])),
                snapshots=[Snapshot(**snap) for snap in data.get("snapshots", [])],
                last_alert_ts=int(data.get("last_alert_ts", 0)),
                consecutive_matches=int(data.get("consecutive_matches", 0)),
            )

    def save(self) -> None:
        raw = {"symbols": {symbol: asdict(state) for symbol, state in self.symbols.items()}}
        self.path.write_text(json.dumps(raw, ensure_ascii=False, indent=2))

    def get_symbol(self, symbol: str) -> SymbolState:
        if symbol not in self.symbols:
            self.symbols[symbol] = SymbolState()
        return self.symbols[symbol]
