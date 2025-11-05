from flask import Flask, request
import requests
import datetime
import os

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

@app.route('/run', methods=['GET'])
def run_bot():
    text = "<pre>Test message for Telegram parse check\nΔOI: 10 │ IV: 3.25 │ VOL: 250</pre>"
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    r = requests.post(url, data=payload)
    return f"Telegram status: {r.status_code}, Response: {r.text}", 200

@app.route('/')
def home():
    return "Diagnostic test running ✅", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)