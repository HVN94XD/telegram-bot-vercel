import os
import json
import urllib.request
from flask import Flask, request
from supabase import create_client

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def enviar_mensaje(chat_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
        
    req = urllib.request.Request(
        url, 
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'}
    )
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode())
    except Exception as e:
        print(f"Error al enviar mensaje: {e}")
        return None

@app.route('/', methods=['POST', 'GET'])
def webhook():
    if request.method == 'POST':
        try:
            data = request.get_json(silent=True)
            if data and "message" in data:
                message = data["message"]
                chat_id = message["chat"]["id"]
                user_id = message["from"]["id"]
                text = message.get("text", "")
                
                # Validación de Administrador / Autorizado básica
                if user_id != ADMIN_ID:
                    # Opcional: verificar en Supabase si prefieres
                    pass

                if text in ["/start", "/hvn94", "HVN94"]:
                    keyboard = {
                        "keyboard": [
                            [{"text": "📁 Catálogo Completo"}, {"text": "🕒 Últimas Subidas"}]
                        ],
                        "resize_keyboard": True
                    }
                    enviar_mensaje(chat_id, "👋 *Panel HVN94 Convocado*", reply_markup=keyboard)
                
                elif text == "📁 Catálogo Completo" or text == "/list":
                    res = supabase.table("packs").select("id, titulo").limit(5).execute()
                    if not res.data:
                        enviar_mensaje(chat_id, "📂 Catálogo vacío.")
                    else:
                        texto_catalogo = "📂 *Catálogo de Packs:*\n\n"
                        for row in res.data:
                            texto_catalogo += f"⭐ {row['titulo']}\n"
                        enviar_mensaje(chat_id, texto_catalogo)

        except Exception as e:
            print(f"Error procesando update: {e}")
            
        return "OK", 200
        
    return "Bot activo correctamente en Vercel", 200
