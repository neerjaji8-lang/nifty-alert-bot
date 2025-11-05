import os
import requests
from datetime import datetime
import pytz

# ====== Environment Variables ======
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SYMBOL = os.getenv("SYMBOL", "NIFTY")
STRIKE_STEP = int(os.getenv("STRIKE_STEP", "50"))

# ====== Helper: Check if Market is Open ======
def is_market_open():
    # India timezone
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    weekday = now.weekday()  # Monday=0, Sunday=6

    # Normal weekends
    if weekday in [5, 6]:
        return False

    # Market hours (9:15 to 15:30 IST)
    market_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if not (market_start <= now <= market_close):
        return False

    # Optional: NSE holiday list (you can extend it)
    holidays_2025 = [
        "2025-01-26", "2025-03-14", "2025-03-31",
        "2025-04-18", "2025-05-01", "2025-08-15",
        "2025-10-02", "2025-10-24", "2025-12-25"
    ]
    today_str = now.strftime("%Y-%m-%d")
    if today_str in holidays_2025:
        return False

    return True

# ====== Dummy Option Chain Fetch (replace with live API) ======
def fetch_option_chain_data():
    call_data = [
        {"strike": 25550, "oi": 50000, "change_volume": 12500, "iv": 3.06, "iv_change": 0.00},
        {"strike": 25600, "oi": 60000, "change_volume": 12000, "iv": 0.22, "iv_change": 0.00},
        {"strike": 25650, "oi": 35000, "change_volume": 10500, "iv": 1.67, "iv_change": 0.00},
        {"strike": 25700, "oi": 40000, "change_volume": 10000, "iv": 3.20, "iv_change": 0.00},
        {"strike": 25750, "oi": 50000, "change_volume": 15000, "iv": 4.51, "iv_change": 0.00},
        {"strike": 25800, "oi": 48000, "change_volume": 9600,  "iv": 5.78, "iv_change": 0.00},
    ]

    put_data = [
        {"strike": 25650, "oi": 48000, "change_volume": 0, "iv": 0.00, "iv_change": 0.00},
        {"strike": 25550, "oi": 50000, "change_volume": 0, "iv": 1.39, "iv_change": 0.00},
        {"strike": 25500, "oi": 52000, "change_volume": 0, "iv": 2.71, "iv_change": 0.00},
        {"strike": 25450, "oi": 53000, "change_volume": 0, "iv": 3.96, "iv_change": 0.00},
        {"strike": 25400, "oi": 54000, "change_volume": 0, "iv": 5.19, "iv_change": 0.00},
    ]

    # Calculate Volume % of OI
    for d in call_data + put_data:
        try:
            d["vol_perc"] = round((d["change_volume"] / d["oi"]) * 100, 2) if d["oi"] > 0 else 0.0
        except:
            d["vol_perc"] = 0.0

    futures_data = {
        "delta_oi": 0,
        "delta_vol": 0,
        "buy_qty": 9507225,
        "sell_qty": 2566575,
        "bias": "Bullish",
        "bias_diff": 6940650
    }

    spot_price = 25597.65
    return call_data, put_data, futures_data, spot_price

# ====== Format Option Chain for Telegram ======
def format_table(data, side_name):
    table = f"\n{'🟢' if side_name == 'CALL' else '🔴'} {side_name} SIDE\n"
    table += f"{'Strike':<8} | {'ΔOI':<6} | {'IV':<6} | {'ΔIV':<6} | {'VOL%':<6}\n"
    table += "─" * 82 + "\n"

    for row in data:
        table += (
            f"{row['strike']:<8} | "
            f"{row['oi']:<6} | "
            f"{row['iv']:<6.2f} | "
            f"{row['iv_change']:<6.2f} | "
            f"{row['vol_perc']:<6.2f}\n"
        )

    avg_iv = round(sum(d['iv'] for d in data) / len(data), 2)
    avg_vol = round(sum(d['vol_perc'] for d in data) / len(data), 2)
    table += "─" * 82 + "\n"
    table += f"Total → ΔOI:+0  |  IV:{avg_iv}  |  VOL%:{avg_vol}\n"
    return table

# ====== Telegram Function ======
def send_to_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    response = requests.post(url, json=payload)
    print("Message sent. Telegram status:", response.status_code)

# ====== Main Function ======
def main():
    if not is_market_open():
        message = "💤 *Market Closed*\nNSE trading hours: 09:15 - 15:30 IST\nNo data fetched today."
        send_to_telegram(message)
        return

    call_data, put_data, futures_data, spot_price = fetch_option_chain_data()
    now = datetime.now().strftime("%d-%b %H:%M:%S IST")

    message = (
        f"📊 *{SYMBOL} Option Chain*\n"
        f"{now}  |  Exp: 04-Nov-2025\n"
        f"Spot: {spot_price}\n"
        + format_table(call_data, "CALL")
        + format_table(put_data, "PUT")
        + f"\n⚙️ *Futures Δ:* ΔOI:{futures_data['delta_oi']} | ΔVOL:{futures_data['delta_vol']}\n"
        f"Buy: {futures_data['buy_qty']:,}  |  Sell: {futures_data['sell_qty']:,}\n"
        f"Bias: 🟢 {futures_data['bias']} ({futures_data['bias_diff']:,})"
    )

    send_to_telegram(message)

if __name__ == "__main__":
    main()