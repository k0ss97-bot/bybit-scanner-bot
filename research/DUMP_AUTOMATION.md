# DUMP automation: baseline and safety gate

This document records the first reproducible automation check. It is not a claim of profitability and does not authorize live orders.

## Historical sample checked on 2026-07-14

Source database: the downloaded `scanner-analysis-20260713.db` copy.

- 1,197 reviewed DUMP messages had a 240-minute result.
- Repeated messages were collapsed into 113 independent same-symbol events.
- Those events formed 64 market episodes after correlation clustering.
- Round-trip cost in the final default run is 0.22%: 5.5 bps entry fee, 5.5 bps exit fee, 5 bps slippage in each direction and a conservative 1 bps funding buffer.
- The database contained no reviewed event from the current `dump-v5.2-confirmation` model.

The old generations produced these diagnostic results:

| Exit | Mean net | Median net | Profit factor | Mean without five best trades |
|---|---:|---:|---:|---:|
| Four-hour time exit | +1.21% | -0.00% | 1.49 | -0.53% |
| 2% stop, otherwise four-hour exit | +1.15% | -2.22% | 1.92 | -0.04% |
| Path-only 3% target / 2% stop | -0.08% | -0.49% | 0.92 | -0.46% |
| Path-only trailing | +0.58% | -0.17% | 1.62 | -0.68% |

The untouched final 20% of the four-hour sequence had mean return `-1.14%`, median `-1.11%` and profit factor `0.59`. The cluster-bootstrap lower bounds were negative. The apparent full-sample profit was therefore concentrated in a few extreme winners and was not stable through time.

## What this backtest does and does not prove

`backtest_dump.py` is an event-level audit. It evaluates only decisions and rejected candidates that the running bot recorded at that moment. It avoids inventing historical candidates with information from the future, separates model generations, collapses signal spam and clusters correlated market moves.

It does not reconstruct every historical top-100 scan. A full replay of the current model requires synchronized Binance and Bybit candles, aggregate trades/CVD, open interest, funding, listing status and the exact symbol ranking at every decision time. Binance publishes downloadable futures market archives, while Bybit exposes historical market data and an open-interest history endpoint. That replay should be a separate walk-forward research stage, not a reinterpretation of old 15-minute alerts.

Official data references:

- [Binance public market data](https://github.com/binance/binance-public-data)
- [Bybit historical market data](https://www.bybit.com/derivatives/en/history-data)
- [Bybit open-interest history API](https://bybit-exchange.github.io/docs/v5/market/open-interest)
- [Bybit order creation behavior](https://bybit-exchange.github.io/docs/v5/order/create-order)

## Current operating stage

The bot now records two local paper variants for every sent signal:

1. SHORT with a 2% stop and four-hour time exit.
2. SHORT with a 2% stop, trailing activated after 2% favorable movement, 1.5% trailing distance and four-hour maximum holding time.

Both use the executable Bybit bid/ask, fees, adverse slippage, 0.5% equity risk per trade, a 25% notional cap, three-position cap and one idea per 30-minute market episode. They never call an authenticated exchange endpoint.

## Automation gate

Live order code remains blocked until every check passes on the same current model and configuration:

1. At least 200 independent reviewed events.
2. At least 98% executable Bybit entry-quote coverage.
3. At least 98% exact four-hour outcome coverage.
4. Positive expectancy on the untouched chronological test split after costs.
5. Positive cluster-bootstrap 95% lower bound.
6. Test profit factor at least 1.20.
7. Positive test expectancy after removing its five best trades.
8. At least 30 continuous days of paper observation with no broker-loop integrity errors.

The baseline result is **FAIL**. This is the intended safety outcome: the old sample is useful for finding weaknesses, but it does not justify unattended real-money trading.
