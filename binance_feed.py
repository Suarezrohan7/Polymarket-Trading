"""
binance_feed.py
Real-time BTC/USDT price via Binance WebSocket.
Completely free — no API key needed.
Tracks price history so we can compute momentum.
"""

import json
import threading
import time
from collections import deque

import websocket


class BinancePriceFeed:
    """
    Connects to Binance trade stream for any symbol (default BTC/USDT).
    Runs in a background thread.  Main thread calls get_snapshot().
    """

    def __init__(self, symbol="btcusdt", history_seconds=120):
        self.symbol = symbol.lower()
        self.WS_URL = f"wss://stream.binance.com:9443/ws/{self.symbol}@trade"
        self.current_price = None
        self._history = deque()          # list of (unix_timestamp, price)
        self._history_seconds = history_seconds
        self._lock = threading.Lock()
        self._thread = None
        self._running = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self):
        """Start background thread.  Blocks up to 15 s for first price."""
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        label = self.symbol.upper().replace("USDT", "")
        print(f"  Connecting to Binance WebSocket ({label})...")
        for _ in range(30):
            if self.current_price is not None:
                print(f"  Connected!  {label} = ${self.current_price:,.2f}")
                return True
            time.sleep(0.5)

        print(f"  Warning: no initial price received for {label} — feed may be slow.")
        return False

    def stop(self):
        self._running = False

    def get_snapshot(self):
        """
        Returns dict with current price and momentum figures,
        or None if not connected yet.
        """
        with self._lock:
            if self.current_price is None:
                return None
            m60 = self._momentum(60)
            m30 = self._momentum(30)
            return {
                "price": self.current_price,
                "momentum_60s_pct": m60,
                "momentum_30s_pct": m30,
                "history_points": len(self._history),
            }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_loop(self):
        """Reconnect loop — keeps WebSocket alive forever."""
        while self._running:
            try:
                ws = websocket.WebSocketApp(
                    self.WS_URL,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:
                if self._running:
                    print(f"  [Binance] WebSocket error: {exc}")
            if self._running:
                time.sleep(5)  # brief pause before reconnecting

    def _on_message(self, ws, raw):
        data = json.loads(raw)
        price = float(data["p"])          # trade price
        ts = time.time()
        with self._lock:
            self.current_price = price
            self._history.append((ts, price))
            self._trim_history(ts)

    def _on_error(self, ws, error):
        pass  # reconnect loop handles this

    def _on_close(self, ws, code, msg):
        pass

    def _trim_history(self, now):
        """Remove entries older than history_seconds."""
        cutoff = now - self._history_seconds
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()

    def _momentum(self, seconds):
        """
        % price change over the last `seconds` seconds.
        Returns None if not enough history.
        """
        if len(self._history) < 2:
            return None
        now = time.time()
        cutoff = now - seconds
        # Find oldest price within the window
        baseline = None
        for ts, price in self._history:
            if ts >= cutoff:
                baseline = price
                break
        if baseline is None:
            baseline = self._history[0][1]
        if baseline == 0:
            return None
        return round(((self.current_price - baseline) / baseline) * 100, 4)


# Quick manual test
if __name__ == "__main__":
    feed = BinancePriceFeed()
    feed.start()
    for _ in range(10):
        snap = feed.get_snapshot()
        if snap:
            print(f"BTC ${snap['price']:,.2f}  "
                  f"mom60={snap['momentum_60s_pct']}%  "
                  f"mom30={snap['momentum_30s_pct']}%")
        time.sleep(3)
    feed.stop()
