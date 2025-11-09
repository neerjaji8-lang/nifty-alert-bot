from flask import Flask, jsonify
import requests, os, datetime, pytz

app = Flask(__name__)

# ====== CONFIG ======
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
SYMBOL             = os.getenv("SYMBOL", "NIFTY")
STRIKE_STEP        = int(os.getenv("STRIKE_STEP", "50"))

OC_URL     = f"https://www.nseindia.com/api/option-chain-indices?symbol={SYMBOL}"
DERIV_URL  = f"https://www.nseindia.com/api/quote-derivative?symbol={SYMBOL}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/121.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.nseindia.com/",
    "Origin":  "https://www.nseindia.com",
    "Connection": "keep-alive",
}

session = requests.Session()
session.headers.update(HEADERS)

# ====== TIME (IST) ======
def is_market_open():
    tz = pytz.timezone("Asia/Kolkata")
    now = datetime.datetime.now(tz)
    if now.weekday() >= 5:  # Sat/Sun
        return False
    open_t  = now.replace(hour=9,  minute=15, second=0, microsecond=0)
    close_t = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return open_t <= now <= close_t

# ====== UTIL ======
def warmup():
    """Set cookies so NSE returns JSON (not 403/HTML)."""
    try:
        session.get("https://www.nseindia.com", timeout=10)
        session.get(f"https://www.nseindia.com/option-chain?symbol={SYMBOL}", timeout=10)
    except Exception:
        pass

def get_json(url, timeout=20):
    """Robust JSON fetch: returns dict or None (never raises json error)."""
    try:
        r = session.get(url, timeout=timeout)
        if r.status_code != 200:
            return None
        try:
            return r.json()
        except Exception:
            return None
    except Exception:
        return None

def round_step(x): return int(round(x / STRIKE_STEP) * STRIKE_STEP)

def percent(part, total):
    if not total: return 0.0
    try:
        return round((float(part) / float(total)) * 100.0, 2)
    except Exception:
        return 0.0

def deep_find(obj, key_contains):
    """Find first numeric under nested dict/list for futures totals."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if key_contains in k.lower() and isinstance(v, (int, float)):
                return int(v)
        for v in obj.values():
            got = deep_find(v, key_contains)
            if got is not None: return got
    elif isinstance(obj, list):
        for it in obj:
            got = deep_find(it, key_contains)
            if got is not None: return got
    return None

def send_telegram(html_text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return {"ok": False, "error": "telegram env missing"}
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": html_text,
               "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        resp = session.post(url, json=payload, timeout=20)
        return resp.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ====== CORE ======
def fetch_option_chain():
    warmup()
    js = get_json(OC_URL)
    if not js: return None
    try:
        expiry  = js["records"]["expiryDates"][0]
        spot    = float(js["records"]["underlyingValue"])
        rows    = [d for d in js["records"]["data"] if d.get("expiryDate") == expiry]
        return {"expiry": expiry, "spot": spot, "rows": rows}
    except Exception:
        return None

def lookup(rows, strike, side):
    s = int(strike)
    for d in rows:
        if int(d.get("strikePrice", -1)) == s and side in d:
            leg = d[side]
            oi  = float(leg.get("openInterest", 0) or 0)
            iv  = float(leg.get("impliedVolatility", 0) or 0)
            vol = float(leg.get("totalTradedVolume", 0) or 0)
            coi = float(leg.get("changeinOpenInterest", 0) or 0)
            return oi, iv, vol, coi
    return 0.0, 0.0, 0.0, 0.0

def pick_strikes(spot, side):
    """1 ITM + 1 ATM + 4 OTM (auto-shift with spot)."""
    atm = round_step(spot)
    if side == "CE":
        strikes = [atm-STRIKE_STEP, atm] + [atm + i*STRIKE_STEP for i in range(1,5)]
    else:  # PE
        strikes = [atm+STRIKE_STEP, atm] + [atm - i*STRIKE_STEP for i in range(1,5)]
    return strikes

def fetch_futures():
    warmup()
    js = get_json(DERIV_URL)
    if not js: return 0, 0, 0, 0
    # Total pending orders (market depth)
    buy  = deep_find(js, "totalbuyquantity")  or 0
    sell = deep_find(js, "totalsellquantity") or 0
    # OI and traded volume (best effort)
    fut_oi  = deep_find(js, "openinterest") or 0
    fut_vol = deep_find(js, "volume")       or 0
    return int(fut_oi), int(fut_vol), int(buy), int(sell)

def build_table(rows):
    lines = []
    lines.append(f"{'Strike':>7}  {'ΔOI':>7}  {'IV':>6}  {'VOL%':>6}")
    lines.append("-"*34)
    for r in rows:
        strike, coi, iv, volpct = r
        lines.append(f"{strike:>7}  {int(coi):+7d}  {iv:>6.2f}  {volpct:>6.2f}")
    return "<pre>" + "\n".join(lines) + "</pre>"

def run_once():
    if not is_market_open():
        return {"ok": False, "msg": "market closed"}

    oc = fetch_option_chain()
    if not oc:
        return {"ok": False, "msg": "nse option api blocked/failed"}

    expiry, spot, rows = oc["expiry"], oc["spot"], oc["rows"]

    # CE side
    ce_strikes = pick_strikes(spot, "CE")
    ce_rows = []
    for s in ce_strikes:
        oi, iv, vol, coi = lookup(rows, s, "CE")
        volpct = percent(vol, oi)
        ce_rows.append((s, coi, iv, volpct))

    # PE side
    pe_strikes = pick_strikes(spot, "PE")
    pe_rows = []
    for s in pe_strikes:
        oi, iv, vol, coi = lookup(rows, s, "PE")
        volpct = percent(vol, oi)
        pe_rows.append((s, coi, iv, volpct))

    # Futures
    fut_oi, fut_vol, buy_q, sell_q = fetch_futures()
    bias_num  = buy_q - sell_q
    bias_side = "🟢 Bullish" if bias_num > 0 else ("🔴 Bearish" if bias_num < 0 else "⚪ Neutral")

    now_ist = datetime.datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%d-%b-%Y %H:%M:%S")
    msg = []
    msg.append(f"<b>📊 {SYMBOL} Option Chain</b>")
    msg.append(f"🕒 {now_ist} IST  |  📅 Exp: <b>{expiry}</b>")
    msg.append(f"Spot: <b>{spot:.2f}</b>")
    msg.append("")
    msg.append("🟩 <b>CALL SIDE</b>")
    msg.append(build_table(ce_rows))
    msg.append("")
    msg.append("🟥 <b>PUT SIDE</b>")
    msg.append(build_table(pe_rows))
    msg.append("")
    msg.append(f"⚙️ <b>Futures</b>  OI: <b>{fut_oi:,}</b>  |  VOL: <b>{fut_vol:,}</b>")
    msg.append(f"Buy: <b>{buy_q:,}</b>  |  Sell: <b>{sell_q:,}</b>  |  Bias: <b>{bias_side}</b> ({abs(bias_num):,})")

    tg = send_telegram("\n".join(msg))
    return {"ok": True, "sent": tg}

# ====== FLASK ROUTES ======
@app.route("/")
def health():
    return "NIFTY Alert Bot Active ✅", 200

@app.route("/run", methods=["GET", "POST"])
def run_endpoint():
    return jsonify(run_once())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))