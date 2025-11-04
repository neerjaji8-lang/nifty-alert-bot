#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, requests, math
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify

# ---------- SETTINGS ----------
SYMBOL = os.getenv("SYMBOL", "NIFTY")
STRIKE_STEP = int(os.getenv("STRIKE_STEP", "50"))
TZ = timezone(timedelta(hours=5, minutes=30))  # IST

USE_FS = os.getenv("USE_FIRESTORE", "0") == "1"
FS_PATH = os.getenv("FS_DOCPATH", "sensex_brain/oc_state")  # collection/doc path

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://www.nseindia.com/",
}

OC_URL = f"https://www.nseindia.com/api/option-chain-indices?symbol={SYMBOL}"
DERIV_URL = f"https://www.nseindia.com/api/quote-derivative?symbol={SYMBOL}"

# ---------- APP ----------
app = Flask(__name__)
session = requests.Session()
_prev_cache = {}      # fallback deltas if Firestore off
_prev_fut_cache = None

# ---------- Firestore (optional) ----------
fs = None
doc_ref = None
if USE_FS:
    try:
        from google.cloud import firestore  # type: ignore
        fs = firestore.Client()
        # FS_PATH like "sensex_brain/oc_state"
        parts = FS_PATH.split("/")
        col = parts[0]
        doc = parts[1] if len(parts) > 1 else "oc_state"
        doc_ref = fs.collection(col).document(doc)
    except Exception:
        fs = None
        doc_ref = None
        USE_FS = False  # hard off if import/perm fails

# ---------- Helpers ----------
def market_open_now() -> bool:
    now = datetime.now(TZ)
    if now.weekday() > 4:  # 0=Mon
        return False
    start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return start <= now <= end

def warmup():
    try:
        session.get("https://www.nseindia.com", headers=HEADERS, timeout=10)
    except Exception:
        pass

def round_to_step(x: float) -> int:
    return int(round(x / STRIKE_STEP) * STRIKE_STEP)

def pick_strikes_for_side(spot: float, side: str):
    """
    side: 'CE' or 'PE'
    Need: 1 ITM, 1 ATM, 4 OTM
    - CALL ITM: strike < spot; OTM: strike > spot
    - PUT  ITM: strike > spot; OTM: strike < spot
    """
    atm = round_to_step(spot)
    strikes = []

    if side == "CE":
        itm = atm - STRIKE_STEP         # nearest ITM (one step in-the-money)
        otms = [atm + i*STRIKE_STEP for i in range(1, 5)]  # 4 OTM
    else:  # PE
        itm = atm + STRIKE_STEP
        otms = [atm - i*STRIKE_STEP for i in range(1, 5)]

    strikes = [itm, atm] + otms
    return strikes

def sign_dot(v):
    if v is None:
        return "⚪"
    return "🟢" if v > 0 else ("🔴" if v < 0 else "⚪")

def fmt_int(v):
    return "—" if v is None else f"{int(v):,}"

def fmt_float(v, dp=2):
    return "—" if v is None else f"{v:.{dp}f}"

def deep_find_oi_vol(obj):
    """Recursively find first openInterest + volume pair in derivative JSON."""
    if isinstance(obj, dict):
        oi = None; vol = None
        for k, v in obj.items():
            lk = k.lower()
            if isinstance(v, (int, float)):
                if "open" in lk and "interest" in lk: oi = v
                if "volume" in lk: vol = v
        if oi is not None and vol is not None:
            return oi, vol
        for v in obj.values():
            r = deep_find_oi_vol(v)
            if r: return r
    elif isinstance(obj, list):
        for it in obj:
            r = deep_find_oi_vol(it)
            if r: return r
    return None

def fetch_futures_change():
    global _prev_fut_cache
    try:
        r = session.get(DERIV_URL, headers=HEADERS, timeout=15)
        r.raise_for_status()
        js = r.json()
        res = deep_find_oi_vol(js) or (0, 0)
        cur = (float(res[0]), float(res[1]))
    except Exception:
        cur = (0.0, 0.0)

    if _prev_fut_cache is None:
        delta = (None, None)
    else:
        delta = (cur[0]-_prev_fut_cache[0], cur[1]-_prev_fut_cache[1])
    _prev_fut_cache = cur
    return delta

def load_prev():
    if USE_FS and doc_ref:
        snap = doc_ref.get()
        return snap.to_dict() or {}
    return _prev_cache

def save_prev(state):
    if USE_FS and doc_ref:
        doc_ref.set(state, merge=True)
    else:
        _prev_cache.update(state)

# ---------- Core ----------
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
            # session OI delta from NSE field (changeinOpenInterest)
            return (
                float(leg.get("openInterest", 0) or 0),
                float(leg.get("impliedVolatility", 0) or 0.0),
                float(leg.get("totalTradedVolume", 0) or 0),
                float(leg.get("changeinOpenInterest", 0) or 0),
            )
    return 0.0, 0.0, 0.0, 0.0

def per_refresh_delta(key, cur_tuple, prev_map):
    """
    cur_tuple = (oi, iv, vol)
    prev_map[key] = {'oi':..,'iv':..,'vol':..}
    returns (Δoi, Δiv, Δvol)
    """
    prev = prev_map.get(key)
    if not prev:
        return (None, None, None)
    return (
        cur_tuple[0] - float(prev.get("oi", 0.0)),
        cur_tuple[1] - float(prev.get("iv", 0.0)),
        cur_tuple[2] - float(prev.get("vol", 0.0)),
    )

def build_table(side, spot, data, prev_map):
    strikes = pick_strikes_for_side(spot, side)
    rows = []
    total_doi = 0.0
    total_dvol = 0.0
    sum_iv = 0.0
    sum_div = 0.0
    counted = 0

    new_state = {}

    for s in strikes:
        oi, iv, vol, nse_doi = lookup_leg(data, s, side)
        d_oi, d_iv, d_vol = per_refresh_delta(f"{side}:{s}", (oi, iv, vol), prev_map)

        # if we don't have per-refresh, fallback to NSE session DOI for the dot/number
        shown_doi = nse_doi if d_oi is None else d_oi

        rows.append({
            "strike": s,
            "doi": shown_doi,
            "iv": iv,
            "div": d_iv,
            "dvol": d_vol
        })

        if d_oi is not None:   total_doi += d_oi
        if d_vol is not None:  total_dvol += d_vol
        sum_iv += iv; counted += 1
        if d_iv is not None:   sum_div += d_iv

        new_state[f"{side}:{s}"] = {"oi": oi, "iv": iv, "vol": vol}

    avg_iv = sum_iv / max(1, counted)
    avg_div = (sum_div / max(1, counted)) if counted else None

    return rows, total_doi if counted else None, avg_iv, avg_div, total_dvol if counted else None, new_state

def render_rows(rows):
    # fixed-width header
    out = []
    out.append(f"{'Strike':>7}  {'ΔOI':>8}  {'IV':>6}  {'ΔIV':>7}  {'ΔVOL':>8}")
    out.append("-"*44)
    for r in rows:
        dot_doi = sign_dot(r["doi"] if r["doi"] is not None else 0)
        dot_div = sign_dot(r["div"] if r["div"] is not None else 0)
        dot_dvol = sign_dot(r["dvol"] if r["dvol"] is not None else 0)
        out.append(
            f"{r['strike']:>7}  "
            f"{dot_doi} {fmt_int(0 if r['doi'] is None else int(r['doi'])):>6}  "
            f"{fmt_float(r['iv']):>6}  "
            f"{dot_div} {fmt_float(0 if r['div'] is None else r['div']):>5}  "
            f"{dot_dvol} {fmt_int(0 if r['dvol'] is None else int(r['dvol'])):>6}"
        )
    return "\n".join(out)

def build_message(expiry, spot, ce_rows, ce_stats, pe_rows, pe_stats, fut_delta):
    now = datetime.now(TZ).strftime("%d-%b %H:%M:%S")
    ce_total_doi, ce_avg_iv, ce_avg_div, ce_total_dvol = ce_stats
    pe_total_doi, pe_avg_iv, pe_avg_div, pe_total_dvol = pe_stats
    f_doi, f_dvol = fut_delta

    msg = []
    msg.append("<b>📊 NIFTY50 Option Chain</b>")
    msg.append(f"<b>🕒</b> {now} IST   <b>📅 Exp:</b> {expiry}")
    msg.append(f"<b>Spot:</b> {spot:.2f}")
    msg.append("")

    # CALL table
    msg.append("<b>CALL</b>")
    msg.append("<pre>" + render_rows(ce_rows) + "</pre>")
    msg.append(
        f"Total ΔOI: <b>{fmt_int(ce_total_doi)}</b>  |  "
        f"Avg IV: <b>{ce_avg_iv:.2f}</b>  |  "
        f"Avg ΔIV: <b>{fmt_float(ce_avg_div)}</b>  |  "
        f"Total ΔVOL: <b>{fmt_int(ce_total_dvol)}</b>"
    )
    msg.append("")

    # PUT table
    msg.append("<b>PUT</b>")
    msg.append("<pre>" + render_rows(pe_rows) + "</pre>")
    msg.append(
        f"Total ΔOI: <b>{fmt_int(pe_total_doi)}</b>  |  "
        f"Avg IV: <b>{pe_avg_iv:.2f}</b>  |  "
        f"Avg ΔIV: <b>{fmt_float(pe_avg_div)}</b>  |  "
        f"Total ΔVOL: <b>{fmt_int(pe_total_dvol)}</b>"
    )
    msg.append("")

    # Futures
    if f_doi is not None or f_dvol is not None:
        fdoi_txt = "—" if f_doi is None else f"{int(f_doi):,}"
        fvol_txt = "—" if f_dvol is None else f"{int(f_dvol):,}"
        msg.append(f"<b>Futures Δ</b>  ΔOI: <b>{fdoi_txt}</b> | ΔVOL: <b>{fvol_txt}</b>")

    return "\n".join(msg)

def send_telegram(text):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return {"ok": False, "error": "telegram env missing"}
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    return session.post(url, json={"chat_id": chat, "text": text, "parse_mode": "HTML"}, timeout=20).json()

# ---------- Run Once ----------
def run_once():
    if not market_open_now():
        return {"ok": False, "msg": "market closed"}

    try:
        expiry, spot, data = get_option_chain()

        # previous snapshot
        prev_map = load_prev()

        # CALL & PUT sections
        ce_rows, ce_doi, ce_avg_iv, ce_avg_div, ce_dvol, new_ce = build_table("CE", spot, data, prev_map)
        pe_rows, pe_doi, pe_avg_iv, pe_avg_div, pe_dvol, new_pe = build_table("PE", spot, data, prev_map)

        # save snapshot
        save_prev({**new_ce, **new_pe})

        # Futures deltas
        f_doi, f_dvol = fetch_futures_change()

        msg = build_message(
            expiry,
            spot,
            ce_rows,
            (ce_doi, ce_avg_iv, ce_avg_div, ce_dvol),
            pe_rows,
            (pe_doi, pe_avg_iv, pe_avg_div, pe_dvol),
            (f_doi, f_dvol)
        )

        tg = send_telegram(msg)
        return {"ok": True, "sent": tg, "spot": spot}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ---------- Flask Routes ----------
@app.route("/")
def health():
    return "NIFTY Alert Bot Active ✅", 200

@app.route("/run", methods=["GET", "POST"])
def run():
    return jsonify(run_once())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
