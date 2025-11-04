#!/usr/bin/env python3
# streamlit_bybit_momentum.py

import os
import time
import hmac
import hashlib
import requests
import streamlit as st
import threading
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

# === CONFIG ===
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))
WINDOW_SECONDS = int(os.getenv("WINDOW_SECONDS", "900"))
PRICE_CHANGE_PCT = float(os.getenv("PRICE_CHANGE_PCT", "3.0"))
VOL_MULTIPLIER = float(os.getenv("VOL_MULTIPLIER", "2.0"))
ACTIVE_START_HOUR = int(os.getenv("ACTIVE_START_HOUR", "3"))
ACTIVE_END_HOUR = int(os.getenv("ACTIVE_END_HOUR", "6"))
MIN_TURNOVER = float(os.getenv("MIN_TURNOVER", "1000"))
BYBIT_CATEGORY = os.getenv("BYBIT_CATEGORY", "spot")

BYBIT_URL = f"https://api.bybit.com/v5/market/tickers?category={BYBIT_CATEGORY}"
TELEGRAM_SEND_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

# === State ===
snapshots = {}
last_alert = {}
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "BybitMomentumScanner/1.1"})


def send_telegram(text):
    """Send message to Telegram"""
    if not TELEGRAM_TOKEN or not CHAT_ID:
        st.warning("⚠️ Telegram config missing.")
        return False
    try:
        payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
        r = SESSION.post(TELEGRAM_SEND_URL, data=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        st.error(f"Telegram send error: {e}")
        return False


def signed_request(url):
    """Bybit signed request (authentic)"""
    ts = str(int(time.time() * 1000))
    query = f"api_key={BYBIT_API_KEY}&timestamp={ts}"
    signature = hmac.new(BYBIT_API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    headers = {"X-BYBIT-API-KEY": BYBIT_API_KEY}
    r = SESSION.get(f"{url}&api_key={BYBIT_API_KEY}&timestamp={ts}&sign={signature}", headers=headers)
    r.raise_for_status()
    return r.json()


def fetch_bybit_tickers():
    try:
        j = signed_request(BYBIT_URL)
        return j.get("result", {}).get("list", [])
    except Exception as e:
        st.error(f"❌ Failed to fetch tickers: {e}")
        return []


def is_active_wib(now_utc):
    now_wib = now_utc + timedelta(hours=7)
    return ACTIVE_START_HOUR <= now_wib.hour < ACTIVE_END_HOUR


def main_loop(stop_event):
    """Main scanning loop"""
    start_time = datetime.now(timezone.utc) + timedelta(hours=7)
    send_telegram(f"🚀 *Bybit Momentum Scanner Started*\n"
                  f"Status: Running...\n"
                  f"Time (WIB): {start_time.strftime('%Y-%m-%d %H:%M:%S')}")

    last_heartbeat = time.time()

    st.info(f"🚀 Running Bybit Momentum Scanner ({BYBIT_CATEGORY})...")

    while not stop_event.is_set():
        now_utc = datetime.now(timezone.utc)

        # Send heartbeat every 30 minutes
        if time.time() - last_heartbeat > 1800:
            send_telegram(f"⏱ *Still running...*\n"
                          f"Last check: {(now_utc + timedelta(hours=7)).strftime('%Y-%m-%d %H:%M:%S')} WIB")
            last_heartbeat = time.time()

        if not is_active_wib(now_utc):
            st.write(f"🕒 Outside active hours ({ACTIVE_START_HOUR}-{ACTIVE_END_HOUR} WIB). Sleeping 5m...")
            time.sleep(300)
            continue

        tickers = fetch_bybit_tickers()
        now_epoch = int(time.time())
        st.write(f"Scanning {len(tickers)} tickers...")

        for t in tickers:
            try:
                symbol = t.get("symbol")
                if not symbol or not symbol.endswith("USDT"):
                    continue

                last_price = float(t.get("lastPrice", 0) or 0)
                turnover24h = float(t.get("turnover24h", 0) or 0)

                if turnover24h < MIN_TURNOVER:
                    snapshots.setdefault(symbol, {"price": last_price, "vol": turnover24h, "ts": now_epoch})
                    continue

                prev = snapshots.get(symbol)
                if not prev:
                    snapshots[symbol] = {"price": last_price, "vol": turnover24h, "ts": now_epoch}
                    continue

                elapsed = now_epoch - prev["ts"]
                if elapsed >= WINDOW_SECONDS:
                    price_change_pct = ((last_price - prev["price"]) / prev["price"]) * 100 if prev["price"] > 0 else 0
                    vol_multiplier = (turnover24h / prev["vol"]) if prev["vol"] > 0 else 0

                    if price_change_pct >= PRICE_CHANGE_PCT and vol_multiplier >= VOL_MULTIPLIER:
                        last_alert_time = last_alert.get(symbol, 0)
                        if now_epoch - last_alert_time >= WINDOW_SECONDS:
                            msg = (f"🚀 *Momentum Alert*\n"
                                   f"{symbol}\n"
                                   f"Price: {last_price:.6f}\n"
                                   f"Δ{int(WINDOW_SECONDS/60)}m: {price_change_pct:.2f}%\n"
                                   f"Vol x: {vol_multiplier:.2f}\n"
                                   f"Time (WIB): {(now_utc + timedelta(hours=7)).strftime('%H:%M:%S')}")
                            st.success(f"Alert: {symbol} ({price_change_pct:.2f}%)")
                            send_telegram(msg)
                            last_alert[symbol] = now_epoch

                    snapshots[symbol] = {"price": last_price, "vol": turnover24h, "ts": now_epoch}

            except Exception as e:
                st.error(f"Error processing {symbol}: {e}")
                continue

        time.sleep(CHECK_INTERVAL)

    send_telegram(f"🛑 *Bybit Momentum Scanner Stopped*\nTime: {(datetime.now(timezone.utc) + timedelta(hours=7)).strftime('%Y-%m-%d %H:%M:%S')} WIB")


# === Streamlit UI ===
st.title("📊 Bybit Momentum Scanner (Auto Telegram)")

if "thread" not in st.session_state:
    st.session_state.thread = None
    st.session_state.stop_event = threading.Event()

if st.button("▶️ Start Scanner"):
    if st.session_state.thread and st.session_state.thread.is_alive():
        st.warning("Scanner is already running!")
    else:
        st.session_state.stop_event.clear()
        st.session_state.thread = threading.Thread(target=main_loop, args=(st.session_state.stop_event,))
        st.session_state.thread.start()
        st.success("Scanner started!")

if st.button("⏹ Stop Scanner"):
    if st.session_state.thread and st.session_state.thread.is_alive():
        st.session_state.stop_event.set()
        st.session_state.thread.join()
        st.success("Scanner stopped.")
    else:
        st.info("Scanner is not running.")
