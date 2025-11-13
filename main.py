#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from flask import Flask, jsonify, request
import requests, os, json
from datetime import datetime
import pytz

app = Flask(__name__)

# ====== ENV ======
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
SYMBOL             = os.getenv("SYMBOL", "NIFTY")
STRIKE_STEP        = int(os.getenv("STRIKE_STEP", "50"))
CACHE_PATH         = "/tmp/oc_cache.json"

# ====== NSE URLs ======
OC_URL    = f"https://www.nseindia.com/api/option-chain-indices?symbol={SYMBOL}"
DERIV_URL = f"https://www.nseindia.com/api/quote-derivative?symbol={SYMBOL}"

# ====== HTTP session & headers ======
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.nseindia.com/",
    "Origin": "https://www.nseindia.com",
}
session = requests.Session()
session.headers.update(HEADERS)

IST = pytz.timezone("Asia/Kolkata")

# ====== Helpers ======
def now_ist():
    return datetime.now(IST)

def is_market_open():
    now = now_ist()
    # Mon-Fri and between 09:15 and 15:30 IST
    if now.weekday() >= 5:
        return False
    start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    end   = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return start <= now <= end

def warmup():
    try:
        session.get("https://www.nseindia.com", timeout=8)
        session.get(f"https://www.nseindia.com/option-chain?symbol={SYMBOL}", timeout=8)
    except:
        pass

def get_json(url, timeout=18):
    try:
        r = session.get(url, timeout=timeout)
        if r.status_code != 200:
            return None
        return r.json()
    except:
        return None

def deep_find(obj, *contains):
    if isinstance(obj, dict):
        for k, v in obj.items():
            lk = k.lower()
            if all(c in lk for c in contains) and isinstance(v, (int, float)):
                return v
        for v in obj.values():
            res = deep_find(v, *contains)
            if res is not None: return res
    elif isinstance(obj, list):
        for it in obj:
            res = deep_find(it, *contains)
            if res is not None: return res
    return None

def load_cache():
    if not os.path.exists(CACHE_PATH): return None
    try:
        with open(CACHE_PATH, "r") as f:
            return json.load(f)
    except:
        return None

def save_cache(obj):
    try:
        with open(CACHE_PATH, "w") as f:
            json.dump(obj, f)
    except:
        pass

def rstep(x): return int(round(x / STRIKE_STEP) * STRIKE_STEP)

def pick_strikes(spot, side):
    atm = rstep(spot)
    if side == "CE":
        return [atm - STRIKE_STEP, atm] + [atm + i*STRIKE_STEP for i in range(1,5)]
    else:
        return [atm + STRIKE_STEP, atm] + [atm - i*STRIKE_STEP for i in range(1,5)]

def percent(part, total):
    try:
        total = float(total)
        return round((float(part) / total) * 100.0, 2) if total != 0 else 0.0
    except:
        return 0.0

def sign_emoji(v):
    if v is None: return "⚪"
    try:
        v = float(v)
    except:
        return "⚪"
    if v > 0: return "🟢"
    if v < 0: return "🔴"
    return "⚪"

# ====== NSE parsers ======
def fetch_option_chain():
    warmup()
    js = get_json(OC_URL)
    if not js: return None
    try:
        expiry = js["records"]["expiryDates"][0]
        spot   = float(js["records"]["underlyingValue"])
        rows   = [d for d in js["records"]["data"] if d.get("expiryDate") == expiry]
        return {"expiry": expiry, "spot": spot, "rows": rows}
    except:
        return None

def lookup(rows, strike, side):
    s = int(strike)
    for d in rows:
        if int(d.get("strikePrice", -1)) == s and side in d:
            leg = d[side]
            oi  = float(leg.get("openInterest", 0) or 0)
            iv  = float(leg.get("impliedVolatility", 0) or 0)
            vol = float(leg.get("totalTradedVolume", 0) or 0)
            return oi, iv, vol
    return 0.0, 0.0, 0.0

def fetch_futures_extras():
    warmup()
    js = get_json(DERIV_URL)
    if not js: return 0, 0, 0, 0, 0.0
    fut_oi  = deep_find(js, "open", "interest") or 0
    fut_vol = deep_find(js, "volume") or 0
    buy_q   = deep_find(js, "totalbuyquantity") or 0
    sell_q  = deep_find(js, "totalsellquantity") or 0
    fut_px  = deep_find(js, "lastprice") or deep_find(js, "last") or 0.0
    return int(fut_oi), int(fut_vol), int(buy_q), int(sell_q), float(fut_px)

# ====== Build & formatting ======
def render_table_rows(side_rows):
    # fixed width columns for Telegram monospace inside <pre>
    header = f"{'Strike':>7}  {'ΔOI':>9}  {'IV':>6}  {'ΔIV':>7}  {'CVol%':>7}"
    sep = "-" * len(header)
    lines = [header, sep]
    for r in side_rows:
        doi = "—" if r["doi"] is None else f"{int(r['doi']):+7,d}"
        div = "—" if r["div"] is None else f"{r['div']:+6.2f}"
        cvp = "—" if r["cvolp"] is None else f"{r['cvolp']:+6.2f}"
        ivs = f"{r['iv']:6.2f}"
        emoji_doi = sign_emoji(r["doi"])
        emoji_div = sign_emoji(r["div"])
        emoji_cvp = sign_emoji(r["cvolp"])
        # put emoji immediately before value for quick color
        line = f"{r['strike']:>7}  {emoji_doi}{doi:>8}  {ivs}  {emoji_div}{div:>6}  {emoji_cvp}{cvp:>6}"
        lines.append(line)
    return "<pre>" + "\n".join(lines) + "</pre>"

def build_summary(tot):
    return (f"ΣΔOI: <b>{tot['sum_doi']:+,}</b>  |  "
            f"Avg IV: <b>{tot['avg_iv']:.2f}</b>  |  "
            f"Avg ΔIV: <b>{tot['avg_div']:+.2f}</b>  |  "
            f"Avg CVol%: <b>{tot['avg_cvolp']:.2f}</b>")

def send_telegram(html_text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return {"ok": False, "error": "telegram env missing"}
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        res = session.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": html_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }, timeout=20)
        return res.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ====== Compute per-side ======
def compute_side(rows, prev_map, strikes, side):
    result = []
    sum_doi = 0
    sum_cvol = 0
    sum_iv = 0.0
    sum_div = 0.0
    sum_cvolp = 0.0
    cnt = 0
    snap = {}
    for s in strikes:
        oi, iv, vol = lookup(rows, s, side)
        key = f"{side}:{s}"
        prev = prev_map.get(key) if prev_map else None

        if prev:
            doi = oi - float(prev.get("oi", 0))
            div = iv - float(prev.get("iv", 0))
            cvol = vol - float(prev.get("vol", 0))
        else:
            # first-run: still send message but deltas show 0 (or use NSE changeinOpenInterest as fallback if needed)
            doi = None
            div = None
            cvol = None

        cvolp = None if (cvol is None or oi == 0) else percent(cvol, oi)

        result.append({"strike": s, "doi": doi, "iv": iv, "div": div, "cvolp": cvolp})
        snap[key] = {"oi": oi, "iv": iv, "vol": vol}

        if doi is not None: sum_doi += int(doi)
        if cvol is not None: sum_cvol += int(cvol)
        if iv is not None: sum_iv += iv
        if div is not None: sum_div += (div if div is not None else 0)
        if cvolp is not None: sum_cvolp += cvolp
        cnt += 1

    totals = {
        "sum_doi": sum_doi,
        "sum_cvol": sum_cvol,
        "avg_iv": (sum_iv / cnt) if cnt else 0.0,
        "avg_div": (sum_div / cnt) if cnt else 0.0,
        "avg_cvolp": (sum_cvolp / cnt) if cnt else 0.0
    }
    return result, totals, snap

# ====== Build-up tag (premium + fut ΔOI) ======
def build_up_tag(fdoi, dprem):
    if fdoi is None or dprem is None:
        return "Neutral"
    if fdoi > 0 and dprem > 0: return "Long Build-up ✅"
    if fdoi > 0 and dprem < 0: return "Short Build-up 🔻"
    if fdoi < 0 and dprem < 0: return "Long Unwinding ⬇️"
    if fdoi < 0 and dprem > 0: return "Short Covering ⬆️"
    return "Neutral"

# ====== Main run logic ======
def run_once(bypass_test=False):
    # market guard (bypass with test param)
    if not is_market_open() and not bypass_test:
        return {"ok": False, "msg": "market closed"}

    oc = fetch_option_chain()
    if not oc:
        return {"ok": False, "msg": "nse option api blocked/failed"}
    expiry = oc["expiry"]; spot = oc["spot"]; rows = oc["rows"]

    cache = load_cache() or {}
    prev_legs = cache.get("legs", {})
    prev_fut = cache.get("futures", {})
    prev_premium = cache.get("premium")

    # compute strikes
    ce_strikes = pick_strikes(spot, "CE")
    pe_strikes = pick_strikes(spot, "PE")

    # compute per-side
    ce_rows, ce_tot, snap_ce = compute_side(rows, prev_legs, ce_strikes, "CE")
    pe_rows, pe_tot, snap_pe = compute_side(rows, prev_legs, pe_strikes, "PE")

    # futures
    fut_oi, fut_vol, buy_q, sell_q, fut_px = fetch_futures_extras()
    premium = None if fut_px == 0 else round(fut_px - spot, 2)

    # futures deltas against cache
    if prev_fut:
        fdoi = fut_oi - int(prev_fut.get("oi", 0))
        fvol = fut_vol - int(prev_fut.get("vol", 0))
    else:
        fdoi = None; fvol = None

    # premium delta
    dprem = None
    if premium is not None and prev_premium is not None:
        dprem = round(premium - float(prev_premium), 2)

    # compute gap change (fut-spot) delta (points)
    prev_gap = None
    if prev_fut and prev_fut.get("price") is not None and cache.get("spot") is not None:
        prev_gap = float(prev_fut.get("price")) - float(cache.get("spot"))
    curr_gap = None if fut_px == 0 else round(fut_px - spot, 2)
    gap_delta = None if (curr_gap is None or prev_gap is None) else round(curr_gap - prev_gap, 2)
    gap_tag = "Neutral"
    if gap_delta is not None:
        gap_tag = "🟢 Futures strengthening" if gap_delta > 0 else ("🔻 Futures weakening" if gap_delta < 0 else "⚪ No change")

    # save cache (always)
    new_cache = {
        "ts": now_ist().isoformat(),
        "expiry": expiry,
        "spot": spot,
        "legs": {**snap_ce, **snap_pe},
        "futures": {"oi": fut_oi, "vol": fut_vol, "price": fut_px},
        "premium": premium
    }
    save_cache(new_cache)

    # build-up tag
    build_tag = build_up_tag(fdoi, dprem)

    # build telegram message
    nowstr = now_ist().strftime("%d-%b-%Y %H:%M:%S")
    header = f"<b>📊 {SYMBOL} Option Chain</b>\n🕒 {nowstr} IST  |  📅 Exp: <b>{expiry}</b>\nSpot: <b>{spot:.2f}</b>\n"

    ce_block = "<b>🟩 CALL SIDE</b>\n" + render_table_rows(ce_rows)
    ce_summary = build_summary(ce_tot)

    pe_block = "<b>🟥 PUT SIDE</b>\n" + render_table_rows(pe_rows)
    pe_summary = build_summary(pe_tot)

    fut_block = (f"⚙️ <b>Futures Δ</b>  ΔOI: <b>{(fdoi if fdoi is not None else '—')}</b>  |  "
                 f"ΔVOL: <b>{(fvol if fvol is not None else '—')}</b>\n"
                 f"💹 <b>Depth</b> Buy: <b>{buy_q:,}</b>  |  Sell: <b>{sell_q:,}</b>\n"
                 f"Bias: <b>{'🟢 Bullish' if buy_q>sell_q else ('🔴 Bearish' if buy_q<sell_q else '⚪ Neutral')}</b>")

    premium_line = f"📐 <b>Premium</b> Fut−Spot: <b>{(premium if premium is not None else '—'):+.2f}</b>  (Δ {dprem if dprem is not None else '—'})  → <b>{build_tag}</b>"
    gap_line = f"📈 <b>Gap Δ</b> (Fut−Spot) change: <b>{(gap_delta if gap_delta is not None else '—')}</b> pts  → <b>{gap_tag}</b>"

    full_msg = "\n\n".join([header, ce_block, ce_summary, pe_block, pe_summary, fut_block, premium_line, gap_line])
    tg = send_telegram(full_msg)
    return {"ok": True, "sent": tg}

# ====== Flask routes ======
@app.route("/", methods=["GET"])
def health():
    return "NIFTY Alert Bot Active ✅", 200

@app.route("/run", methods=["GET", "POST"])
def run_endpoint():
    test = request.args.get("test","0") == "1"
    return jsonify(run_once(bypass_test=test))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))