import os
import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
payload = {
    "chat_id": CHAT_ID,
    "text": "🚀 ¡Conexión exitosa! El Radar Cripto está activo y vinculado a Telegram."
}

res = requests.post(url, json=payload)
print("Respuesta de Telegram:", res.status_code, res.text)