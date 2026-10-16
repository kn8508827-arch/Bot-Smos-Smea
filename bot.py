import os
import requests
from flask import Flask, request

app = Flask(__name__)

# ดึง Token จาก Environment Variables บน Railway
TOKEN = os.environ.get('BOT_TOKEN')
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TOKEN}"

@app.route('/', methods=['GET'])
def index():
    return "Telegram Bot is running successfully!", 200

@app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    update = request.get_json()
    if update:
        # ตรวจสอบข้อความที่ส่งเข้ามาจากผู้ใช้
        if "message" in update:
            chat_id = update["message"]["chat"]["id"]
            text = update["message"].get("text", "")
            
            # ตอบกลับข้อความทดสอบ
            send_message(chat_id, f"ได้รับข้อความของคุณแล้ว: {text}")
            
    return "OK", 200

def send_message(chat_id, text):
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    requests.post(url, json=payload)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
