#!/usr/bin/env python3
import os, requests
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify
app = Flask(__name__)
SYMBOL = "NIFTY"
OC_URL = f"https://www.nseindia.com/api/option-chain-indices?symbol={SYMBOL}"
HEADERS = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64)","Referer":"https://www.nseindia.com/"}
TZ = timezone(timedelta(hours=5, minutes=30))
def market_open():
    now = datetime.now(TZ)
    if now.weekday() > 4: return False
    start = now.replace(hour=9, minute=19, second=0, microsecond=0)
    end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return start <= now <= end
def send_telegram(text, parse_mode="HTML"):
    t = os.getenv("TELEGRAM_BOT_TOKEN"); c = os.getenv("TELEGRAM_CHAT_ID")
    if not t or not c: return {"ok": False, "error": "telegram env missing"}
    u = f"https://api.telegram.org/bot{t}/sendMessage"
    return requests.post(u, json={"chat_id": c, "text": text, "parse_mode": parse_mode}, timeout=20).json()
def fetch_data():
    s = requests.Session(); s.get("https://www.nseindia.com", headers=HEADERS, timeout=10)
    js = s.get(OC_URL, headers=HEADERS, timeout=15).json()
    expiry = js["records"]["expiryDates"][0]; underlying = js["records"]["underlyingValue"]; data = js["records"]["data"]
    atm = round(underlying / 50) * 50
    ce = next((d["CE"] for d in data if d.get("strikePrice")==atm and "CE" in d), None)
    pe = next((d["PE"] for d in data if d.get("strikePrice")==atm and "PE" in d), None)
    ce_oi = ce.get("openInterest",0) if ce else 0; pe_oi = pe.get("openInterest",0) if pe else 0
    pcr = round(pe_oi / ce_oi, 2) if ce_oi else 0
    return expiry, underlying, atm, ce_oi, pe_oi, pcr
def run_once():
    if not market_open(): return {"ok": False, "msg": "Market Closed"}
    try:
        expiry, underlying, atm, ce_oi, pe_oi, pcr = fetch_data()
        now = datetime.now(TZ).strftime("%d-%b %H:%M:%S")
        msg = (f"<b>📊 NIFTY Option Chain Update</b>\n"
               f"<b>🕒 Time:</b> {now} IST\n"
               f"<b>📅 Expiry:</b> {expiry}\n\n"
               f"<pre>{'Strike':<10}{'CE OI':>10}{'PE OI':>10}\n{'-'*30}\n"
               f"{atm:<10}{ce_oi:>10,}{pe_oi:>10,}</pre>\n"
               f"<b>📈 PCR:</b> {pcr}\n<b>Underlying:</b> {underlying:.2f}")
        tg = send_telegram(msg)
        return {"ok": True, "sent": tg}
    except Exception as e:
        return {"ok": False, "error": str(e)}
@app.route("/")
def home(): return "NIFTY Alert Bot Active ✅", 200
@app.route("/run", methods=["GET","POST"])
def run(): return jsonify(run_once())
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
