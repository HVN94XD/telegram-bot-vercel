import os
import re
import sqlite3
import threading
from flask import Flask, request
import telebot
from telemetry import types if 'telemetry' in globals() else telebot.types

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
CONTACTO_ADMIN = "@HVN94"
TIEMPO_AUTO_ELIMINAR = 60
ITEMS_POR_PAGINA = 8

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
app = Flask(__name__)

ultimo_pack_id = None
lock_db = threading.Lock()
GRUPOS_REGISTRADOS = set()

# --- BASE DE DATOS LOCAL (SQLite en /tmp para entornos serverless) ---
DB_PATH = "/tmp/archivos.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS packs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            titulo TEXT,
            descripcion TEXT,
            fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pack_archivos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pack_id INTEGER,
            file_id TEXT,
            nombre_archivo TEXT,
            tipo TEXT,
            chat_id INTEGER,
            message_id INTEGER,
            FOREIGN KEY (pack_id) REFERENCES packs(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS miembros_autorizados (
            user_id INTEGER PRIMARY KEY,
            nombre TEXT,
            username TEXT,
            origen TEXT,
            fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS grupos_vinculados (
            chat_id INTEGER PRIMARY KEY,
            titulo TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# --- AUTODESTRUCCIÓN Y LIMPIEZA ---
def auto_destruir_mensaje(chat_id, message_ids, delay=60):
    def tarea():
        import time
        time.sleep(delay)
        for msg_id in message_ids:
            try:
                bot.delete_message(chat_id, msg_id)
            except Exception:
                pass
    threading.Thread(target=tarea, daemon=True).start()

def borrar_comando_usuario(message):
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except Exception:
        pass

def enviar_temporal(chat_id, texto, markup=None, parse_mode="Markdown"):
    try:
        msg = bot.send_message(chat_id, texto, reply_markup=markup, parse_mode=parse_mode)
        auto_destruir_mensaje(chat_id, [msg.message_id], delay=TIEMPO_AUTO_ELIMINAR)
        return msg
    except Exception:
        return None

# --- GESTIÓN DE ACCESOS ---
def registrar_grupo_en_bd(chat_id, titulo):
    if chat_id not in GRUPOS_REGISTRADOS:
        GRUPOS_REGISTRADOS.add(chat_id)
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO grupos_vinculados (chat_id, titulo) VALUES (?, ?)", (chat_id, titulo))
        conn.commit()
        conn.close()

def es_miembro_autorizado(user_id):
    if user_id == ADMIN_ID:
        return True
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM miembros_autorizados WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    return res is not None

# --- MANIPULACIÓN DE ARCHIVOS Y REGISTROS ---
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
    global ultimo_pack_id
    with lock_db:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        tiene_caption = bool(caption and len(caption.strip()) > 5)

        if tiene_caption:
            titulo = limpiar_titulo(caption) or nombre_archivo
            descripcion = caption.strip()

            cursor.execute("INSERT INTO packs (titulo, descripcion) VALUES (?, ?)", (titulo, descripcion))
            pack_id = cursor.lastrowid
            ultimo_pack_id = pack_id

            cursor.execute("""
                INSERT INTO pack_archivos (pack_id, file_id, nombre_archivo, tipo, chat_id, message_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (pack_id, file_id, nombre_archivo, tipo, chat_id, message_id))
        else:
            if ultimo_pack_id is not None:
                cursor.execute("""
                    INSERT INTO pack_archivos (pack_id, file_id, nombre_archivo, tipo, chat_id, message_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (ultimo_pack_id, file_id, nombre_archivo, tipo, chat_id, message_id))
            else:
                titulo = nombre_archivo
                cursor.execute("INSERT INTO packs (titulo, descripcion) VALUES (?, ?)", (titulo, "Sin descripción"))
                pack_id = cursor.lastrowid
                ultimo_pack_id = pack_id
                cursor.execute("""
                    INSERT INTO pack_archivos (pack_id, file_id, nombre_archivo, tipo, chat_id, message_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (pack_id, file_id, nombre_archivo, tipo, chat_id, message_id))

        conn.commit()
        conn.close()

def obtener_total_packs():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM packs")
    total = cursor.fetchone()[0]
    conn.close()
    return total

def obtener_packs_pagina(pagina=1, limite=ITEMS_POR_PAGINA):
    offset = (pagina - 1) * limite
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, titulo FROM packs ORDER BY id DESC LIMIT ? OFFSET ?", (limite, offset))
    res = cursor.fetchall()
    conn.close()
    return res

def obtener_detalles_pack(pack_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT titulo, descripcion, fecha FROM packs WHERE id = ?", (pack_id,))
    pack = cursor.fetchone()
    if not pack:
        conn.close()
        return None
    cursor.execute("SELECT file_id, nombre_archivo, tipo FROM pack_archivos WHERE pack_id = ?", (pack_id,))
    archivos = cursor.fetchall()
    conn.close()
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

def teclado_principal(es_admin=False):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton("📁 Catálogo Completo"),
        types.KeyboardButton("🕒 Últimas Subidas"),
        types.KeyboardButton("ℹ️ Ayuda")
    )
    return markup

def crear_markup_catalogo(pagina=1):
    total = obtener_total_packs()
    total_paginas = max(1, (total + ITEMS_POR_PAGINA - 1) // ITEMS_POR_PAGINA)
    packs = obtener_packs_pagina(pagina)
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    for pack_id, titulo in packs:
        markup.add(types.InlineKeyboardButton(f"⭐ {titulo}", callback_data=f"pack_{pack_id}"))
    
    botones_nav = []
    if pagina > 1:
        botones_nav.append(types.InlineKeyboardButton("⬅️ Anterior", callback_data=f"pag_{pagina - 1}"))
    botones_nav.append(types.InlineKeyboardButton(f"📄 {pagina}/{total_paginas}", callback_data="noop"))
    if pagina < total_paginas:
        botones_nav.append(types.InlineKeyboardButton("Siguiente ➡️", callback_data=f"pag_{pagina + 1}"))
    
    markup.row(*botones_nav)
    return markup, total_paginas

# --- HANDLERS DE TELEGRAM ---
@bot.message_handler(commands=['start', 'hvn94', 'HVN94'])
def cmd_start_hvn94(message):
    if message.chat.type in ['group', 'supergroup']:
        registrar_grupo_en_bd(message.chat.id, message.chat.title or "Grupo")

    if not es_miembro_autorizado(message.from_user.id):
        enviar_temporal(message.chat.id, "⛔ **Acceso Restringido**. No estás autorizado para usar este bot.")
        return

    es_admin = (message.from_user.id == ADMIN_ID)
    texto = "👋 **¡Panel HVN94 Convocado!**\n\nUsa los botones de abajo para navegar por el catálogo."
    enviar_temporal(message.chat.id, texto, markup=teclado_principal(es_admin))

@bot.message_handler(func=lambda msg: msg.text == "📁 Catálogo Completo" or msg.text == "/list")
def ver_catalogo(message):
    if not es_miembro_autorizado(message.from_user.id):
        return
    total = obtener_total_packs()
    if total == 0:
        enviar_temporal(message.chat.id, "📂 Aún no hay archivos registrados.")
        return
    markup, _ = crear_markup_catalogo(pagina=1)
    enviar_temporal(message.chat.id, f"📂 **Catálogo Disponible** ({total} elementos):", markup)

@bot.message_handler(func=lambda msg: msg.chat.type in ['group', 'supergroup'], content_types=['text', 'document', 'video', 'audio', 'photo'])
def capturar_grupo_exclusivo(message):
    f_id, f_nombre, f_tipo = extraer_info_archivo(message)
    if f_id:
        registrar_archivo_o_pack(f_id, f_nombre, f_tipo, message.caption or "", message.chat.id, message.message_id)

@bot.callback_query_handler(func=lambda call: True)
def callbacks(call):
    user_id = call.from_user.id
    data = call.data

    if data == "noop":
        bot.answer_callback_query(call.id)
        return

    if not es_miembro_autorizado(user_id):
        bot.answer_callback_query(call.id, "⛔ Acceso Denegado.", show_alert=True)
        return

    if data.startswith("pag_"):
        pagina = int(data.replace("pag_", ""))
        markup, _ = crear_markup_catalogo(pagina=pagina)
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
        except Exception:
            pass
        bot.answer_callback_query(call.id)

    elif data.startswith("pack_"):
        pack_id = int(data.replace("pack_", ""))
        detalles = obtener_detalles_pack(pack_id)
        if not detalles:
            bot.answer_callback_query(call.id, "El elemento ya no existe.")
            return

        (titulo, descripcion, _), archivos = detalles
        nombres_archivos = "\n".join([f"  • `{a[1]}`" for a in archivos])
        texto = f"🏷 **{titulo}**\n\n📦 **Archivos ({len(archivos)}):**\n{nombres_archivos}\n\n📋 **Descripción:**\n{descripcion}"
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("⬇️ Descargar Pack", callback_data=f"descargar_{pack_id}"))
        enviar_temporal(call.message.chat.id, texto, markup)
        bot.answer_callback_query(call.id)

    elif data.startswith("descargar_"):
        pack_id = int(data.replace("descargar_", ""))
        detalles = obtener_detalles_pack(pack_id)
        if detalles:
            _, archivos = detalles
            for f_id, _, tipo in archivos:
                try:
                    bot.send_document(call.message.chat.id, f_id)
                except Exception:
                    pass
        bot.answer_callback_query(call.id)

# --- ENTRADA WEBHOOK PARA VERCEL ---
@app.route('/api/index', methods=['POST', 'GET'])
def webhook():
    if request.method == 'POST':
        json_data = request.get_json(silent=True)
        if json_data:
            update = telebot.types.Update.de_json(json_data)
            bot.process_new_updates([update])
        return 'OK', 200
    return 'Bot activo correctamente en Vercel', 200
