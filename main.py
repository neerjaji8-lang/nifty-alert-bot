#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, json, math, requests
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify

# ---------- CONFIG ----------
SYMBOL = os.getenv("SYMBOL", "NIFTY")
STRIKE_STEP = int(os.getenv("STRIKE_STEP", "50"))
TZ = timezone(timedelta(hours=5, minutes=30))  # IST
CACHE_FILE = "/tmp/last_data.json"             # Cloud Run writable path

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://www.nseindia.com/",
}

OC_URL = f"https://www.nseindia.com/api/option-chain-indices?symbol={SYMBOL}"
DERIV_URL = f"https://www.nseindia.com/api/quote-derivative?symbol={SYMBOL}"

app = Flask(__name__)
session = requests.Session()

# ---------- utils ----------
def now_ist():
    return datetime.now(TZ)

def market_open_now() -> bool:
    n = now_ist()
    if n.weekday() > 4:  # Sat/Sun
        return False
    start = n.replace(hour=9,  minute=15, second=0, microsecond=0)
    end   = n.replace(hour=15, minute=30, second=0, microsecond=0)
    return start <= n <= end

def warmup():
    try:
        session.get("https://www.nseindia.com", headers=HEADERS, timeout=10)
        session.get(f"https://www.nseindia.com/option-chain?symbol={SYMBOL}", headers=HEADERS, timeout=10)
    except Exception:
        pass

def round_to_step(x: float) -> int:
    return int(round(x / STRIKE_STEP) * STRIKE_STEP)

def pick_strikes_for_side(spot: float, side: str):
    """
    side: 'CE' or 'PE'
    Return 1 ITM, 1 ATM, 4 OTM (total 6)
    - CALL ITM: strike < spot ; OTM: > spot
    - PUT  ITM: strike > spot ; OTM: < spot
    """
    atm = round_to_step(spot)
    if side == "CE":
        itm = atm - STRIKE_STEP
        otms = [atm + i*STRIKE_STEP for i in range(1, 5)]
    else:  # PE
        itm = atm + STRIKE_STEP
        otms = [atm - i*STRIKE_STEP for i in range(1, 5)]
    return [itm, atm] + otms

def sign_dot(v):
    if v is None:
        return "⚪"
    return "🟢" if v > 0 else ("🔴" if v < 0 else "⚪")

def fmt_int(v):  return "—" if v is None else f"{int(v):,}"
def fmt_f(v, dp=2): return "—" if v is None else f"{v:.{dp}f}"

def load_cache():
    try:
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_cache(data: dict):
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass

def deep_find_oi_vol(obj):
    """Recursively find first (openInterest, ...volume...) pair in derivative JSON."""
    if isinstance(obj, dict):
        oi = None; vol = None
        for k, v in obj.items():
            lk = k.lower()
            if isinstance(v, (int, float)):
                if "open" in lk and "interest" in lk: oi = v
                if "volume" in lk: vol = v
        if oi is not None and vol is not None:
            return float(oi), float(vol)
        for v in obj.values():
            r = deep_find_oi_vol(v)
            if r: return r
    elif isinstance(obj, list):
        for it in obj:
            r = deep_find_oi_vol(it)
            if r: return r
    return None

# ---------- NSE fetch ----------
def get_option_chain():
    warmup()
    r = session.get(OC_URL, headers=HEADERS, timeout=20)
    r.raise_for_status()
    js = r.json()
    expiry = js["records"]["expiryDates"][0]
    spot = float(js["records"]["underlyingValue"])
    data = [d for d in js["records"]["data"] if d["expiryDate"] == expiry]
    return expiry, spot, data

def lookup_leg(data, strike, side):
    for d in data:
        if int(d.get("strikePrice", -1)) == int(strike) and side in d:
            leg = d[side]
            return (
                float(leg.get("openInterest", 0) or 0.0),
                float(leg.get("impliedVolatility", 0) or 0.0),
                float(leg.get("totalTradedVolume", 0) or 0.0)
            )
    return 0.0, 0.0, 0.0

def fetch_futures_oi_vol():
    try:
        r = session.get(DERIV_URL, headers=HEADERS, timeout=15)
        r.raise_for_status()
        js = r.json()
        res = deep_find_oi_vol(js)
        if not res: return 0.0, 0.0
        return res
    except Exception:
        return 0.0, 0.0

# ---------- rows & message ----------
def per_delta(prev, cur):
    if prev is None:
        return None
    return cur - prev

def build_rows(side, spot, data, cache):
    strikes = pick_strikes_for_side(spot, side)
    rows = []
    sum_iv = 0.0; cnt = 0
    tot_doi = 0.0; tot_dvol = 0.0
    new_snap = {}

    for s in strikes:
        oi, iv, vol = lookup_leg(data, s, side)
        key = f"{side}:{s}"
        prev = cache.get(key)  # {'oi','iv','vol'}

        d_oi  = per_delta(prev.get("oi"),  oi)  if prev else None
        d_iv  = per_delta(prev.get("iv"),  iv)  if prev else None
        d_vol = per_delta(prev.get("vol"), vol) if prev else None

        rows.append({"strike": s, "doi": d_oi, "iv": iv, "div": d_iv, "dvol": d_vol})
        sum_iv += iv; cnt += 1
        if d_oi  is not None:  tot_doi  += d_oi
        if d_vol is not None:  tot_dvol += d_vol

        new_snap[key] = {"oi": oi, "iv": iv, "vol": vol}

    avg_iv = (sum_iv / max(1, cnt))
    avg_div = None
    # compute avg ΔIV only on rows where we had delta
    div_sum = sum(r["div"] for r in rows if r["div"] is not None)
    div_cnt = sum(1 for r in rows if r["div"] is not None)
    if div_cnt:
        avg_div = div_sum / div_cnt

    return rows, (tot_doi if div_cnt else None), avg_iv, avg_div, (tot_dvol if div_cnt else None), new_snap

def render_rows(rows):
    out = []
    out.append(f"{'Strike':>7}  {'ΔOI':>8}  {'IV':>6}  {'ΔIV':>7}  {'ΔVOL':>8}")
    out.append("-"*44)
    for r in rows:
        out.append(
            f"{r['strike']:>7}  "
            f"{sign_dot(r['doi'])} {fmt_int(0 if r['doi'] is None else r['doi']):>6}  "
            f"{fmt_f(r['iv']):>6}  "
            f"{sign_dot(r['div'])} {fmt_f(0 if r['div'] is None else r['div']):>5}  "
            f"{sign_dot(r['dvol'])} {fmt_int(0 if r['dvol'] is None else r['dvol']):>6}"
        )
    return "\n".join(out)

def build_message(expiry, spot, ce_rows, ce_stats, pe_rows, pe_stats, fut_delta):
    t = now_ist().strftime("%d-%b %H:%M:%S")
    ce_tot_doi, ce_avg_iv, ce_avg_div, ce_tot_dvol = ce_stats
    pe_tot_doi, pe_avg_iv, pe_avg_div, pe_tot_dvol = pe_stats
    f_doi, f_vol = fut_delta

    msg = []
    msg.append("<b>📊 NIFTY50 Option Chain</b>")
    msg.append(f"<b>🕒</b> {t} IST   <b>📅 Exp:</b> {expiry}")
    msg.append(f"<b>Spot:</b> {spot:.2f}\n")

    msg.append("<b>CALL</b>")
    msg.append("<pre>" + render_rows(ce_rows) + "</pre>")
    msg.append(
        f"Total ΔOI: <b>{fmt_int(ce_tot_doi)}</b>  |  "
        f"Avg IV: <b>{ce_avg_iv:.2f}</b>  |  "
        f"Avg ΔIV: <b>{fmt_f(ce_avg_div)}</b>  |  "
        f"Total ΔVOL: <b>{fmt_int(ce_tot_dvol)}</b>\n"
    )

    msg.append("<b>PUT</b>")
    msg.append("<pre>" + render_rows(pe_rows) + "</pre>")
    msg.append(
        f"Total ΔOI: <b>{fmt_int(pe_tot_doi)}</b>  |  "
        f"Avg IV: <b>{pe_avg_iv:.2f}</b>  |  "
        f"Avg ΔIV: <b>{fmt_f(pe_avg_div)}</b>  |  "
        f"Total ΔVOL: <b>{fmt_int(pe_tot_dvol)}</b>\n"
    )

    if f_doi is not None or f_vol is not None:
        msg.append(f"<b>Futures Δ</b>  ΔOI: <b>{fmt_int(f_doi)}</b> | ΔVOL: <b>{fmt_int(f_vol)}</b>")

    return "\n".join(msg)

def send_telegram(text):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return {"ok": False, "error": "telegram env missing"}
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = session.post(url, json={"chat_id": chat, "text": text, "parse_mode": "HTML"}, timeout=20)
        return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ---------- main work ----------
def run_once():
    if not market_open_now():
        return {"ok": False, "msg": "market closed"}

    try:
        expiry, spot, data = get_option_chain()

        cache = load_cache()

        ce_rows, ce_doi, ce_avg_iv, ce_avg_div, ce_dvol, ce_snap = build_rows("CE", spot, data, cache)
        pe_rows, pe_doi, pe_avg_iv, pe_avg_div, pe_dvol, pe_snap = build_rows("PE", spot, data, cache)

        # Futures snapshot + delta
        cur_foi, cur_fvol = fetch_futures_oi_vol()
        prev_f = cache.get("FUT")  # {'oi','vol'}
        f_doi  = None if not prev_f else cur_foi - prev_f.get("oi", 0.0)
        f_dvol = None if not prev_f else cur_fvol - prev_f.get("vol", 0.0)

        # Save new cache
        cache.update(ce_snap)
        cache.update(pe_snap)
        cache["FUT"] = {"oi": cur_foi, "vol": cur_fvol}
        save_cache(cache)

        msg = build_message(
            expiry, spot,
            ce_rows, (ce_doi, ce_avg_iv, ce_avg_div, ce_dvol),
            pe_rows, (pe_doi, pe_avg_iv, pe_avg_div, pe_dvol),
            (f_doi, f_dvol)
        )
        tg = send_telegram(msg)
        return {"ok": True, "sent": tg}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ---------- Flask ----------
@app.route("/")
def health():
    return "NIFTY Alert Bot Active ✅", 200

@app.route("/run", methods=["GET", "POST"])
def run():
    return jsonify(run_once())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))