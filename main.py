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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.nseindia.com/",
    "Origin":  "https://www.nseindia.com",
}
session = requests.Session()
session.headers.update(HEADERS)

# ---------- TIME ----------
def is_market_open():
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    return now.hour >= 9 and (now.hour < 15 or (now.hour == 15 and now.minute <= 30))

# ---------- CACHE ----------
def load_cache():
    if not os.path.exists(CACHE_PATH): return None
    try:
        with open(CACHE_PATH, "r") as f: return json.load(f)
    except: return None

def save_cache(obj):
    try:
        with open(CACHE_PATH, "w") as f: json.dump(obj, f)
    except: pass

# ---------- UTILS ----------
def warmup():
    try:
        session.get("https://www.nseindia.com", timeout=8)
        session.get(f"https://www.nseindia.com/option-chain?symbol={SYMBOL}", timeout=8)
    except: pass

def get_json(url):
    try:
        r = session.get(url, timeout=15)
        if r.status_code == 200: return r.json()
    except: pass
    return None

def deep_find(obj, *contains):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if all(c in k.lower() for c in contains) and isinstance(v, (int, float)):
                return float(v)
        for v in obj.values():
            res = deep_find(v, *contains)
            if res is not None: return res
    elif isinstance(obj, list):
        for it in obj:
            res = deep_find(it, *contains)
            if res is not None: return res
    return None

def rstep(x): return int(round(x / STRIKE_STEP) * STRIKE_STEP)
def pick_strikes(spot, side):
    atm = rstep(spot)
    if side == "CE": return [atm - STRIKE_STEP, atm] + [atm + i*STRIKE_STEP for i in range(1,5)]
    else:            return [atm + STRIKE_STEP, atm] + [atm - i*STRIKE_STEP for i in range(1,5)]

def percent(p,t):
    try:
        if t != 0: return round(p/t*100,2)
    except: pass
    return 0.0

def emoji(v):
    if v is None: return "⚪"
    return "🟢" if v > 0 else ("🔴" if v < 0 else "⚪")

# ---------- NSE ----------
def fetch_option_chain():
    warmup()
    js = get_json(OC_URL)
    if not js: return None
    expiry = js["records"]["expiryDates"][0]
    spot = float(js["records"]["underlyingValue"])
    data = [d for d in js["records"]["data"] if d["expiryDate"] == expiry]
    return {"expiry": expiry, "spot": spot, "rows": data}

def lookup(rows, strike, side):
    for d in rows:
        if int(d.get("strikePrice", -1)) == strike and side in d:
            leg = d[side]
            return float(leg.get("openInterest", 0)), float(leg.get("impliedVolatility", 0)), float(leg.get("totalTradedVolume", 0))
    return 0.0, 0.0, 0.0

def fetch_fut():
    warmup()
    js = get_json(DERIV_URL)
    if not js: return 0,0,0,0,0.0
    oi = deep_find(js,"open","interest") or 0
    vol= deep_find(js,"volume") or 0
    b  = deep_find(js,"totalbuyquantity") or 0
    s  = deep_find(js,"totalsellquantity") or 0
    lp = deep_find(js,"lastprice") or 0.0
    return int(oi),int(vol),int(b),int(s),float(lp)

# ---------- FORMAT ----------
def render_rows(rows):
    out = ["<pre>Strike   ΔOI        IV     ΔIV       CVol%</pre>",
           "<pre>------------------------------------------</pre>"]
    for r in rows:
        out.append(
            f"<pre>{r['strike']:>6}  {emoji(r['doi'])}{r['doi']:+8,d}   {r['iv']:>5.2f}  {emoji(r['div'])}{r['div']:+6.2f}   {emoji(r['cvolp'])}{r['cvolp']:+7.2f}</pre>"
        )
    return "\n".join(out)

def build_msg(expiry,spot,ce,ce_tot,pe,pe_tot,fdoi,fvol,buy,sell,prem,dprem,tag):
    now = datetime.now(IST).strftime("%d-%b-%Y %H:%M:%S")
    biasnum = buy - sell
    bias = "🟢 Bullish" if biasnum>0 else ("🔴 Bearish" if biasnum<0 else "⚪ Neutral")

    msg = []
    msg.append(f"<b>📊 {SYMBOL} Option Chain</b>")
    msg.append(f"🕒 {now} IST | 📅 Exp: <b>{expiry}</b>")
    msg.append(f"Spot: <b>{spot:.2f}</b>\n")
    msg.append("🟩 <b>CALL SIDE</b>")
    msg.append(render_rows(ce))
    msg.append(f"ΣΔOI:<b>{ce_tot['doi']:+,}</b> | Avg IV:<b>{ce_tot['iv']:.2f}</b> | ΔIV:<b>{ce_tot['div']:+.2f}</b> | Avg CVol%:<b>{ce_tot['cvp']:.2f}</b>\n")
    msg.append("🟥 <b>PUT SIDE</b>")
    msg.append(render_rows(pe))
    msg.append(f"ΣΔOI:<b>{pe_tot['doi']:+,}</b> | Avg IV:<b>{pe_tot['iv']:.2f}</b> | ΔIV:<b>{pe_tot['div']:+.2f}</b> | Avg CVol%:<b>{pe_tot['cvp']:.2f}</b>\n")
    msg.append(f"⚙️ Futures ΔOI:<b>{fdoi:+,}</b> | ΔVOL:<b>{fvol:+,}</b>")
    msg.append(f"📊 Depth Buy:<b>{buy:,}</b> | Sell:<b>{sell:,}</b> | Bias:{bias} (<b>{abs(biasnum):,}</b>)")
    msg.append(f"📐 Premium Fut-Spot:<b>{prem:+.2f}</b> (Δ {dprem:+.2f}) → <b>{tag}</b>")
    return "\n".join(msg)

def send_tg(txt):
    if not TELEGRAM_BOT_TOKEN: return
    url=f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    session.post(url,json={"chat_id":TELEGRAM_CHAT_ID,"text":txt,"parse_mode":"HTML"})

# ---------- COMPUTE ----------
def compute(rows,prev,strikes,side):
    out=[]; doi=div=cvp=0;cnt=0;snap={}
    for s in strikes:
        oi,iv,vol=lookup(rows,s,side)
        old=prev.get(f"{side}:{s}",{}) if prev else {}
        d_oi=oi-float(old.get("oi",0))
        d_iv=iv-float(old.get("iv",0))
        cvolp=percent(vol-float(old.get("vol",0)),oi)
        out.append({"strike":s,"doi":d_oi,"iv":iv,"div":d_iv,"cvolp":cvolp})
        snap[f"{side}:{s}"]={"oi":oi,"iv":iv,"vol":vol}
        doi+=d_oi;div+=d_iv;cvp+=cvolp;cnt+=1
    return out,{"doi":doi,"iv":(iv/cnt),"div":div/cnt,"cvp":cvp/cnt},snap

def build_tag(fdoi,dprem):
    if fdoi>0 and dprem>0:return"Long Build-up ✅"
    if fdoi>0 and dprem<0:return"Short Build-up 🔻"
    if fdoi<0 and dprem<0:return"Long Unwinding ⬇️"
    if fdoi<0 and dprem>0:return"Short Covering ⬆️"
    return"Neutral"

# ---------- MAIN ----------
@app.route("/run")
def run():
    if not is_market_open() and request.args.get("test","0")!="1":
        return jsonify({"ok":False,"msg":"market closed"})
    oc=fetch_option_chain()
    if not oc: return jsonify({"ok":False,"msg":"NSE error"})
    exp,spot,rows=oc["expiry"],oc["spot"],oc["rows"]
    ce,pe=None,None
    cache=load_cache()or{}
    prev=cache.get("legs",{})
    ce_s,pe_s=pick_strikes(spot,"CE"),pick_strikes(spot,"PE")
    ce,ct,sce=compute(rows,prev,ce_s,"CE")
    pe,pt,spe=compute(rows,prev,pe_s,"PE")
    fut_oi,fut_vol,buy,sell,fut_p=fetch_fut()
    prem=fut_p-spot
    fdoi=fut_oi-float(cache.get("futures",{}).get("oi",0))
    fvol=fut_vol-float(cache.get("futures",{}).get("vol",0))
    dprem=prem-float(cache.get("premium",0))
    save_cache({"legs":{**sce,**spe},"futures":{"oi":fut_oi,"vol":fut_vol},"premium":prem})
    tag=build_tag(fdoi,dprem)
    send_tg(build_msg(exp,spot,ce,ct,pe,pt,fdoi,fvol,buy,sell,prem,dprem,tag))
    return jsonify({"ok":True,"msg":"sent"})

@app.route("/")
def home(): return "NIFTY Alert Bot Active ✅"

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",8080)))