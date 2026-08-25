from flask import Flask, request
import os
import urllib.request
import json

app = Flask(__name__)
BOT_TOKEN = os.environ.get("BOT_TOKEN")

@app.route('/api/index', methods=['POST', 'GET'])
def webhook():
    if request.method == 'POST':
        try:
            data = request.get_json(silent=True)
            if data and "message" in data:
                chat_id = data["message"]["chat"]["id"]
                text = data["message"].get("text", "")
                
                # Si escriben cualquier cosa, respondemos de vuelta
                if BOT_TOKEN:
                    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                    payload = {
                        "chat_id": chat_id,
                        "text": f"¡Hola! Recibí tu mensaje: {text}"
                    }
                    req = urllib.request.Request(
                        url, 
                        data=json.dumps(payload).encode('utf-8'),
                        headers={'Content-Type': 'application/json'}
                    )
                    urllib.request.urlopen(req)
        except Exception as e:
            print(f"Error: {e}")
            
        return "OK", 200
        
    return "El bot está funcionando correctamente", 200
