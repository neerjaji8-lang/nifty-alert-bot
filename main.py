from flask import Flask, jsonify
import requests
import datetime
import os
import json

app = Flask(__name__)

# 🔹 Telegram setup
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# 🔹 NSE headers
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

# 🔹 Session for NSE
session = requests.Session()
session.headers.update(HEADERS)

OPTION_URL = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
FUTURE_URL = "https://www.nseindia.com/api/liveEquity-derivatives?index=nifty"


def is_market_open():
    now = datetime.datetime.now()
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=15, second=0)
    market_close = now.replace(hour=15, minute=30, second=0)
    return market_open <= now <= market_close


def get_data(url):
    res = session.get(url, timeout=10)
    return res.json()


def percent(part, total):
    return round((part / total) * 100, 2) if total != 0 else 0


@app.route("/")
def home():
    return jsonify({"ok": True, "msg": "Nifty Alert Bot Active ✅"})


@app.route("/run", methods=["GET", "POST"])
def run_alert():
    if not is_market_open():
        return jsonify({"msg": "market closed", "ok": False})

    try:
        # ---------------- OPTION DATA ----------------
        data = get_data(OPTION_URL)
        spot = round(data["records"]["underlyingValue"])
        expiry = data["records"]["expiryDates"][0]
        records = data["records"]["data"]

        atm_strike = round(spot / 50) * 50
        ce_data, pe_data = [], []

        for item in records:
            strike = item["strikePrice"]
            ce = item.get("CE")
            pe = item.get("PE")

            if ce:
                ce_data.append({
                    "strike": strike,
                    "oi": ce["openInterest"],
                    "coi": ce["changeinOpenInterest"],
                    "iv": ce["impliedVolatility"],
                    "iv_chg": ce["impliedVolatility"] - ce.get("lastPrice", 0),
                    "vol": ce["totalTradedVolume"],
                    "vol%": percent(ce["totalTradedVolume"], ce["openInterest"])
                })

            if pe:
                pe_data.append({
                    "strike": strike,
                    "oi": pe["openInterest"],
                    "coi": pe["changeinOpenInterest"],
                    "iv": pe["impliedVolatility"],
                    "iv_chg": pe["impliedVolatility"] - pe.get("lastPrice", 0),
                    "vol": pe["totalTradedVolume"],
                    "vol%": percent(pe["totalTradedVolume"], pe["openInterest"])
                })

        # Sort by strike
        ce_data.sort(key=lambda x: x["strike"])
        pe_data.sort(key=lambda x: x["strike"])

        # Filter 1 ITM + 1 ATM + 4 OTM
        ce_filtered = [x for x in ce_data if atm_strike - 50 <= x["strike"] <= atm_strike + 200][:6]
        pe_filtered = [x for x in pe_data if atm_strike - 200 <= x["strike"] <= atm_strike + 50][-6:]

        # ---------------- FUTURE DATA ----------------
        future = get_data(FUTURE_URL)
        buy = sum([f["buyQuantity"] for f in future.get("data", []) if "buyQuantity" in f])
        sell = sum([f["sellQuantity"] for f in future.get("data", []) if "sellQuantity" in f])
        bias = "🟢 Bullish" if buy > sell else "🔴 Bearish"

        # ---------------- TELEGRAM MESSAGE ----------------
        msg = f"⚡ *NIFTY Option Chain* ⚡\n" \
              f"📅 `{datetime.datetime.now().strftime('%d-%b-%Y %H:%M:%S')}`\n" \
              f"📈 Spot: *{spot}* | Exp: `{expiry}`\n\n" \
              f"🟩 *CALL SIDE*\n" \
              f"`Strike | ΔOI | IV | ΔIV | VOL%`\n" + \
              "\n".join([f"{x['strike']} | {x['coi']} | {x['iv']} | {x['iv_chg']} | {x['vol%']}%" for x in ce_filtered]) + \
              "\n\n🟥 *PUT SIDE*\n" \
              f"`Strike | ΔOI | IV | ΔIV | VOL%`\n" + \
              "\n".join([f"{x['strike']} | {x['coi']} | {x['iv']} | {x['iv_chg']} | {x['vol%']}%" for x in pe_filtered]) + \
              f"\n\n💹 *Futures Data*\nBuy: `{buy:,}` | Sell: `{sell:,}` | Bias: {bias}"

        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={
            "chat_id": CHAT_ID,
            "text": msg,
            "parse_mode": "Markdown"
        })

        return jsonify({"msg": "Alert sent ✅", "ok": True})

    except Exception as e:
        return jsonify({"error": str(e), "ok": False})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)