import os
import requests
from datetime import datetime
import pytz

# ========== ENVIRONMENT VARS ==========
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SYMBOL = os.getenv("SYMBOL", "NIFTY")
STRIKE_STEP = int(os.getenv("STRIKE_STEP", "50"))

# ========== MARKET OPEN CHECK ==========
def is_market_open():
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    weekday = now.weekday()
    if weekday in [5, 6]:
        return False
    start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if not (start <= now <= end):
        return False
    holidays = [
        "2025-01-26", "2025-03-14", "2025-04-18", "2025-05-01",
        "2025-08-15", "2025-10-02", "2025-10-24", "2025-12-25"
    ]
    today = now.strftime("%Y-%m-%d")
    return today not in holidays

# ========== AUTO STRIKE GENERATION ==========
def get_dynamic_strikes(spot, step, count=3):
    atm = round(spot / step) * step
    strikes = [atm + (i * step) for i in range(-count, count + 1)]
    return sorted(strikes)

# ========== OPTION CHAIN FETCH (DUMMY) ==========
def fetch_option_chain_data(spot_price):
    strikes = get_dynamic_strikes(spot_price, STRIKE_STEP, 3)
    call_data, put_data = [], []

    for s in strikes:
        # Dummy test data (replace with live)
        call_data.append({
            "strike": s,
            "oi": 50000 + (s - strikes[0]) * 8,
            "change_volume": 0,
            "iv": round(2 + (s - strikes[0]) / 1000, 2),
            "iv_change": 0.00
        })
        put_data.append({
            "strike": s,
            "oi": 48000 + (strikes[-1] - s) * 10,
            "change_volume": 0,
            "iv": round(3 + (strikes[-1] - s) / 1200, 2),
            "iv_change": 0.00
        })

    futures_data = {
        "delta_oi": 0,
        "delta_vol": 0,
        "buy_qty": 9507225,
        "sell_qty": 2566575,
        "bias": "Bullish",
        "bias_diff": 6940650
    }

    return call_data, put_data, futures_data

# ========== CALCULATE VOL% ==========
def calculate_volume_percentage(data):
    total, count = 0, 0
    for d in data:
        if d["oi"] > 0 and d["change_volume"] > 0:
            d["vol_perc"] = round((d["change_volume"] / d["oi"]) * 100, 2)
            total += d["vol_perc"]
            count += 1
        else:
            d["vol_perc"] = 0.00
    avg_vol = round(total / count, 2) if count > 0 else 0.00
    return avg_vol

# ========== TABLE FORMAT ==========
def format_table(data, side_name):
    table = f"\n{'🟢' if side_name == 'CALL' else '🔴'} {side_name} SIDE\n"
    table += f"{'Strike':<8} | {'ΔOI':<8} | {'IV':<6} | {'ΔIV':<6} | {'VOL%':<8}\n"
    table += "─" * 70 + "\n"
    for row in data:
        table += (
            f"{row['strike']:<8} | "
            f"{row['oi']:<8} | "
            f"{row['iv']:<6.2f} | "
            f"{row['iv_change']:<6.2f} | "
            f"{row['vol_perc']:<8.2f}\n"
        )
    avg_iv = round(sum(d['iv'] for d in data) / len(data), 2)
    avg_vol = calculate_volume_percentage(data)
    table += "─" * 70 + "\n"
    table += f"Total → ΔOI:+0 | IV:{avg_iv} | VOL%:{avg_vol}\n"
    return table

# ========== TELEGRAM MESSAGE ==========
def send_to_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    r = requests.post(url, json=payload)
    print("Telegram status:", r.status_code)

# ========== MAIN ==========
def main():
    if not is_market_open():
        send_to_telegram("💤 *Market Closed*\nNSE Timings: 09:15 - 15:30 IST\nNo Option Chain Update.")
        return

    spot_price = 25597.65  # Replace with live API
    call_data, put_data, futures_data = fetch_option_chain_data(spot_price)
    now = datetime.now().strftime("%d-%b %H:%M:%S IST")

    message = (
        f"📊 *{SYMBOL} Option Chain*\n"
        f"{now} | Exp: 04-Nov-2025\nSpot: {spot_price}\n"
        + format_table(call_data, "CALL")
        + format_table(put_data, "PUT")
        + f"\n⚙️ Futures Δ: ΔOI:{futures_data['delta_oi']} | ΔVOL:{futures_data['delta_vol']}\n"
          f"Buy: {futures_data['buy_qty']:,} | Sell: {futures_data['sell_qty']:,}\n"
          f"Bias: 🟢 {futures_data['bias']} ({futures_data['bias_diff']:,})"
    )

    send_to_telegram(message)

if __name__ == "__main__":
    main()