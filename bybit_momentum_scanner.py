#!/usr/bin/env python3
# bybit_momentum_scanner.py
# Requires: requests, python-dotenv

import os
import time
import requests
import logging
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# --- Load environment variables ---
load_dotenv()

# --- CONFIG from .env file ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))            # seconds
WINDOW_SECONDS = int(os.getenv("WINDOW_SECONDS", "900"))          # 15 minutes
PRICE_CHANGE_PCT = float(os.getenv("PRICE_CHANGE_PCT", "3.0"))    # percent
VOL_MULTIPLIER = float(os.getenv("VOL_MULTIPLIER", "2.0"))        # x times
ACTIVE_START_HOUR = int(os.getenv("ACTIVE_START_HOUR", "3"))      # WIB
ACTIVE_END_HOUR = int(os.getenv("ACTIVE_END_HOUR", "6"))          # WIB (exclusive)
MIN_TURNOVER = float(os.getenv("MIN_TURNOVER", "1000"))
BYBIT_CATEGORY = os.getenv("BYBIT_CATEGORY", "spot")              # spot/linear/etc

# --- Constants ---
BYBIT_URLS = [
    f"https://api.bybit.com/v5/market/tickers?category={BYBIT_CATEGORY}",
    f"https://api.bytick.com/v5/market/tickers?category={BYBIT_CATEGORY}",
    f"https://api2.bybit.com/v5/market/tickers?category={BYBIT_CATEGORY}"
]
TELEGRAM_SEND_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

# --- Logging setup ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("scanner")

# --- State ---
snapshots = {}   # symbol -> {"price": float, "vol": float, "ts": epoch}
last_alert = {}  # symbol -> epoch
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "BybitMomentumScanner/1.0"})

# --- Telegram Send ---
def send_telegram(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        logger.warning("Telegram config missing. Skipping send.")
        return False
    try:
        payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
        r = SESSION.post(TELEGRAM_SEND_URL, data=payload, timeout=10)
        if r.status_code == 200:
            return True
        else:
            logger.error("Telegram send failed: %s %s", r.status_code, r.text)
            return False
    except Exception as e:
        logger.exception("Telegram send exception: %s", e)
        return False

# --- Fetch Bybit Data ---
def fetch_bybit_tickers():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json",
        "Referer": "https://www.bybit.com/"
    }

    for url in BYBIT_URLS:
        try:
            r = SESSION.get(url, headers=headers, timeout=10)
            if r.status_code == 403:
                logger.warning("403 Forbidden on %s — trying next mirror...", url)
                continue
            r.raise_for_status()
            j = r.json()
            result = j.get("result", {}).get("list", [])
            if not result:
                logger.warning("⚠️ Empty result from Bybit: %s", j)
            return result
        except Exception as e:
            logger.warning("Fetch failed from %s: %s", url, e)
            continue

    logger.error("❌ All Bybit endpoints failed.")
    return []

# --- Time filter ---
def is_active_wib(now_utc):
    now_wib = now_utc + timedelta(hours=7)
    h = now_wib.hour
    # sementara aktif terus untuk testing
    return True
    # jika mau aktif hanya jam tertentu, pakai ini:
    # return ACTIVE_START_HOUR <= h < ACTIVE_END_HOUR

def human_ts(ts=None):
    return datetime.fromtimestamp(ts or time.time()).strftime("%Y-%m-%d %H:%M:%S")

# --- Main Loop ---
def main_loop():
    logger.info("Starting Bybit Momentum Scanner (category=%s)", BYBIT_CATEGORY)
    send_telegram("✅ Scanner aktif! Bybit Momentum Scanner sudah berjalan 🚀")

    while True:
        try:
            now_utc = datetime.now(timezone.utc)
            if not is_active_wib(now_utc):
                logger.info("Outside active hours (WIB %02d-%02d). Sleeping...", ACTIVE_START_HOUR, ACTIVE_END_HOUR)
                time.sleep(60 * 5)
                continue

            tickers = fetch_bybit_tickers()
            now_epoch = int(time.time())

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

# --- Run ---
if __name__ == "__main__":
    main_loop()
