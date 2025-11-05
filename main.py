import os
import requests
from datetime import datetime
import pytz
import json # JSON data handling ke liye

# ========== ENVIRONMENT VARS ==========
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SYMBOL = os.getenv("SYMBOL", "NIFTY")
STRIKE_STEP = int(os.getenv("STRIKE_STEP", "50"))
STRIKE_COUNT = 6 # ATM ke upar aur neeche 6 strikes
NSE_BASE_URL = "https://www.nseindia.com/api/option-chain-indices?symbol="

# ========== MARKET OPEN CHECK (NO CHANGE) ==========
# ... (is_market_open function jaisa pehle tha, waisa hi rahega) ...

def is_market_open():
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    weekday = now.weekday()
    # Market band Saturday (5) aur Sunday (6)
    if weekday in [5, 6]:
        return False
    # Market hours 09:15 se 15:30 IST
    start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if not (start <= now <= end):
        return False
    # Holidays (Tum 2025 ki list de chuke ho)
    holidays = [
        "2025-01-26", "2025-03-14", "2025-04-18", "2025-05-01",
        "2025-08-15", "2025-10-02", "2025-10-24", "2025-12-25"
    ]
    today = now.strftime("%Y-%m-%d")
    return today not in holidays

# ========== REAL-TIME OPTION CHAIN FETCH ==========
def fetch_option_chain_data_live(symbol):
    try:
        # NSE API ke liye zaruri headers (yeh API call ko stabilize karne ke liye zaroori hain)
        headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.149 Safari/537.36',
            'accept-language': 'en,gu;q=0.9,hi;q=0.8',
            'accept-encoding': 'gzip, deflate, br'
        }
        
        # Session cookie fetch karne ke liye base url par pehle hit karna behtar hai
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=headers) 

        # Option Chain API call
        response = session.get(NSE_BASE_URL + symbol, headers=headers, timeout=10)
        response.raise_for_status() # HTTP errors ke liye

        data = response.json()
        
        # Spot Price
        spot_price = data['records']['underlyingValue']
        
        # Expiry Date (Latest/Near-term)
        expiry_date = data['records']['expiryDates'][0]
        
        # Data processing
        filtered_call_data = []
        filtered_put_data = []
        
        # ATM nikalna
        atm = round(spot_price / STRIKE_STEP) * STRIKE_STEP
        
        # Strikes jo humein chahiye
        target_strikes = sorted([atm + (i * STRIKE_STEP) for i in range(-STRIKE_COUNT, STRIKE_COUNT + 1)])

        for record in data['records']['data']:
            strike = record['strikePrice']
            if strike in target_strikes and record['expiryDate'] == expiry_date:
                # Call Data
                ce = record.get('CE', {})
                if ce:
                    filtered_call_data.append({
                        "strike": strike,
                        "oi": ce.get('openInterest', 0),
                        "coi": ce.get('changeinOpenInterest', 0), # Change in OI
                        "volume": ce.get('totalTradedVolume', 0),
                        "c_volume": ce.get('changeinOpenInterest', 0), # NSE API mein change volume nahi milta, isliye hum sirf OI aur COI ka use karenge.
                        "iv": ce.get('impliedVolatility', 0.0),
                        "iv_change": 0.00 # NSE API mein IV change direct nahi milta
                    })
                
                # Put Data
                pe = record.get('PE', {})
                if pe:
                    filtered_put_data.append({
                        "strike": strike,
                        "oi": pe.get('openInterest', 0),
                        "coi": pe.get('changeinOpenInterest', 0), # Change in OI
                        "volume": pe.get('totalTradedVolume', 0),
                        "c_volume": pe.get('changeinOpenInterest', 0), # Same as call
                        "iv": pe.get('impliedVolatility', 0.0),
                        "iv_change": 0.00
                    })
        
        # Futures Data (Dummy / Example - NSE API se Futures data alag se fetch karna padta hai)
        futures_data = {
            "coi": 1200, 
            "c_volume": 50000, 
            "buy_qty": 9507225, 
            "sell_qty": 2566575, 
            "bias": "Bullish",
            "bias_diff": 6940650
        }


        return spot_price, expiry_date, filtered_call_data, filtered_put_data, futures_data

    except requests.exceptions.RequestException as e:
        print(f"Error fetching data: {e}")
        send_to_telegram(f"⚠️ Data Fetch Error for {symbol}: {e}")
        return None, None, None, None, None

# ========== CALCULATE VOLUME % (Tumhari requirement ke mutabik) ==========
def calculate_oi_vol_percentage(data):
    # Volume % calculation: (Total Traded Volume / OI) * 100
    for d in data:
        # COI, IV, Volume ki jagah, hum volume ka OI % dikha rahe hain.
        if d["oi"] > 0 and d["volume"] > 0:
            d["vol_perc"] = round((d["volume"] / d["oi"]) * 100, 2)
        else:
            d["vol_perc"] = 0.00
    return data

# ========== TABLE FORMAT (Updated) ==========
def format_table(data, side_name):
    
    # Volume % calculation apply kiya
    data_with_perc = calculate_oi_vol_percentage(data)

    table = f"\n{'🟢' if side_name == 'CALL' else '🔴'} **{side_name} SIDE**\n"
    # Column Header: COI, IV, VOL%
    table += f"| {'Strike':<8} | {'COI':>8} | {'IV':>6} | {'VOL%':>6} |\n"
    table += "|" + "─" * 38 + "|\n"
    
    total_coi = sum(d['coi'] for d in data_with_perc)
    
    for row in data_with_perc:
        # **COI** (Change in OI) aur **VOL%** (Volume/OI percentage)
        table += (
            f"| {row['strike']:<8} | "
            f"{row['coi']:>8} | "
            f"{row['iv']:>6.2f} | "
            f"{row['vol_perc']:>6.2f} |\n"
        )
    
    avg_iv = round(sum(d['iv'] for d in data_with_perc) / len(data_with_perc), 2)
    # Total Vol % average nikalna
    vol_percs = [d['vol_perc'] for d in data_with_perc if d['vol_perc'] > 0]
    avg_vol_perc = round(sum(vol_percs) / len(vol_percs), 2) if vol_percs else 0.00

    table += "|" + "─" * 38 + "|\n"
    table += f"Total → COI: **{total_coi:+,}** | IV:{avg_iv} | VOL%:{avg_vol_perc}\n"
    return table

# ========== TELEGRAM MESSAGE (NO CHANGE) ==========
def send_to_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    # Parse_mode ko 'Markdown' se 'MarkdownV2' ya 'HTML' mein change karna padega agar fixed-width font chahiye.
    # Simple 'Markdown' mein table alignment achi nahi hoti, lekin hum yahan bolding use kar rahe hain.
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload)
        r.raise_for_status()
        print(f"Telegram status: {r.status_code}")
    except requests.exceptions.RequestException as e:
        print(f"Error sending Telegram message: {e}")

# ========== MAIN (har 3 minute mein run karne ke liye) ==========
def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Error: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not set.")
        return

    # Is block ko har 3 minute mein run karna padega (Cloud platform ya Cron Job use karein)
    if not is_market_open():
        # Ye message sirf ek baar bhejna chahiye, har 3 min mein nahi.
        # Isliye production mein is logic ko adjust karna hoga.
        print("Market Closed. Exiting.")
        return

    spot_price, expiry_date, call_data, put_data, futures_data = fetch_option_chain_data_live(SYMBOL)
    
    if not spot_price:
        return # Data fetch mein error hua

    now = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%d-%b %H:%M:%S IST")
    
    # Buy/Sell Difference percentage
    total_qty = futures_data['buy_qty'] + futures_data['sell_qty']
    buy_perc = round((futures_data['buy_qty'] / total_qty) * 100, 2) if total_qty > 0 else 0
    sell_perc = round((futures_data['sell_qty'] / total_qty) * 100, 2) if total_qty > 0 else 0
    
    bias_emoji = '🟢' if futures_data['bias'] == 'Bullish' else '🔴' if futures_data['bias'] == 'Bearish' else '🟡'

    message = (
        f"📊 **{SYMBOL} Option Chain** ({now})\n"
        f"Exp: {expiry_date} | Spot: **{spot_price:,.2f}**\n"
        
        # Call and Put Tables
        + format_table(call_data, "CALL")
        + format_table(put_data, "PUT")
        
        # Futures Data
        + "\n--- **Futures Data** ---\n"
          f"⚙️ ΔOI: **{futures_data['coi']:,}** | ΔVol: {futures_data['c_volume']:,}\n"
          f"Buy: **{futures_data['buy_qty']:,}** ({buy_perc}%) | Sell: **{futures_data['sell_qty']:,}** ({sell_perc}%)\n"
          f"Bias: {bias_emoji} **{futures_data['bias']}** ({futures_data['bias_diff']:,} Diff)"
    )

    send_to_telegram(message)

if __name__ == "__main__":
    # Tumhein is code ko har 3 minute mein run karne ke liye ek scheduler (jese: cron job, or cloud function) ki zaroorat padegi.
    main()