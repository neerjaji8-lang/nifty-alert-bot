from flask import Flask, jsonify
import requests
import datetime
import json
import os

app = Flask(__name__)

# 🔹 Telegram bot setup
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# 🔹 NSE API URLs
OPTION_CHAIN_URL = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
FUTURE_URL = "https://www.nseindia.com/api/liveEquity-derivatives?index=nifty"

# 🔹 Session for NSE requests
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
})

# 🔹 Simple JSON cache
CACHE_FILE = "cache.json"

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_cache(data):
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f)

def is_market_open():
    now = datetime.datetime.now()
    if now.weekday() >= 5:  # Saturday/Sunday
        return False
    market_open = now.replace(hour=9, minute=15, second=0)
    market_close = now.replace(hour=15, minute=30, second=0)
    return market_open <= now <= market_close

def get_option_chain():
    res = session.get(OPTION_CHAIN_URL, timeout=10)
    data = res.json()
    return data

def calculate_percentage(cvol, total_oi):
    try:
        if total_oi > 0:
            return round((cvol / total_oi) * 100, 2)
        else:
            return 0
    except:
        return 0

@app.route("/")
def home():
    return jsonify({"status": "ok", "msg": "NIFTY alert bot active ✅"})

@app.route("/run", methods=["GET", "POST"])
def run_alert():
    if not is_market_open():
        return jsonify({"msg": "market closed", "ok": False})

    try:
        data = get_option_chain()
        records = data["records"]["data"]
        ce_data = []
        pe_data = []

        for item in records:
            strike = item["strikePrice"]
            ce = item.get("CE")
            pe = item.get("PE")
            if ce:
                ce_data.append({
                    "strike": strike,
                    "oi": ce["openInterest"],
                    "coi": ce["changeinOpenInterest"],
                    "vol": ce["totalTradedVolume"],
                    "cvol%": calculate_percentage(ce["totalTradedVolume"], ce["openInterest"])
                })
            if pe:
                pe_data.append({
                    "strike": strike,
                    "oi": pe["openInterest"],
                    "coi": pe["changeinOpenInterest"],
                    "vol": pe["totalTradedVolume"],
                    "cvol%": calculate_percentage(pe["totalTradedVolume"], pe["openInterest"])
                })

        # 🔹 ATM detection
        spot_price = data["records"]["underlyingValue"]
        atm_strike = round(spot_price / 50) * 50

        ce_filtered = [x for x in ce_data if atm_strike - 100 <= x["strike"] <= atm_strike + 200]
        pe_filtered = [x for x in pe_data if atm_strike - 200 <= x["strike"] <= atm_strike + 100]

        # 🔹 Telegram message format (table-style)
        msg = f"📊 *NIFTY Option Data*  \nSpot: `{spot_price}`  \n\n" \
              f"⚡ *CALLS (ATM {atm_strike})*\n" \
              f"`Strike | OI | COI | VOL%`\n" + \
              "\n".join([f"{x['strike']} | {x['oi']} | {x['coi']} | {x['cvol%']}%" for x in ce_filtered]) + \
              "\n\n⚡ *PUTS (ATM {atm_strike})*\n" \
              f"`Strike | OI | COI | VOL%`\n" + \
              "\n".join([f"{x['strike']} | {x['oi']} | {x['coi']} | {x['cvol%']}%" for x in pe_filtered])

        telegram_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": CHAT_ID,
            "text": msg,
            "parse_mode": "Markdown"
        }
        requests.post(telegram_url, data=payload)

        save_cache(data)
        return jsonify({"msg": "Data sent to Telegram ✅", "ok": True})

    except Exception as e:
        return jsonify({"error": str(e), "ok": False})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)