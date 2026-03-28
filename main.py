"""
main.py — Polymarket Latency Arbitrage Bot
Runs every 30 seconds.

Strategy:
  1. Get real-time BTC price momentum from Binance WebSocket.
  2. Find an active BTC prediction market on Polymarket.
  3. If Binance shows strong directional move but Polymarket odds are ~50/50:
       -> Ask Claude for confirmation.
       -> Place paper bet in the mispriced direction.
  4. Resolve paper bets whose duration has expired.
  5. Enforce kill switch ($10) and ceiling ($100).

Open two terminals:
  Terminal 1: python main.py
  Terminal 2: python dashboard.py
"""

import json
import os
import time
import yaml
from datetime import datetime

import binance_feed
import polymarket_client
import arbitrage_detector
import paper_trader


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config():
    with open("config.yaml") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log_event(msg):
    os.makedirs("logs", exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    with open("logs/trades.log", "a") as f:
        f.write(line + "\n")


def log_decision(decision_dict):
    os.makedirs("logs", exist_ok=True)
    decisions = []
    path = "logs/decisions.json"
    if os.path.exists(path):
        with open(path) as f:
            try:
                decisions = json.load(f)
            except Exception:
                decisions = []
    decisions.append({
        "timestamp": datetime.now().isoformat(),
        **decision_dict,
    })
    decisions = decisions[-200:]   # keep last 200
    with open(path, "w") as f:
        json.dump(decisions, f, indent=2)


# ---------------------------------------------------------------------------
# Market cache (avoid repeated discovery on each cycle)
# ---------------------------------------------------------------------------

_cached_markets = {}   # keyed by keyword group "btc" / "eth"
_cache_ts       = {}
CACHE_TTL       = 300  # re-discover every 5 minutes


def get_active_market(config, keywords, label):
    """Return market odds dict or None for a given keyword group."""
    global _cached_markets, _cache_ts

    now = time.time()

    # Use configured market ID if provided
    fixed_id = config.get("polymarket_condition_id")
    if fixed_id:
        return polymarket_client.get_market_odds(fixed_id)

    # Use cache
    if label in _cached_markets and (now - _cache_ts.get(label, 0)) < CACHE_TTL:
        cid  = _cached_markets[label]["condition_id"]
        odds = polymarket_client.get_market_odds(cid)
        if odds:
            return odds

    # Discover
    print(f"  Searching Polymarket for active {label.upper()} markets...")
    markets = polymarket_client.find_btc_markets(keywords)

    if not markets:
        print(f"  No {label.upper()} markets found.")
        return None

    best = polymarket_client.pick_best_market(markets)
    if not best:
        return None

    odds = polymarket_client.get_market_odds_from_gamma(best)
    if odds:
        _cached_markets[label] = odds
        _cache_ts[label]       = now
        print(f"  [{label.upper()}] Market: {odds['question'][:70]}")
        print(f"  YES={odds['yes_price']:.3f}  NO={odds['no_price']:.3f}")

    return odds


# ---------------------------------------------------------------------------
# Single trading cycle
# ---------------------------------------------------------------------------

def run_cycle(feeds):
    config  = load_config()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"\n{'='*62}")
    print(f"  CYCLE — {now_str}")
    print(f"{'='*62}")

    # --- Portfolio state ---
    summary = paper_trader.get_summary()
    balance = summary["balance_usdc"]
    print(f"  Balance: ${balance:.2f} | PnL: ${summary['total_pnl']:+.2f} | "
          f"W/L: {summary['wins']}/{summary['losses']} | "
          f"Open bets: {summary['open_bets']}")

    # --- Kill switch ---
    if balance <= config["kill_switch_balance"]:
        print(f"\n  KILL SWITCH — balance ${balance:.2f} is at or below "
              f"${config['kill_switch_balance']}")
        log_event(f"KILL SWITCH triggered at ${balance:.2f}")
        return "STOPPED"

    # --- Ceiling ---
    if balance >= config["ceiling_balance"]:
        print(f"\n  CEILING HIT — balance ${balance:.2f} >= "
              f"${config['ceiling_balance']}")
        log_event(f"CEILING HIT at ${balance:.2f} — stopping all trading")
        return "CEILING_HIT"

    # --- Check each feed (BTC + ETH) ---
    feed_configs = [
        ("btc", feeds["btc"], ["BTC", "bitcoin"]),
        ("eth", feeds["eth"], ["ETH", "ethereum"]),
    ]

    for label, feed, keywords in feed_configs:
        snap = feed.get_snapshot()
        if snap is None:
            print(f"  No {label.upper()} price yet — waiting...")
            continue

        print(f"  {label.upper()}: ${snap['price']:,.2f} | "
              f"mom60={snap['momentum_60s_pct']:+.3f}% | "
              f"mom30={snap['momentum_30s_pct']:+.3f}%")

        # Resolve expired bets using this asset's price
        paper_trader.resolve_expired_bets(snap["price"])

        # --- Polymarket odds ---
        market_odds = get_active_market(config, keywords, label)
        if market_odds is None:
            print(f"  No active {label.upper()} market found.")
            continue

        if not market_odds.get("active", True):
            print(f"  {label.upper()} market inactive — clearing cache.")
            _cached_markets.pop(label, None)
            continue

        # --- Arbitrage detection + Claude ---
        opp, decision = arbitrage_detector.detect_and_analyze(
            snap, market_odds, summary
        )

        if opp is None:
            print(f"  [{label.upper()}] No opportunity — HOLD.")
            log_decision({
                "action":    "HOLD",
                "reason":    f"momentum {snap['momentum_60s_pct']:+.3f}% below threshold",
                "btc_price": snap["price"],
                "yes_price": market_odds["yes_price"],
                "no_price":  market_odds["no_price"],
            })
            continue

        if decision is None or decision["action"] == "PASS":
            reason = decision["reasoning"] if decision else "Claude unavailable"
            print(f"  [{label.upper()}] Claude says PASS — {reason[:80]}")
            log_decision({
                "action":    "PASS",
                "reason":    reason,
                "direction": opp["direction"],
                "edge":      opp["edge"],
                "btc_price": snap["price"],
            })
            continue

        # --- Place bet ---
        action    = decision["action"]
        direction = "YES" if action == "BET_YES" else "NO"
        amount    = decision["suggested_amount"]
        odds      = market_odds["yes_price"] if direction == "YES" else market_odds["no_price"]

        bet = paper_trader.place_bet(
            direction          = direction,
            amount_usdc        = amount,
            entry_odds         = odds,
            market_question    = market_odds["question"],
            btc_price_at_entry = snap["price"],
            reasoning          = decision["reasoning"],
        )

        if bet:
            log_line = (
                f"[{label.upper()}] BET {direction} ${amount:.2f} @ {odds*100:.1f}% | "
                f"price=${snap['price']:.2f} | "
                f"mom={opp['momentum_pct']:+.3f}% | "
                f"edge={opp['edge']*100:+.1f}% | "
                f"conf={decision['confidence']*100:.0f}% | "
                f"{market_odds['question'][:50]}"
            )
            log_event(log_line)
            log_decision({
                "action":     action,
                "direction":  direction,
                "amount":     amount,
                "odds":       odds,
                "edge":       opp["edge"],
                "confidence": decision["confidence"],
                "reasoning":  decision["reasoning"],
                "btc_price":  snap["price"],
                "market":     market_odds["question"][:60],
            })

        # Refresh summary after bet
        summary = paper_trader.get_summary()

    return "OK"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    config   = load_config()
    interval = config["run_interval_seconds"]
    mode     = "PAPER TRADING" if config["paper_trading"] else "LIVE TRADING"

    print(f"""
+----------------------------------------------------------+
|  POLYMARKET LATENCY ARBITRAGE BOT                        |
|  Mode    : {mode:<46}|
|  Balance : ${config['starting_balance_usdc']:<5.2f}  Kill: ${config['kill_switch_balance']:<5}  Ceiling: ${config['ceiling_balance']:<5}  |
|  Max bet : ${config['max_bet_usdc']:<5}  Min edge: {config['min_edge_probability']*100:.0f}%                    |
+----------------------------------------------------------+
""")

    # Start Binance WebSocket feeds for BTC and ETH
    btc_feed = binance_feed.BinancePriceFeed(symbol="btcusdt")
    eth_feed = binance_feed.BinancePriceFeed(symbol="ethusdt")

    btc_feed.start()
    eth_feed.start()

    feeds = {"btc": btc_feed, "eth": eth_feed}

    while True:
        try:
            status = run_cycle(feeds)
            if status in ("STOPPED", "CEILING_HIT"):
                if status == "CEILING_HIT":
                    summary = paper_trader.get_summary()
                    print(f"\n  CONGRATULATIONS!  Balance: ${summary['balance_usdc']:.2f}")
                    print(f"  Win rate: {summary['win_rate']}%  "
                          f"Total trades: {summary['bets_placed']}")
                else:
                    print("\n  Bot stopped.  Check logs/trades.log")
                break
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\n\n  Stopped by user.")
            btc_feed.stop()
            eth_feed.stop()
            break
        except Exception as exc:
            print(f"\n  Error: {exc}")
            print(f"  Retrying in {interval} seconds...")
            time.sleep(interval)

    btc_feed.stop()
    eth_feed.stop()


if __name__ == "__main__":
    main()
