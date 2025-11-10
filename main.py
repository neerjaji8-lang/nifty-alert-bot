#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from flask import Flask, jsonify, request
import requests, os, json
from datetime import datetime
import pytz

app = Flask(__name__)

# ========== ENV ==========
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
SYMBOL             = os.getenv("SYMBOL", "NIFTY")
STRIKE_STEP        = int(os.getenv("STRIKE_STEP", "50"))

OC_URL    = f"https://www.nseindia.com/api/option-chain-indices?symbol={SYMBOL}"
DERIV_URL = f"https://www.nseindia.com/api/quote-derivative?symbol={SYMBOL}"

CACHE_PATH = "/tmp/oc_cache.json"
IST = pytz.timezone("Asia/Kolkata")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.nseindia.com/",
    "Origin":  "https://www.nseindia.com",
    "Connection": "keep-alive",
}
session = requests.Session()
session.headers.update(HEADERS)

# ========== TIME ==========
def is_market_open():
    now = datetime.now(IST)
    if now.weekday() >= 5:  # Sat/Sun
        return False
    start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    end   = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return start <= now <= end

# ========== IO ==========
def load_cache():
    if not os.path.exists(CACHE_PATH):
        return None
    try:
        with open(CACHE_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return None

def save_cache(obj):
    try:
        with open(CACHE_PATH, "w") as f:
            json.dump(obj, f)
    except Exception:
        pass

# ========== HTTP ==========
def warmup():
    try:
        session.get("https://www.nseindia.com", timeout=10)
        session.get(f"https://www.nseindia.com/option-chain?symbol={SYMBOL}", timeout=10)
    except Exception:
        pass

def get_json(url, timeout=20):
    try:
        r = session.get(url, timeout=timeout)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None

def deep_find(obj, *contains):
    if isinstance(obj, dict):
        for k, v in obj.items():
            lk = k.lower()
            if all(c in lk for c in contains) and isinstance(v, (int, float)):
                return float(v)
        for v in obj.values():
            got = deep_find(v, *contains)
            if got is not None:
                return got
    elif isinstance(obj, list):
        for it in obj:
            got = deep_find(it, *contains)
            if got is not None:
                return got
    return None

# ========== MATH ==========
def rstep(x): return int(round(x / STRIKE_STEP) * STRIKE_STEP)

def pick_strikes(spot, side):
    atm = rstep(spot)
    if side == "CE":
        return [atm - STRIKE_STEP, atm] + [atm + i*STRIKE_STEP for i in range(1,5)]
    else:
        return [atm + STRIKE_STEP, atm] + [atm - i*STRIKE_STEP for i in range(1,5)]

def percent(part, total):
    try:
        if total and float(total) != 0:
            return round((float(part)/float(total))*100.0, 2)
    except Exception:
        pass
    return 0.0

# ========== NSE PARSE ==========
def fetch_option_chain():
    warmup()
    js = get_json(OC_URL)
    if not js:
        return None
    try:
        expiry = js["records"]["expiryDates"][0]  # nearest expiry
        spot   = float(js["records"]["underlyingValue"])
        rows   = [d for d in js["records"]["data"] if d.get("expiryDate") == expiry]
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
            return oi, iv, vol
    return 0.0, 0.0, 0.0

def fetch_futures_extras():
    """
    Returns: fut_oi, fut_vol, buy_qty, sell_qty, fut_price
    """
    warmup()
    js = get_json(DERIV_URL)
    if not js:
        return 0, 0, 0, 0, 0.0
    fut_oi    = deep_find(js, "open", "interest") or 0.0
    fut_vol   = deep_find(js, "volume") or 0.0
    buy_q     = deep_find(js, "totalbuyquantity") or 0.0
    sell_q    = deep_find(js, "totalsellquantity") or 0.0
    fut_price = deep_find(js, "lastprice") or 0.0
    return int(fut_oi), int(fut_vol), int(buy_q), int(sell_q), float(fut_price)

# ========== RENDER ==========
def render_rows(side_rows):
    lines = []
    lines.append(f"{'Strike':>7}  {'ΔOI':>9}  {'IV':>6}  {'ΔIV':>7}  {'CVol':>8}  {'CVol%':>7}")
    lines.append("-"*54)
    for r in side_rows:
        doi = "—" if r["doi"] is None else f"{int(r['doi']):+d}"
        div = "—" if r["div"] is None else f"{r['div']:+.2f}"
        cvo = "—" if r["cvol"] is None else f"{int(r['cvol']):,}"
        cvp = "—" if r["cvolp"] is None else f"{r['cvolp']:.2f}"
        lines.append(
            f"{r['strike']:>7}  {doi:>9}  {r['iv']:>6.2f}  {div:>7}  {cvo:>8}  {cvp:>7}"
        )
    return "<pre>" + "\n".join(lines) + "</pre>"

def build_message(expiry, spot, ce_rows, ce_tot, pe_rows, pe_tot,
                  fdoi, fvol, buy, sell, prem, dprem, build_tag):
    now = datetime.now(IST).strftime("%d-%b-%Y %H:%M:%S")
    bias_num  = buy - sell
    bias_side = "🟢 Bullish" if bias_num > 0 else ("🔴 Bearish" if bias_num < 0 else "⚪ Neutral")
    dprem_txt = "—" if dprem is None else f"{dprem:+.2f}"
    fdoi_txt  = "—" if fdoi  is None else f"{int(fdoi):+d}"
    fvol_txt  = "—" if fvol  is None else f"{int(fvol):+d}"

    msg = []
    msg.append(f"<b>📊 {SYMBOL} Option Chain</b>")
    msg.append(f"🕒 {now} IST  |  📅 Exp: <b>{expiry}</b>")
    msg.append(f"Spot: <b>{spot:.2f}</b>")
    msg.append("")
    msg.append("🟩 <b>CALL SIDE</b>")
    msg.append(render_rows(ce_rows))
    msg.append(f"<i>Total ➜ ΣΔOI:</i> <b>{ce_tot['sum_doi']:+,}</b>  |  "
               f"<i>Avg IV:</i> <b>{ce_tot['avg_iv']:.2f}</b>  |  "
               f"<i>Avg ΔIV:</i> <b>{ce_tot['avg_div']:+.2f}</b>  |  "
               f"<i>ΣCVol:</i> <b>{ce_tot['sum_cvol']:,}</b>  |  "
               f"<i>Avg CVol%:</i> <b>{ce_tot['avg_cvolp']:.2f}</b>")
    msg.append("")
    msg.append("🟥 <b>PUT SIDE</b>")
    msg.append(render_rows(pe_rows))
    msg.append(f"<i>Total ➜ ΣΔOI:</i> <b>{pe_tot['sum_doi']:+,}</b>  |  "
               f"<i>Avg IV:</i> <b>{pe_tot['avg_iv']:.2f}</b>  |  "
               f"<i>Avg ΔIV:</i> <b>{pe_tot['avg_div']:+.2f}</b>  |  "
               f"<i>ΣCVol:</i> <b>{pe_tot['sum_cvol']:,}</b>  |  "
               f"<i>Avg CVol%:</i> <b>{pe_tot['avg_cvolp']:.2f}</b>")
    msg.append("")
    msg.append(f"⚙️ <b>Futures Δ</b>  ΔOI: <b>{fdoi_txt}</b>  |  ΔVOL: <b>{fvol_txt}</b>")
    msg.append(f"💹 <b>Depth</b>  Buy: <b>{buy:,}</b>  |  Sell: <b>{sell:,}</b>  |  Bias: <b>{bias_side}</b> ({abs(bias_num):,})")
    msg.append(f"📐 <b>Premium</b>  Fut−Spot: <b>{prem:+.2f}</b>  (Δ {dprem_txt})  → <b>{build_tag}</b>")
    return "\n".join(msg)

def send_telegram(text_html):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return {"ok": False, "error": "telegram env missing"}
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        res = session.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text_html,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }, timeout=20)
        return res.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ========== SIDE COMPUTE ==========
def compute_side(rows, prev_map, strikes, side):
    out = []
    sum_doi=sum_cvol=0
    sum_iv=sum_div=sum_cvolp=0.0
    cnt=0
    # Also collect snapshot for cache
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
            doi = div = cvol = None
        cvolp = None if (cvol is None or oi == 0) else percent(cvol, oi)

        out.append({"strike": s, "doi": doi, "iv": iv, "div": div, "cvol": cvol, "cvolp": cvolp})
        snap[key] = {"oi": oi, "iv": iv, "vol": vol}

        if doi is not None:   sum_doi += int(doi)
        if cvol is not None:  sum_cvol += int(cvol)
        if iv is not None:    sum_iv += iv
        if div is not None:   sum_div += div
        if cvolp is not None: sum_cvolp += cvolp
        cnt += 1

    totals = {
        "sum_doi": sum_doi,
        "sum_cvol": sum_cvol,
        "avg_iv": (sum_iv / cnt) if cnt else 0.0,
        "avg_div": (sum_div / cnt) if cnt else 0.0,
        "avg_cvolp": (sum_cvolp / cnt) if cnt else 0.0
    }
    return out, totals, snap

# ========== BUILD-UP TAG ==========
def build_up_tag(fdoi, dprem):
    # Use sign of Futures ΔOI and ΔPremium
    if fdoi is None or dprem is None:
        return "Neutral"
    if fdoi > 0 and dprem > 0:   return "Long Build-up ✅"
    if fdoi > 0 and dprem < 0:   return "Short Build-up 🔻"
    if fdoi < 0 and dprem < 0:   return "Long Unwinding ⬇️"
    if fdoi < 0 and dprem > 0:   return "Short Covering ⬆️"
    return "Neutral"

# ========== MAIN RUN ==========
def run_once():
    if not is_market_open() and request.args.get("test","0") != "1":
        return {"ok": False, "msg": "market closed"}

    oc = fetch_option_chain()
    if not oc:
        return {"ok": False, "msg": "nse option api blocked/failed"}

    expiry, spot, rows = oc["expiry"], oc["spot"], oc["rows"]

    cache = load_cache() or {}
    prev_legs = cache.get("legs", {})
    prev_premium = cache.get("premium")
    prev_fut = cache.get("futures", {})

    ce_strikes = pick_strikes(spot, "CE")
    pe_strikes = pick_strikes(spot, "PE")

    ce_rows, ce_tot, snap_ce = compute_side(rows, prev_legs, ce_strikes, "CE")
    pe_rows, pe_tot, snap_pe = compute_side(rows, prev_legs, pe_strikes, "PE")

    fut_oi, fut_vol, buy, sell, fut_price = fetch_futures_extras()
    premium = (fut_price - spot) if fut_price else None

    # Futures deltas
    if prev_fut:
        fdoi = fut_oi - float(prev_fut.get("oi", 0))
        fvol = fut_vol - float(prev_fut.get("vol", 0))
    else:
        fdoi = fvol = None

    # Premium delta
    dprem = None
    if premium is not None and (prev_premium is not None):
        dprem = round(premium - float(prev_premium), 2)

    # Save cache
    new_legs = {**snap_ce, **snap_pe}
    new_cache = {
        "ts": datetime.now(IST).isoformat(),
        "expiry": expiry,
        "spot": spot,
        "legs": new_legs,
        "futures": {"oi": fut_oi, "vol": fut_vol, "price": fut_price},
        "premium": premium
    }
    save_cache(new_cache)

    # First run → only prime cache (avoid junk deltas)
    if not prev_legs and request.args.get("test","0") != "1":
        return {"ok": True, "primed": True, "note": "base snapshot stored"}

    build_tag = build_up_tag(fdoi, dprem)

    msg = build_message(
        expiry, spot, ce_rows, ce_tot, pe_rows, pe_tot,
        fdoi, fvol, buy, sell,
        prem=0.0 if premium is None else premium,
        dprem=dprem,
        build_tag=build_tag
    )
    tg = send_telegram(msg)
    return {"ok": True, "sent": tg}

# ========== ROUTES ==========
@app.route("/")
def health():
    return "NIFTY Alert Bot Active ✅", 200

@app.route("/run", methods=["GET", "POST"])
def run_endpoint():
    return jsonify(run_once())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))