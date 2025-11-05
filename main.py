from flask import Flask, request
import requests
import datetime
import os

app = Flask(__name__)

# 🧠 Environment variables (Cloud Run se load)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SYMBOL = os.getenv("SYMBOL", "NIFTY")
STRIKE_STEP = int(os.getenv("STRIKE_STEP", "50"))

# 🔹 Dummy data fetch simulation (replace with your Angel One API logic)
def fetch_option_chain_data():
    call_data = [
        {"strike": 25550, "change_in_oi": 0, "iv": 3.06, "iv_change": 0.00, "vol_perc": 0.00},
        {"strike": 25600, "change_in_oi": 0, "iv": 0.22, "iv_change": 0.00, "vol_perc": 0.00},
        {"strike": 25650, "change_in_oi": 0, "iv": 1.67, "iv_change": 0.00, "vol_perc": 0.00},
        {"strike": 25700, "change_in_oi": 0, "iv": 3.20, "iv_change": 0.00, "vol_perc": 0.00},
        {"strike": 25750, "change_in_oi": 0, "iv": 4.51, "iv_change": 0.00, "vol_perc": 0.00},
        {"strike": 25800, "change_in_oi": 0, "iv": 5.78, "iv_change": 0.00, "vol_perc": 0.00},
    ]
    put_data = [
        {"strike": 25650, "change_in_oi": 0, "iv": 0.00, "iv_change": 0.00, "vol_perc": 0.00},
        {"strike": 25550, "change_in_oi": 0, "iv": 1.39, "iv_change": 0.00, "vol_perc": 0.00},
        {"strike": 25500, "change_in_oi": 0, "iv": 2.71, "iv_change": 0.00, "vol_perc": 0.00},
        {"strike": 25450, "change_in_oi": 0, "iv": 3.96, "iv_change": 0.00, "vol_perc": 0.00},
        {"strike": 25400, "change_in_oi": 0, "iv": 5.19, "iv_change": 0.00, "vol_perc": 0.00},
    ]
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


# 🔹 Helper function to calculate totals
def calculate_totals(data):
    total_oi = sum(item["change_in_oi"] for item in data)
    avg_iv = round(sum(item["iv"] for item in data) / len(data), 2) if data else 0
    avg_vol = round(sum(item["vol_perc"] for item in data) / len(data), 2) if data else 0
    return total_oi, avg_iv, avg_vol


# 🔹 Format Option Chain neatly for Telegram
def format_table(title, data):
    total_oi, avg_iv, avg_vol = calculate_totals(data)
    text = f"*{title} SIDE*\n"
    text += "```\nStrike     ΔOI      IV      ΔIV     VOL%\n"
    for row in data:
        text += f"{row['strike']:<8}{row['change_in_oi']:<9}{row['iv']:<8}{row['iv_change']:<8}{row['vol_perc']:<8}\n"
    text += "```\n"
    text += f"*Total → ΔOI:* {total_oi:+} │ *IV:* {avg_iv} │ *VOL%:* {avg_vol}\n\n"
    return text


# 🔹 Send message to Telegram
def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    r = requests.post(url, data=payload)
    return r.status_code


# 🔹 Flask route for Cloud Run trigger
@app.route('/run', methods=['GET'])
def run_bot():
    now = datetime.datetime.now().strftime("%d-%b %H:%M:%S IST")

    call_data, put_data, futures_data, spot_price = fetch_option_chain_data()

    call_text = format_table("CALL", call_data)
    put_text = format_table("PUT", put_data)

    fut = futures_data
    futures_text = (
        f"⚙️ *Futures Δ:* ΔOI:{fut['delta_oi']} │ ΔVOL:{fut['delta_vol']}\n"
        f"*Buy:* {fut['buy_qty']:,} │ *Sell:* {fut['sell_qty']:,}\n"
        f"*Bias:* 🟢 *{fut['bias']} ({fut['bias_diff']:,})*\n"
    )

    header = (
        f"📊 *{SYMBOL} Option Chain*\n"
        f"🗓 *{now}* │ *Exp:* 04-Nov-2025\n"
        f"📈 *Spot:* {spot_price}\n\n"
    )

    message = header + call_text + put_text + futures_text
    status = send_telegram_message(message)

    return f"Message sent. Telegram status: {status}", 200


# 🔹 Default health route
@app.route('/')
def home():
    return "Nifty Alert Bot is active 🚀", 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)