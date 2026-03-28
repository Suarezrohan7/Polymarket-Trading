"""
polymarket_client.py
Fetches live market data from Polymarket.

Two APIs:
  - Gamma API  (https://gamma-api.polymarket.com) — market search & metadata
  - CLOB API   (https://clob.polymarket.com)       — live YES/NO prices

No auth needed for read-only access.
"""

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE  = "https://clob.polymarket.com"

TIMEOUT = 10
VERIFY_SSL = False


# ---------------------------------------------------------------------------
# Market discovery
# ---------------------------------------------------------------------------

def find_btc_markets(keywords=None):
    """
    Search for active BTC price-prediction markets on Polymarket.
    Returns a list of market dicts, or [] if none found / API unreachable.
    """
    if keywords is None:
        keywords = ["BTC", "bitcoin", "price"]

    found = []
    for kw in keywords:
        try:
            resp = requests.get(
                f"{GAMMA_BASE}/markets",
                params={
                    "active": "true",
                    "closed": "false",
                    "q": kw,
                    "limit": 50,
                },
                timeout=TIMEOUT,
                verify=VERIFY_SSL,

            )
            resp.raise_for_status()
            markets = resp.json()
            if isinstance(markets, list):
                found.extend(markets)
            elif isinstance(markets, dict) and "data" in markets:
                found.extend(markets["data"])
        except Exception as exc:
            print(f"  [Polymarket] Search error for '{kw}': {exc}")

    # Deduplicate by conditionId
    seen = set()
    unique = []
    for m in found:
        cid = m.get("conditionId") or m.get("condition_id")
        if cid and cid not in seen:
            seen.add(cid)
            unique.append(m)

    return unique


def pick_best_market(markets):
    """
    From a list of candidate markets, pick the most suitable one.
    Prefer: binary YES/NO markets, highest volume, shortest end date.
    Returns a market dict or None.
    """
    binary = [
        m for m in markets
        if m.get("outcomePrices") or m.get("tokens") or m.get("clobTokenIds")
    ]
    if not binary:
        binary = markets

    # Sort by volume (descending) — use whatever field is available
    def vol_key(m):
        return float(m.get("volume24hr", 0) or m.get("volumeNum", 0) or 0)

    binary.sort(key=vol_key, reverse=True)
    return binary[0] if binary else None


# ---------------------------------------------------------------------------
# Live odds
# ---------------------------------------------------------------------------

def get_market_odds(condition_id):
    """
    Fetch live YES and NO prices for a market from the CLOB.
    Returns dict or None on failure.

    YES price == implied probability that YES resolves (0.0 – 1.0).
    """
    try:
        resp = requests.get(
            f"{CLOB_BASE}/markets/{condition_id}",
            timeout=TIMEOUT,
            verify=VERIFY_SSL,
        )
        resp.raise_for_status()
        market = resp.json()
    except Exception as exc:
        print(f"  [Polymarket] CLOB market fetch error: {exc}")
        return None

    tokens = market.get("tokens", [])
    if not tokens:
        return None

    yes_token = next(
        (t for t in tokens if t.get("outcome", "").upper() == "YES"), None
    )
    no_token = next(
        (t for t in tokens if t.get("outcome", "").upper() == "NO"), None
    )

    if not yes_token or not no_token:
        # fallback: assume first=YES, second=NO
        if len(tokens) >= 2:
            yes_token, no_token = tokens[0], tokens[1]
        else:
            return None

    yes_price = _get_midpoint(yes_token["token_id"])
    no_price  = _get_midpoint(no_token["token_id"])

    if yes_price is None or no_price is None:
        return None

    return {
        "condition_id": condition_id,
        "question": market.get("question", "Unknown market"),
        "yes_price": yes_price,
        "no_price": no_price,
        "yes_token_id": yes_token["token_id"],
        "no_token_id": no_token["token_id"],
        "active": market.get("active", True),
    }


def get_market_odds_from_gamma(market_dict):
    """
    Extract YES/NO prices directly from a Gamma API market object
    (no extra CLOB call needed if outcomePrices is present).
    Returns same dict shape as get_market_odds(), or None.
    """
    prices_raw = market_dict.get("outcomePrices")
    question   = market_dict.get("question", "Unknown market")
    cid        = market_dict.get("conditionId") or market_dict.get("condition_id")

    if prices_raw and len(prices_raw) >= 2:
        try:
            prices = [float(p) for p in prices_raw]
            yes_price = prices[0]
            no_price  = prices[1]
            return {
                "condition_id": cid,
                "question": question,
                "yes_price": yes_price,
                "no_price": no_price,
                "yes_token_id": None,
                "no_token_id": None,
                "active": market_dict.get("active", True),
            }
        except (ValueError, TypeError):
            pass

    # Fall back to CLOB if we have a conditionId
    if cid:
        return get_market_odds(cid)
    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_midpoint(token_id):
    """Get midpoint price for a CLOB token. Returns float or None."""
    try:
        resp = requests.get(
            f"{CLOB_BASE}/midpoint",
            params={"token_id": token_id},
            timeout=TIMEOUT,
            verify=VERIFY_SSL,
        )
        resp.raise_for_status()
        mid = resp.json().get("mid")
        return float(mid) if mid is not None else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Searching for BTC markets on Polymarket...")
    markets = find_btc_markets()
    print(f"Found {len(markets)} market(s)")
    for m in markets[:5]:
        print(f"  {m.get('question', '?')[:80]}")

    best = pick_best_market(markets)
    if best:
        print(f"\nBest market: {best.get('question')}")
        cid = best.get("conditionId") or best.get("condition_id")
        odds = get_market_odds_from_gamma(best)
        if odds:
            print(f"  YES={odds['yes_price']:.3f}  NO={odds['no_price']:.3f}")
        else:
            print("  Could not fetch odds.")
