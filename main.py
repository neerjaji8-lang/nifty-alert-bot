from flask import Flask, jsonify
import requests
import datetime
import pytz
import os

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

# -------------------- TIME FIX --------------------
def is_market_open():
    tz = pytz.timezone("Asia/Kolkata")
    now = datetime.datetime.now(tz)
    if now.weekday() >= 5:  # Saturday, Sunday
        return False
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_open <= now <= market_close

# -------------------- HELPERS --------------------
def get_data(url):
    res = session.get(url, timeout=10)
    return res.json()

def percent(part, total):
    return round((part / total) * 100, 2) if total != 0 else 0

# -------------------- MAIN APP --------------------
@app.route("/")
def home():
    return jsonify({"ok": True, "msg": "Nifty Alert Bot Active ✅"})

@app.route("/run", methods=["GET", "POST"])
def run_alert():
    if not is_market_open():
        return jsonify({"msg": "market closed", "ok": False})

    try:
        # ✅ Option Chain
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
                    "vol": ce["totalTradedVolume"],
                    "vol%": percent(ce["totalTradedVolume"], ce["openInterest"])
                })
            if pe:
                pe_data.append({
                    "strike": strike,
                    "oi": pe["openInterest"],
                    "coi": pe["changeinOpenInterest"],
                    "iv": pe["impliedVolatility"],
                    "vol": pe["totalTradedVolume"],
                    "vol%": percent(pe["totalTradedVolume"], pe["openInterest"])
                })

        # Sort strikes
        ce_data.sort(key=lambda x: x["strike"])
        pe_data.sort(key=lambda x: x["strike"])

        # Select 1 ITM + 1 ATM + 4 OTM
        ce_filtered = [x for x in ce_data if atm_strike - 50 <= x["strike"] <= atm_strike + 200][:6]
        pe_filtered = [x for x in pe_data if atm_strike - 200 <= x["strike"] <= atm_strike + 50][-6:]

        # ✅ Futures data
        future = get_data(FUTURE_URL)
        buy = sum([f.get("buyQuantity", 0) for f in future.get("data", [])])
        sell = sum([f.get("sellQuantity", 0) for f in future.get("data", [])])
        bias = "🟢 Bullish" if buy > sell else "🔴 Bearish"

        # ✅ Telegram message format
        msg = f"📊 *NIFTY Option Chain*\n" \
              f"🕒 {datetime.datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%d-%b-%Y %H:%M:%S')} IST\n" \
              f"📈 Spot: *{spot}* | Exp: `{expiry}`\n\n" \
              f"🟩 *CALL SIDE*\n" \
              f"`Strike | ΔOI | IV | VOL%`\n" + \
              "\n".join([f"{x['strike']} | {x['coi']} | {x['iv']} | {x['vol%']}%" for x in ce_filtered]) + \
              "\n\n🟥 *PUT SIDE*\n" \
              f"`Strike | ΔOI | IV | VOL%`\n" + \
              "\n".join([f"{x['strike']} | {x['coi']} | {x['iv']} | {x['vol%']}%" for x in pe_filtered]) + \
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