"""
paper_trader.py
Simulates Polymarket bets with fake USDC.
Persists state to logs/paper_portfolio.json.

Resolution logic:
  After `bet_resolution_minutes` we look at whether BTC moved in the
  predicted direction relative to BTC price at bet entry.
  If correct -> payout = amount / entry_odds  (binary payoff at $1)
  If wrong   -> lose amount
"""

import json
import os
import yaml
from datetime import datetime


PORTFOLIO_FILE = "logs/paper_portfolio.json"


def load_config():
    with open("config.yaml") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Portfolio I/O
# ---------------------------------------------------------------------------

def _default_portfolio():
    config = load_config()
    return {
        "balance_usdc":   config["starting_balance_usdc"],
        "starting_balance": config["starting_balance_usdc"],
        "open_bets":      [],
        "closed_bets":    [],
        "total_pnl":      0.0,
        "wins":           0,
        "losses":         0,
        "bets_placed":    0,
        "balance_history": [{"t": datetime.now().isoformat(), "b": config["starting_balance_usdc"]}],
    }


def load_portfolio():
    os.makedirs("logs", exist_ok=True)
    if not os.path.exists(PORTFOLIO_FILE):
        p = _default_portfolio()
        save_portfolio(p)
        return p
    with open(PORTFOLIO_FILE) as f:
        try:
            return json.load(f)
        except Exception:
            p = _default_portfolio()
            save_portfolio(p)
            return p


def save_portfolio(portfolio):
    os.makedirs("logs", exist_ok=True)
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(portfolio, f, indent=2)


# ---------------------------------------------------------------------------
# Core actions
# ---------------------------------------------------------------------------

def _record_balance(portfolio):
    history = portfolio.setdefault("balance_history", [])
    history.append({"t": datetime.now().isoformat(), "b": portfolio["balance_usdc"]})
    if len(history) > 500:
        portfolio["balance_history"] = history[-500:]


def place_bet(direction, amount_usdc, entry_odds, market_question,
              btc_price_at_entry, reasoning):
    """
    direction        : "YES" | "NO"
    amount_usdc      : dollars to risk
    entry_odds       : Polymarket price for our side (0.0 – 1.0)
    btc_price_at_entry: BTC price right now (used for resolution)
    reasoning        : string from Claude

    Returns bet dict or None if insufficient balance / kill switch.
    """
    config     = load_config()
    portfolio  = load_portfolio()
    balance    = portfolio["balance_usdc"]

    if balance <= config["kill_switch_balance"]:
        print(f"  [Trader] Kill switch active — balance ${balance:.2f} <= "
              f"${config['kill_switch_balance']}.  No bet placed.")
        return None

    if balance >= config["ceiling_balance"]:
        print(f"  [Trader] Ceiling reached — balance ${balance:.2f}.  Stopping.")
        return None

    amount_usdc = min(amount_usdc, config["max_bet_usdc"], balance)

    if amount_usdc < 0.50:
        print("  [Trader] Bet too small, skipping.")
        return None

    now = datetime.now()
    bet = {
        "id":                 f"bet_{now.strftime('%Y%m%d_%H%M%S')}",
        "direction":          direction,
        "amount_usdc":        round(amount_usdc, 4),
        "entry_odds":         round(entry_odds, 4),
        "potential_payout":   round(amount_usdc / entry_odds, 4) if entry_odds > 0 else 0,
        "potential_profit":   round(amount_usdc / entry_odds - amount_usdc, 4) if entry_odds > 0 else 0,
        "market_question":    market_question[:120],
        "btc_price_at_entry": btc_price_at_entry,
        "opened_at":          now.isoformat(),
        "reasoning":          reasoning[:300],
        "status":             "OPEN",
    }

    portfolio["balance_usdc"]  -= amount_usdc
    portfolio["balance_usdc"]   = round(portfolio["balance_usdc"], 4)
    portfolio["bets_placed"]   += 1
    portfolio["open_bets"].append(bet)
    _record_balance(portfolio)
    save_portfolio(portfolio)

    print(f"  [Trader] PAPER BET: {direction} ${amount_usdc:.2f} @ "
          f"{entry_odds*100:.1f}% odds | "
          f"potential payout=${bet['potential_payout']:.2f}")
    return bet


def resolve_expired_bets(current_btc_price):
    """
    Check all open bets.  Resolve any that have been open longer than
    bet_resolution_minutes.

    Resolution:
      - direction == "YES" => win if current_btc_price > btc_price_at_entry
      - direction == "NO"  => win if current_btc_price < btc_price_at_entry
    """
    config    = load_config()
    portfolio = load_portfolio()
    duration  = config.get("bet_resolution_minutes", 5) * 60  # seconds

    from datetime import timezone
    now = datetime.now()
    still_open = []
    log_lines  = []

    for bet in portfolio["open_bets"]:
        opened = datetime.fromisoformat(bet["opened_at"])
        elapsed = (now - opened).total_seconds()

        if elapsed < duration:
            still_open.append(bet)
            continue

        # --- Resolve ---
        entry_price = bet["btc_price_at_entry"]
        direction   = bet["direction"]

        if direction == "YES":
            won = current_btc_price > entry_price
        else:
            won = current_btc_price < entry_price

        if won:
            payout = bet["potential_payout"]
            pnl    = round(payout - bet["amount_usdc"], 4)
            portfolio["wins"]          += 1
            portfolio["balance_usdc"]  += payout
            result_str = f"WIN  +${pnl:.2f}"
            emoji = "WIN"
        else:
            payout = 0.0
            pnl    = -bet["amount_usdc"]
            portfolio["losses"]        += 1
            result_str = f"LOSS -${abs(pnl):.2f}"
            emoji = "LOSS"

        portfolio["total_pnl"]    += pnl
        portfolio["balance_usdc"]  = round(portfolio["balance_usdc"], 4)
        _record_balance(portfolio)

        closed_bet = {
            **bet,
            "status":              emoji,
            "closed_at":           now.isoformat(),
            "btc_price_at_close":  current_btc_price,
            "pnl_usdc":            round(pnl, 4),
            "payout":              round(payout, 4),
        }
        portfolio["closed_bets"].append(closed_bet)

        line = (
            f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] "
            f"RESOLVED {direction} | {result_str} | "
            f"BTC entry=${entry_price:.2f} exit=${current_btc_price:.2f} | "
            f"{bet['market_question'][:60]}"
        )
        log_lines.append(line)
        print(f"  [Trader] {line}")

    portfolio["open_bets"] = still_open
    save_portfolio(portfolio)

    if log_lines:
        os.makedirs("logs", exist_ok=True)
        with open("logs/trades.log", "a") as f:
            for line in log_lines:
                f.write(line + "\n")

    return log_lines


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def get_summary():
    portfolio = load_portfolio()
    wins      = portfolio["wins"]
    losses    = portfolio["losses"]
    total     = wins + losses
    win_rate  = round(wins / max(1, total) * 100, 1)

    return {
        "balance_usdc":    portfolio["balance_usdc"],
        "starting_balance": portfolio["starting_balance"],
        "total_pnl":       round(portfolio["total_pnl"], 4),
        "open_bets":       len(portfolio["open_bets"]),
        "closed_bets":     total,
        "wins":            wins,
        "losses":          losses,
        "win_rate":        win_rate,
        "bets_placed":     portfolio["bets_placed"],
        "open_bets_detail":  portfolio["open_bets"],
        "closed_bets_detail": list(reversed(portfolio["closed_bets"]))[:10],
    }
