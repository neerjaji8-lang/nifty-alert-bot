#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, json, requests
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify

# ------------- CONFIG -------------
SYMBOL = os.getenv("SYMBOL", "NIFTY")
STRIKE_STEP = int(os.getenv("STRIKE_STEP", "50"))
TZ = timezone(timedelta(hours=5, minutes=30))  # IST
CACHE_PATH = "/tmp/last_data.json"  # Cloud Run writable path

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://www.nseindia.com/",
}

OC_URL = f"https://www.nseindia.com/api/option-chain-indices?symbol={SYMBOL}"
DERIV_URL = f"https://www.nseindia.com/api/quote-derivative?symbol={SYMBOL}"

# ------------- APP -------------
app = Flask(__name__)
session = requests.Session()

# ------------- UTIL -------------
def market_open_now() -> bool:
    now = datetime.now(TZ)
    if now.weekday() > 4:
        return False
    start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return start <= now <= end

def warmup():
    try:
        session.get("https://www.nseindia.com", headers=HEADERS, timeout=10)
    except Exception:
        pass

def round_step(x: float) -> int:
    return int(round(x / STRIKE_STEP) * STRIKE_STEP)

def pick_strikes(spot: float, side: str):
    """Return [1 ITM, 1 ATM, 4 OTM] for CE/PE."""
    atm = round_step(spot)
    if side == "CE":
        itm = atm - STRIKE_STEP
        otm = [atm + i * STRIKE_STEP for i in range(1, 5)]
    else:  # PE
        itm = atm + STRIKE_STEP
        otm = [atm - i * STRIKE_STEP for i in range(1, 5)]
    return [itm, atm] + otm

def sign_dot(v):
    if v is None or v == 0:
        return "⚪"
    return "🟢" if v > 0 else "🔴"

def fmt_int(v):
    if v is None:
        return "—"
    return f"{int(v):,}"

def fmt_float(v, dp=2, show_sign=False):
    if v is None:
        return "—"
    s = f"{v:.{dp}f}"
    if show_sign and not s.startswith("-") and v != 0:
        s = "+" + s
    return s

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

# ------------- FETCH -------------
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
    """Return tuple (oi, iv, vol, changeInOI) for given strike/side."""
    s = int(strike)
    for d in data:
        if int(d.get("strikePrice", -1)) == s and side in d:
            leg = d[side]
            oi = float(leg.get("openInterest", 0) or 0)
            iv = float(leg.get("impliedVolatility", 0) or 0)
            vol = float(leg.get("totalTradedVolume", 0) or 0)
            coi = float(leg.get("changeinOpenInterest", 0) or 0)  # NSE session ΔOI
            return oi, iv, vol, coi
    return 0.0, 0.0, 0.0, 0.0

def deep_find(obj, *keys_like):
    """Find first numeric value whose key contains all substrings in keys_like."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            lk = k.lower()
            if all(s in lk for s in keys_like) and isinstance(v, (int, float)):
                return float(v)
        for v in obj.values():
            got = deep_find(v, *keys_like)
            if got is not None:
                return got
    elif isinstance(obj, list):
        for it in obj:
            got = deep_find(it, *keys_like)
            if got is not None:
                return got
    return None

def get_futures_extras():
    """Return (fut_oi, fut_vol, total_buy_qty, total_sell_qty)."""
    try:
        r = session.get(DERIV_URL, headers=HEADERS, timeout=20)
        r.raise_for_status()
        js = r.json()
        # OI and Volume (best-effort recursive)
        fut_oi = deep_find(js, "open", "interest") or 0.0
        fut_vol = deep_find(js, "volume") or 0.0
        # Order book totals
        tbq = deep_find(js, "totalbuyquantity") or 0.0
        tsq = deep_find(js, "totalsellquantity") or 0.0
        return float(fut_oi), float(fut_vol), int(tbq), int(tsq)
    except Exception:
        return 0.0, 0.0, 0, 0

# ------------- CORE -------------
def build_side_rows(spot, data, prev_legs, side):
    strikes = pick_strikes(spot, side)
    rows = []
    new_legs = {}
    total_volpct_sum = 0.0
    volpct_count = 0
    for s in strikes:
        oi, iv, vol, nse_coi = lookup_leg(data, s, side)
        key = f"{side}:{s}"
        prev = prev_legs.get(key) if prev_legs else None
        if prev:
            d_oi = oi - float(prev.get("oi", 0))
            d_iv = iv - float(prev.get("iv", 0))
            d_vol = vol - float(prev.get("vol", 0))
        else:
            d_oi = d_iv = d_vol = None

        # VOL% (ΔVOL / OI * 100). If OI==0 or d_vol is None -> None
        if d_vol is None or oi == 0:
            volpct = None
        else:
            volpct = (d_vol / oi) * 100.0

        if volpct is not None:
            total_volpct_sum += volpct
            volpct_count += 1

        rows.append({
            "strike": s,
            "doi": d_oi if d_oi is not None else nse_coi,  # fallback to NSE session ΔOI on first run
            "iv": iv,
            "div": d_iv,
            "volpct": volpct
        })

        new_legs[key] = {"oi": oi, "iv": iv, "vol": vol}

    avg_volpct = (total_volpct_sum / volpct_count) if volpct_count else None
    return rows, new_legs, avg_volpct

def render_rows(rows):
    out = []
    out.append(f"{'Strike':>7}  {'ΔOI':>9}  {'IV':>6}  {'ΔIV':>7}  {'VOL%':>7}")
    out.append("-" * 44)
    for r in rows:
        dot_doi = sign_dot(0 if r["doi"] is None else r["doi"])
        dot_div = sign_dot(0 if r["div"] is None else r["div"])
        dot_vp = sign_dot(0 if r["volpct"] is None else r["volpct"])
        doi_txt = "—" if r["doi"] is None else f"{int(r['doi']):+d}"
        div_txt = fmt_float(r["div"], 2, show_sign=True) if r["div"] is not None else "—"
        vp_txt = fmt_float(r["volpct"], 2, show_sign=True) if r["volpct"] is not None else "—"
        out.append(
            f"{r['strike']:>7}  "
            f"{dot_doi} {doi_txt:>6}  "
            f"{fmt_float(r['iv'],2):>6}  "
            f"{dot_div} {div_txt:>5}  "
            f"{dot_vp} {vp_txt:>6}"
        )
    return "\n".join(out)

def build_message(expiry, spot, ce_rows, ce_avg_vp, pe_rows, pe_avg_vp, fut_delta, buy_qty, sell_qty):
    now = datetime.now(TZ).strftime("%d-%b %H:%M:%S")
    fdoi, fvol = fut_delta

    bias_num = buy_qty - sell_qty
    bias_side = "🟢 Bullish" if bias_num > 0 else ("🔴 Bearish" if bias_num < 0 else "⚪ Neutral")

    lines = []
    lines.append(f"<b>📊 {SYMBOL} Option Chain</b>")
    lines.append(f"<b>🕒</b> {now} IST   <b>📅 Exp:</b> {expiry}")
    lines.append(f"<b>Spot:</b> {spot:.2f}")
    lines.append("")

    # CALL table
    lines.append("<b>CALL</b>")
    lines.append("<pre>" + render_rows(ce_rows) + "</pre>")
    lines.append(f"Avg VOL%: <b>{fmt_float(ce_avg_vp,2)}</b>")
    lines.append("")

    # PUT table
    lines.append("<b>PUT</b>")
    lines.append("<pre>" + render_rows(pe_rows) + "</pre>")
    lines.append(f"Avg VOL%: <b>{fmt_float(pe_avg_vp,2)}</b>")
    lines.append("")

    # Futures block (no % for volume as requested)
    fdoi_txt = "—" if fdoi is None else f"{int(fdoi):+d}"
    fvol_txt = "—" if fvol is None else f"{int(fvol):+d}"
    lines.append("<b>⚙️ Futures Δ</b>  "
                 f"ΔOI: <b>{fdoi_txt}</b>  |  ΔVOL: <b>{fvol_txt}</b>")
    lines.append(f"Buy: <b>{fmt_int(buy_qty)}</b>  |  Sell: <b>{fmt_int(sell_qty)}</b>  "
                 f"|  Bias: <b>{bias_side}</b> ({fmt_int(abs(bias_num))})")

    return "\n".join(lines)

def send_telegram(text):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return {"ok": False, "error": "telegram env missing"}
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        res = session.post(url, json=payload, timeout=20)
        return res.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ------------- RUN ONCE -------------
def run_once():
    if not market_open_now():
        return {"ok": False, "msg": "market closed"}

    try:
        expiry, spot, data = get_option_chain()

        # Load cache
        cache = load_cache()
        prev_legs = cache.get("legs", {}) if cache else {}

        # Build CE/PE rows (with Δ from cache)
        ce_rows, new_ce, ce_avg_vp = build_side_rows(spot, data, prev_legs, "CE")
        pe_rows, new_pe, pe_avg_vp = build_side_rows(spot, data, prev_legs, "PE")

        # Futures values
        fut_oi, fut_vol, buy_qty, sell_qty = get_futures_extras()

        # Futures Δ using cache
        if cache and "futures" in cache:
            prev_f = cache["futures"]
            f_doi = fut_oi - float(prev_f.get("oi", 0))
            f_dvol = fut_vol - float(prev_f.get("vol", 0))
        else:
            f_doi = None
            f_dvol = None

        # Prepare message
        msg = build_message(
            expiry, spot,
            ce_rows, ce_avg_vp,
            pe_rows, pe_avg_vp,
            (f_doi, f_dvol),
            buy_qty, sell_qty
        )

        # Save new cache
        new_cache = {
            "ts": datetime.now(TZ).isoformat(),
            "expiry": expiry,
            "spot": spot,
            "legs": {**new_ce, **new_pe},
            "futures": {"oi": fut_oi, "vol": fut_vol}
        }
        save_cache(new_cache)

        # If first run (no cache previously), don't spam Telegram
        if cache is None:
            return {"ok": True, "primed": True, "note": "base snapshot stored"}

        # Send Telegram
        tg = send_telegram(msg)
        return {"ok": True, "sent": tg}

    except Exception as e:
        return {"ok": False, "error": str(e)}

# ------------- FLASK -------------
@app.route("/")
def health():
    return "NIFTY Alert Bot Active ✅", 200

@app.route("/run", methods=["GET", "POST"])
def run():
    return jsonify(run_once())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))