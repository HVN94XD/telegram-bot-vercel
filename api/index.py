os = __import__('os')
re = __import__('re')
from flask import Flask, request
import telebot
from telebot import types
from supabase import create_client

BOT_TOKEN = os.environ.get("BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
ITEMS_POR_PAGINA = 5

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
app = Flask(__name__)

# --- VALIDACIÓN DE USUARIOS EN SUPABASE ---
def es_miembro_autorizado(user_id):
    if user_id == ADMIN_ID:
        return True
    res = supabase.table("usuarios_autorizados").select("user_id").eq("user_id", user_id).execute()
    return len(res.data) > 0

# --- OPERACIONES DEL CATÁLOGO EN SUPABASE ---
def limpiar_titulo(texto):
    if not texto:
        return None
    patron_ignorar = re.compile(
        r'^(\.(obmx?|svb|opk|espk|spk|ice|exe|zip|rar)|obmx?|svb|opk|espk|spk|ice|exe|\d+(\.\d+)?\s*(kb|mb|gb|b))\b',
        re.IGNORECASE
    )
    lineas = [l.strip() for l in texto.split("\n") if l.strip()]
    for linea in lineas:
        if patron_ignorar.match(linea):
            continue
        linea_limpia = re.sub(r'[🎖️⭐🎖🔥📌🚀📁📄💥✨\t]', '', linea).strip()
        if len(linea_limpia) > 2:
            return linea_limpia
    return lineas[0] if lineas else None

def registrar_archivo_o_pack(file_id, nombre_archivo, tipo, caption, chat_id, message_id):
    titulo = limpiar_titulo(caption) or nombre_archivo
    descripcion = (caption or "").strip()

    existentes = supabase.table("packs").select("id").ilike("titulo", titulo).execute()
    for row in existentes.data:
        eliminar_pack_manual(row["id"])

    res = supabase.table("packs").insert({"titulo": titulo, "descripcion": descripcion}).execute()
    pack_id = res.data[0]["id"]

    supabase.table("pack_archivos").insert({
        "pack_id": pack_id,
        "file_id": file_id,
        "nombre_archivo": nombre_archivo,
        "tipo": tipo,
        "chat_id": chat_id,
        "message_id": message_id
    }).execute()

def eliminar_pack_manual(pack_id):
    archivos = supabase.table("pack_archivos").select("chat_id, message_id").eq("pack_id", pack_id).execute()
    for row in archivos.data:
        if row["chat_id"] and row["message_id"]:
            try:
                bot.delete_message(row["chat_id"], row["message_id"])
            except Exception:
                pass
    supabase.table("packs").delete().eq("id", pack_id).execute()

def obtener_total_packs():
    res = supabase.table("packs").select("id", count="exact").execute()
    return res.count or 0

def obtener_packs_pagina(pagina=1, limite=ITEMS_POR_PAGINA):
    offset = (pagina - 1) * limite
    res = supabase.table("packs").select("id, titulo").order("id", desc=True).range(offset, offset + limite - 1).execute()
    return [(row["id"], row["titulo"]) for row in res.data]

def obtener_detalles_pack(pack_id):
    res_p = supabase.table("packs").select("titulo, descripcion, fecha").eq("id", pack_id).execute()
    if not res_p.data:
        return None
    pack = (res_p.data[0]["titulo"], res_p.data[0]["descripcion"], res_p.data[0].get("fecha"))
    res_a = supabase.table("pack_archivos").select("file_id, nombre_archivo, tipo").eq("pack_id", pack_id).execute()
    archivos = [(row["file_id"], row["nombre_archivo"], row["tipo"]) for row in res_a.data]
    return pack, archivos

def extraer_info_archivo(message):
    if message.document:
        return message.document.file_id, message.document.file_name or "Archivo", "document"
    elif message.video:
        return message.video.file_id, message.video.file_name or "Video", "video"
    elif message.audio:
        return message.audio.file_id, message.audio.file_name or "Audio", "audio"
    elif message.photo:
        return message.photo[-1].file_id, "Foto", "photo"
    return None, None, None

def crear_markup_catalogo(pagina=1):
    total = obtener_total_packs()
    total_paginas = max(1, (total + ITEMS_POR_PAGINA - 1) // ITEMS_POR_PAGINA)
    packs = obtener_packs_pagina(pagina)
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    for pack_id, titulo in packs:
        markup.add(types.InlineKeyboardButton(f"⭐ {titulo}", callback_data=f"pack_{pack_id}"))
    
    botones = []
    if pagina > 1:
        botones.append(types.InlineKeyboardButton("⬅️ Ant", callback_data=f"pag_{pagina - 1}"))
    botones.append(types.InlineKeyboardButton(f"{pagina}/{total_paginas}", callback_data="noop"))
    if pagina < total_paginas:
        botones.append(types.InlineKeyboardButton("Sig ➡️", callback_data=f"pag_{pagina + 1}"))
    
    markup.row(*botones)
    return markup

# --- HANDLERS TELEGRAM ---
@bot.message_handler(commands=['start', 'hvn94', 'HVN94'])
def cmd_start(message):
    if not es_miembro_autorizado(message.from_user.id):
        bot.reply_to(message, "⛔ No estás autorizado para usar este bot.")
        return
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("📁 Catálogo Completo", "🕒 Últimas Subidas")
    bot.send_message(message.chat.id, "👋 **Panel HVN94 Convocado**", reply_markup=markup, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📁 Catálogo Completo" or m.text == "/list")
def ver_catalogo(message):
    if not es_miembro_autorizado(message.from_user.id):
        return
    if obtener_total_packs() == 0:
        bot.send_message(message.chat.id, "📂 Catálogo vacío.")
        return
    markup = crear_markup_catalogo(1)
    bot.send_message(message.chat.id, "📂 **Catálogo:**", reply_markup=markup, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.chat.type in ['group', 'supergroup'], content_types=['document', 'video', 'audio', 'photo', 'text'])
def capturar_contenido(message):
    f_id, f_nombre, f_tipo = extraer_info_archivo(message)
    if f_id:
        registrar_archivo_o_pack(f_id, f_nombre, f_tipo, message.caption or "", message.chat.id, message.message_id)

@bot.callback_query_handler(func=lambda call: True)
def callbacks(call):
    if call.data == "noop":
        bot.answer_callback_query(call.id)
        return

    if not es_miembro_autorizado(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ Acceso Denegado.", show_alert=True)
        return

    if call.data.startswith("pag_"):
        pagina = int(call.data.replace("pag_", ""))
        markup = crear_markup_catalogo(pagina)
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
        bot.answer_callback_query(call.id)

    elif call.data.startswith("pack_"):
        pack_id = int(call.data.replace("pack_", ""))
        detalles = obtener_detalles_pack(pack_id)
        if not detalles:
            bot.answer_callback_query(call.id, "No existe.")
            return
        (titulo, desc, _), archivos = detalles
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("⬇️ Descargar", callback_data=f"dl_{pack_id}"))
        if call.from_user.id == ADMIN_ID:
            markup.add(types.InlineKeyboardButton("🗑️ Eliminar", callback_data=f"del_{pack_id}"))
        bot.send_message(call.message.chat.id, f"📦 **{titulo}**\n\n{desc}", reply_markup=markup, parse_mode="Markdown")
        bot.answer_callback_query(call.id)

    elif call.data.startswith("dl_"):
        pack_id = int(call.data.replace("dl_", ""))
        detalles = obtener_detalles_pack(pack_id)
        if detalles:
            _, archivos = detalles
            for f_id, _, tipo in archivos:
                bot.send_document(call.message.chat.id, f_id)
        bot.answer_callback_query(call.id)

    elif call.data.startswith("del_"):
        if call.from_user.id == ADMIN_ID:
            pack_id = int(call.data.replace("del_", ""))
            eliminar_pack_manual(pack_id)
            bot.answer_callback_query(call.id, "🗑️ Pack eliminado.")
            bot.edit_message_text("✅ Pack eliminado.", call.message.chat.id, call.message.message_id)

# --- SERVERLESS ENTRYPOINT ---
@app.route('/', methods=['POST', 'GET'])
def webhook():
    if request.method == 'POST':
        json_data = request.get_json(silent=True)
        if json_data:
            update = telebot.types.Update.de_json(json_data)
            bot.process_new_updates([update])
        return 'OK', 200
    return 'Bot activo', 200
