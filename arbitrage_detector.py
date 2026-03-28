"""
arbitrage_detector.py
Compares Binance BTC momentum vs Polymarket odds.
Uses Claude to confirm opportunity and produce final recommendation.
"""

import json
import os
import yaml
import anthropic
from dotenv import load_dotenv
load_dotenv()


def load_config():
    with open("config.yaml") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Rule-based pre-filter (cheap, no API call)
# ---------------------------------------------------------------------------

def estimate_win_probability(momentum_pct):
    """
    Rough mapping: BTC momentum -> probability of resolving in that direction.

    |momentum| = 0.30% -> ~62%
    |momentum| = 0.50% -> ~70%
    |momentum| = 0.80% -> ~80%
    |momentum| = 1.00% -> ~86%

    Formula: prob = 0.5 + tanh(|momentum| * 2.5) * 0.5
    Saturates near 1.0 for very large moves.
    """
    import math
    abs_mom = abs(momentum_pct)
    return round(0.5 + math.tanh(abs_mom * 2.5) * 0.5, 4)


def quick_check(price_snapshot, market_odds, config):
    """
    Fast rule-based filter.  Returns an opportunity dict or None.

    Opportunity dict keys:
      direction       "YES" | "NO"
      momentum_pct    float
      our_probability float  (estimated win probability)
      market_price    float  (what Polymarket charges for our side)
      edge            float  (our_prob - market_price)
    """
    momentum = price_snapshot.get("momentum_60s_pct")
    if momentum is None:
        return None

    threshold = config.get("momentum_threshold_pct", 0.30)
    min_prob  = config.get("min_edge_probability", 0.70)

    if abs(momentum) < threshold:
        return None   # not moving enough

    our_prob = estimate_win_probability(momentum)

    if momentum > 0:
        direction    = "YES"
        market_price = market_odds["yes_price"]
    else:
        direction    = "NO"
        market_price = market_odds["no_price"]

    edge = our_prob - market_price

    # Only proceed if we believe we're right more often than min_prob
    # AND market is offering us better value than our estimate
    if our_prob < min_prob:
        return None

    return {
        "direction":    direction,
        "momentum_pct": round(momentum, 4),
        "our_probability": our_prob,
        "market_price": market_price,
        "edge":         round(edge, 4),
    }


# ---------------------------------------------------------------------------
# Claude analysis (confirms or overrides rule-based signal)
# ---------------------------------------------------------------------------

def ask_claude(price_snapshot, market_odds, opportunity, portfolio_summary, config):
    """
    Ask Claude to evaluate the arbitrage opportunity.
    Returns a decision dict:
      action          "BET_YES" | "BET_NO" | "PASS"
      confidence      0.0 – 1.0
      suggested_amount  float (USDC)
      reasoning       str
    """
    api_key = os.getenv("ANTHROPIC_API_KEY") or config.get("anthropic_api_key")
    client = anthropic.Anthropic(api_key=api_key)

    balance  = portfolio_summary.get("balance_usdc", 0)
    max_bet  = min(config["max_bet_usdc"], balance)
    open_bets = portfolio_summary.get("open_bets", 0)

    prompt = f"""You are an expert prediction-market arbitrageur.
You monitor Binance BTC real-time price and compare it against Polymarket odds.
Your edge: Binance moves fast — Polymarket odds update slowly.

CURRENT SITUATION
-----------------
BTC Price      : ${price_snapshot['price']:,.2f}
Momentum 60s   : {price_snapshot['momentum_60s_pct']:+.4f}%
Momentum 30s   : {price_snapshot['momentum_30s_pct']:+.4f}%

Polymarket market : "{market_odds['question']}"
YES price (prob)  : {market_odds['yes_price']:.4f}  ({market_odds['yes_price']*100:.1f}% implied)
NO price (prob)   : {market_odds['no_price']:.4f}   ({market_odds['no_price']*100:.1f}% implied)

Rule-based signal : {opportunity['direction']}
Our win estimate  : {opportunity['our_probability']*100:.1f}%
Polymarket offers : {opportunity['market_price']*100:.1f}% for our side
Edge              : {opportunity['edge']*100:+.1f}%

PORTFOLIO
---------
Balance       : ${balance:.2f} USDC
Open bets     : {open_bets}
Max bet       : ${max_bet:.2f} USDC

DECISION RULES
--------------
1. Only bet if BTC momentum is clearly directional and sustained.
2. Only bet YES when momentum strongly positive AND Polymarket YES < 65%.
3. Only bet NO  when momentum strongly negative AND Polymarket NO  < 65%.
4. PASS if: momentum is borderline, market is already pricing it in (odds > 70%),
   or there is any reason to doubt the signal.
5. Suggest bet size between $1 and ${max_bet:.0f} proportional to your confidence.
6. If balance < $12 — suggest smaller size.

Respond ONLY with valid JSON:
{{
  "action": "BET_YES" or "BET_NO" or "PASS",
  "confidence": 0.75,
  "suggested_amount": 4.00,
  "reasoning": "2-3 sentences explaining the decision."
}}

Only JSON. No other text."""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )

    text = message.content[0].text.strip()
    # Strip markdown fences if present
    if "```" in text:
        for part in text.split("```"):
            if "{" in part:
                text = part.strip()
                if text.startswith("json"):
                    text = text[4:].strip()
                break

    decision = json.loads(text)
    # Clamp suggested amount
    decision["suggested_amount"] = min(
        float(decision.get("suggested_amount", max_bet)),
        max_bet,
    )
    return decision


# ---------------------------------------------------------------------------
# Main entry point used by main.py
# ---------------------------------------------------------------------------

def detect_and_analyze(price_snapshot, market_odds, portfolio_summary):
    """
    Full pipeline: quick check -> Claude confirm.
    Returns (opportunity, claude_decision) tuple or (None, None).
    """
    config = load_config()

    opp = quick_check(price_snapshot, market_odds, config)
    if opp is None:
        return None, None

    print(f"  [Detector] Signal: {opp['direction']} | "
          f"mom={opp['momentum_pct']:+.3f}% | "
          f"our_prob={opp['our_probability']*100:.1f}% | "
          f"edge={opp['edge']*100:+.1f}%")
    print("  [Detector] Consulting Claude...")

    try:
        decision = ask_claude(
            price_snapshot, market_odds, opp, portfolio_summary, config
        )
    except Exception as exc:
        print(f"  [Detector] Claude error: {exc}")
        return opp, None

    print(f"  [Detector] Claude says: {decision['action']} "
          f"({decision['confidence']*100:.0f}% conf) "
          f"amount=${decision['suggested_amount']:.2f}")
    print(f"  [Detector] Reason: {decision['reasoning'][:100]}")

    return opp, decision
