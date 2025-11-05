#!/usr/bin/env python3
# bybit_momentum_scanner.py
# Requires: requests, python-dotenv, hmac, hashlib

import os
import time
import hmac
import hashlib
import requests
import logging
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# === Load ENV ===
load_dotenv()

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))
WINDOW_SECONDS = int(os.getenv("WINDOW_SECONDS", "900"))
PRICE_CHANGE_PCT = float(os.getenv("PRICE_CHANGE_PCT", "3.0"))
VOL_MULTIPLIER = float(os.getenv("VOL_MULTIPLIER", "2.0"))
MIN_TURNOVER = float(os.getenv("MIN_TURNOVER", "1000"))
BYBIT_CATEGORY = os.getenv("BYBIT_CATEGORY", "spot")

# === Constants ===
BYBIT_URL = "https://api.bybit.com"
TELEGRAM_SEND_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("scanner")

snapshots = {}
last_alert = {}
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "BybitMomentumScanner/2.0"})

# === AUTH SIGN ===
def bybit_request(endpoint, params=None):
    if params is None:
        params = {}
    ts = str(int(time.time() * 1000))
    recv_window = "5000"
    params_str = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
    signature_payload = ts + BYBIT_API_KEY + recv_window + params_str
    signature = hmac.new(BYBIT_API_SECRET.encode("utf-8"), signature_payload.encode("utf-8"), hashlib.sha256).hexdigest()

    headers = {
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-SIGN": signature,
        "X-BAPI-SIGN-TYPE": "2",
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv_window,
    }

    url = BYBIT_URL + endpoint
    try:
        r = SESSION.get(url, params=params, headers=headers, timeout=10)
        r.raise_for_status()
        j = r.json()
        if j.get("retCode") == 0:
            return j.get("result", {}).get("list", [])
        else:
            logger.error("Bybit error: %s", j)
            return []
    except Exception as e:
        logger.exception("Bybit fetch error: %s", e)
        return []

# === Telegram ===
def send_telegram(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        logger.warning("Telegram config missing. Skipping send.")
        return False
    try:
        payload = {"chat_id": CHAT_ID, "text": text}
        r = SESSION.post(TELEGRAM_SEND_URL, data=payload, timeout=10)
        if r.status_code == 200:
            return True
        else:
            logger.error("Telegram send failed: %s %s", r.status_code, r.text)
            return False
    except Exception as e:
        logger.exception("Telegram send exception: %s", e)
        return False

# === Main Loop ===
def main_loop():
    logger.info("Starting Bybit Momentum Scanner (auth mode, category=%s)", BYBIT_CATEGORY)
    send_telegram("✅ Scanner aktif (API Key) — Bybit Momentum Scanner berjalan 🚀")

    while True:
        try:
            tickers = bybit_request("/v5/market/tickers", {"category": BYBIT_CATEGORY})
            now_epoch = int(time.time())
            now_utc = datetime.now(timezone.utc)

            logger.info("Scanning %d tickers ...", len(tickers))

            for t in tickers:
                try:
                    symbol = t.get("symbol")
                    if not symbol or not symbol.endswith("USDT"):
                        continue

                    last_price = float(t.get("lastPrice", 0) or 0)
                    turnover24h = float(t.get("turnover24h", 0) or 0)

                    if turnover24h < MIN_TURNOVER:
                        if symbol not in snapshots:
                            snapshots[symbol] = {"price": last_price, "vol": turnover24h, "ts": now_epoch}
                        continue

                    if symbol not in snapshots:
                        snapshots[symbol] = {"price": last_price, "vol": turnover24h, "ts": now_epoch}
                        continue

                    prev = snapshots[symbol]
                    elapsed = now_epoch - prev["ts"]

                    if elapsed >= WINDOW_SECONDS:
                        price_change_pct = ((last_price - prev["price"]) / prev["price"]) * 100 if prev["price"] > 0 else 0
                        vol_multiplier = (turnover24h / prev["vol"]) if prev["vol"] > 0 else 0

                        last_alert_time = last_alert.get(symbol, 0)
                        MIN_ALERT_GAP = WINDOW_SECONDS

                        if price_change_pct >= PRICE_CHANGE_PCT and vol_multiplier >= VOL_MULTIPLIER:
                            if now_epoch - last_alert_time >= MIN_ALERT_GAP:
                                msg = (
                                    "🚀 *Momentum Alert*\n"
                                    f"{symbol}\n"
                                    f"Price: {last_price:.6f}\n"
                                    f"Δ{int(WINDOW_SECONDS/60)}m: {price_change_pct:.2f}%\n"
                                    f"Vol x: {vol_multiplier:.2f}\n"
                                    f"Time (WIB): {(now_utc + timedelta(hours=7)).strftime('%Y-%m-%d %H:%M:%S')}"
                                )
                                logger.info("ALERT %s -> price %.2f%% vol x%.2f", symbol, price_change_pct, vol_multiplier)
                                send_telegram(msg)
                                last_alert[symbol] = now_epoch

                        snapshots[symbol] = {"price": last_price, "vol": turnover24h, "ts": now_epoch}

                except Exception as e:
                    logger.exception("Error processing ticker: %s", e)
                    continue

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            logger.info("Stopping scanner by user.")
            break
        except Exception as e:
            logger.exception("Unexpected error main loop: %s", e)
            time.sleep(10)


if __name__ == "__main__":
    main_loop()
