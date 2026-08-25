import os
import re
import sqlite3
import threading
import time
from flask import Flask, request
import telebot
from telebot import types

# ================= CONFIGURACIÓN =================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

CONTACTO_ADMIN = "@HVN94"
TIEMPO_AUTO_ELIMINAR = 30  # Reducido a 30 segundos
ITEMS_POR_PAGINA = 8

# URL o file_id de tu imagen de bienvenida
WELCOME_IMAGE_URL = "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?q=80&w=1000"

BIOGRAFIA_TEXTO = (
    "👑 **BIENVENIDO AL PANEL OFICIAL HVN94**\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "🔹 **Owner & Dev:** @HVN94\n"
    "🔹 **Acceso:** Exclusivo para miembros del grupo oficial.\n"
    "🔹 **Sistema:** Auto-indexación y entrega temporal de configs.\n\n"
    "⚡ _Selecciona una opción del menú o busca con `/buscar [nombre]`._\n"
    f"⏱ _Este mensaje y las entregas se autodestruyen en {TIEMPO_AUTO_ELIMINAR}s._"
)
# =================================================

app = Flask(__name__)
bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

ultimo_pack_id = None
lock_db = threading.Lock()
GRUPOS_REGISTRADOS = set()
DB_PATH = "/tmp/archivos.db"

# --- BASE DE DATOS LOCAL (/tmp) ---
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
        CREATE TABLE IF NOT EXISTS grupos_vinculados (
            chat_id INTEGER PRIMARY KEY,
            titulo TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# --- AUTODESTRUCCIÓN ---
def auto_destruir_mensaje(chat_id, message_ids, delay=30):
    def tarea():
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

def enviar_temporal(chat_id, texto, markup=None, parse_mode="Markdown", message_thread_id=None):
    try:
        kwargs = {"parse_mode": parse_mode}
        if markup:
            kwargs["reply_markup"] = markup
        if message_thread_id:
            kwargs["message_thread_id"] = message_thread_id

        msg = bot.send_message(chat_id, texto, **kwargs)
        auto_destruir_mensaje(chat_id, [msg.message_id], delay=TIEMPO_AUTO_ELIMINAR)
        return msg
    except Exception:
        return None

def enviar_foto_temporal(chat_id, foto_url, caption, markup=None, parse_mode="Markdown", message_thread_id=None):
    try:
        kwargs = {"caption": caption, "parse_mode": parse_mode}
        if markup:
            kwargs["reply_markup"] = markup
        if message_thread_id:
            kwargs["message_thread_id"] = message_thread_id

        msg = bot.send_photo(chat_id, foto_url, **kwargs)
        auto_destruir_mensaje(chat_id, [msg.message_id], delay=TIEMPO_AUTO_ELIMINAR)
        return msg
    except Exception:
        # Fallback a texto si la URL de la imagen falla
        return enviar_temporal(chat_id, caption, markup, parse_mode, message_thread_id)

# --- SEGURIDAD: CONTROL DE GRUPO Y ANTI-RATA ---
def validar_o_castigar_grupo(chat_id, titulo):
    try:
        admin_member = bot.get_chat_member(chat_id, ADMIN_ID)
        if admin_member.status in ['creator', 'administrator', 'member', 'restricted']:
            if chat_id not in GRUPOS_REGISTRADOS:
                GRUPOS_REGISTRADOS.add(chat_id)
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                cursor.execute("INSERT OR IGNORE INTO grupos_vinculados (chat_id, titulo) VALUES (?, ?)", (chat_id, titulo))
                conn.commit()
                conn.close()
            return True
    except Exception:
        pass

    # SI TÚ NO ESTÁS EN EL GRUPO: SPAM Y SALIDA
    try:
        alerta_rata = (
            "🚨 **RATA DETECTADA | ACCESO ILEGAL** 🚨\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Este bot es de uso privado y exclusivo de @HVN94.\n"
            "El administrador principal NO está en este grupo.\n\n"
            "🐀 **RATA DETECTADA. ABANDONANDO GRUPO INMEDIATAMENTE...**"
        )
        for _ in range(3):
            bot.send_message(chat_id, alerta_rata, parse_mode="Markdown")
        bot.leave_chat(chat_id)
    except Exception:
        pass
    return False

def es_miembro_autorizado(user_id):
    if user_id == ADMIN_ID:
        return True

    if not GRUPOS_REGISTRADOS:
        return False

    for grupo_id in list(GRUPOS_REGISTRADOS):
        try:
            yo = bot.get_chat_member(grupo_id, ADMIN_ID)
            if yo.status not in ['creator', 'administrator', 'member', 'restricted']:
                continue

            m = bot.get_chat_member(grupo_id, user_id)
            if m.status in ['creator', 'administrator', 'member', 'restricted']:
                return True
        except Exception:
            continue
    return False

# --- GESTIÓN DE ARCHIVOS ---
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

            cursor.execute("SELECT id FROM packs WHERE LOWER(titulo) = LOWER(?)", (titulo,))
            existentes = cursor.fetchall()
            for row in existentes:
                old_id = row[0]
                cursor.execute("SELECT chat_id, message_id FROM pack_archivos WHERE pack_id = ?", (old_id,))
                for c_id, m_id in cursor.fetchall():
                    if c_id and m_id:
                        try:
                            bot.delete_message(c_id, m_id)
                        except Exception:
                            pass
                cursor.execute("DELETE FROM pack_archivos WHERE pack_id = ?", (old_id,))
                cursor.execute("DELETE FROM packs WHERE id = ?", (old_id,))

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

def eliminar_pack_manual(pack_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id, message_id FROM pack_archivos WHERE pack_id = ?", (pack_id,))
    for c_id, m_id in cursor.fetchall():
        if c_id and m_id:
            try:
                bot.delete_message(c_id, m_id)
            except Exception:
                pass
    cursor.execute("DELETE FROM pack_archivos WHERE pack_id = ?", (pack_id,))
    cursor.execute("DELETE FROM packs WHERE id = ?", (pack_id,))
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

def buscar_packs(query):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT p.id, p.titulo 
        FROM packs p
        LEFT JOIN pack_archivos a ON p.id = a.pack_id
        WHERE p.titulo LIKE ? OR p.descripcion LIKE ? OR a.nombre_archivo LIKE ?
        LIMIT 15
    """, (f"%{query}%", f"%{query}%", f"%{query}%"))
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

# --- MENÚS FLOTANTES ---
def teclado_principal_flotante():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📁 Catálogo Completo", callback_data="menu_catalogo"),
        types.InlineKeyboardButton("🕒 Últimas Subidas", callback_data="menu_recientes"),
        types.InlineKeyboardButton("🔍 Buscar Archivo", callback_data="menu_buscar"),
        types.InlineKeyboardButton("ℹ️ Info", callback_data="menu_ayuda")
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
    markup.add(types.InlineKeyboardButton("🔙 Volver al Menú", callback_data="menu_principal"))
    return markup, total_paginas

# --- COMANDOS PRINCIPALES ---
@bot.message_handler(commands=['start', 'hvn94', 'HVN94'])
def cmd_start_hvn94(message):
    borrar_comando_usuario(message)
    thread_id = getattr(message, 'message_thread_id', None)

    if message.chat.type in ['group', 'supergroup']:
        if not validar_o_castigar_grupo(message.chat.id, message.chat.title or "Grupo"):
            return

    if not es_miembro_autorizado(message.from_user.id):
        enviar_temporal(message.chat.id, "🚫 **Acceso denegado.** Debes estar en el grupo oficial con el Administrador.", message_thread_id=thread_id)
        return

    enviar_foto_temporal(
        chat_id=message.chat.id,
        foto_url=WELCOME_IMAGE_URL,
        caption=BIOGRAFIA_TEXTO,
        markup=teclado_principal_flotante(),
        message_thread_id=thread_id
    )

@bot.message_handler(commands=['buscar', 'search'])
def cmd_buscar(message):
    borrar_comando_usuario(message)
    thread_id = getattr(message, 'message_thread_id', None)
    
    if not es_miembro_autorizado(message.from_user.id):
        return

    texto_parts = message.text.split(maxsplit=1)
    if len(texto_parts) < 2:
        enviar_temporal(message.chat.id, "⚠️ **Uso:** `/buscar [nombre]`\nEjemplo: `/buscar izzi`", message_thread_id=thread_id)
        return

    query = texto_parts[1].strip()
    resultados = buscar_packs(query)
    
    if not resultados:
        enviar_temporal(message.chat.id, f"❌ No se encontró nada para: `{query}`", message_thread_id=thread_id)
        return

    markup = types.InlineKeyboardMarkup(row_width=1)
    for pack_id, titulo in resultados:
        markup.add(types.InlineKeyboardButton(f"⭐ {titulo}", callback_data=f"pack_{pack_id}"))
    markup.add(types.InlineKeyboardButton("🔙 Volver al Menú", callback_data="menu_principal"))
    
    enviar_temporal(message.chat.id, f"🔍 **Resultados para:** `{query}`", markup, message_thread_id=thread_id)

# --- CAPTURA DE MENSAJES Y ARCHIVOS ---
@bot.message_handler(func=lambda m: m.chat.type in ['group', 'supergroup'], content_types=['text', 'document', 'video', 'audio', 'photo'])
def capturar_grupo_exclusivo(message):
    if not validar_o_castigar_grupo(message.chat.id, message.chat.title or "Grupo"):
        return

    if message.text and message.text.lower().strip() in ["hvn94", "!hvn94", "#hvn94", "/hvn94"]:
        cmd_start_hvn94(message)
        return

    f_id, f_nombre, f_tipo = extraer_info_archivo(message)
    if f_id:
        caption = message.caption or ""
        registrar_archivo_o_pack(f_id, f_nombre, f_tipo, caption, message.chat.id, message.message_id)

# --- CALLBACKS INTERACTIVOS ---
@bot.callback_query_handler(func=lambda call: True)
def callbacks(call):
    user_id = call.from_user.id
    data = call.data
    thread_id = getattr(call.message, 'message_thread_id', None)

    if data == "noop":
        bot.answer_callback_query(call.id)
        return

    if not es_miembro_autorizado(user_id):
        bot.answer_callback_query(call.id, "⛔ Acceso Denegado.", show_alert=True)
        return

    if data == "menu_principal":
        try:
            bot.edit_message_caption(BIOGRAFIA_TEXTO, call.message.chat.id, call.message.message_id, reply_markup=teclado_principal_flotante(), parse_mode="Markdown")
        except Exception:
            try:
                bot.edit_message_text(BIOGRAFIA_TEXTO, call.message.chat.id, call.message.message_id, reply_markup=teclado_principal_flotante(), parse_mode="Markdown")
            except Exception:
                pass
        bot.answer_callback_query(call.id)
        return

    if data == "menu_catalogo":
        total = obtener_total_packs()
        if total == 0:
            bot.answer_callback_query(call.id, "📂 Aún no hay archivos registrados.", show_alert=True)
            return
        markup, _ = crear_markup_catalogo(1)
        txt = f"📂 **Catálogo Disponible** ({total} elementos):"
        try:
            bot.edit_message_caption(txt, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        except Exception:
            bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        bot.answer_callback_query(call.id)
        return

    if data == "menu_recientes":
        packs = obtener_packs_pagina(pagina=1, limite=6)
        if not packs:
            bot.answer_callback_query(call.id, "📂 No hay archivos recientes.", show_alert=True)
            return
        markup = types.InlineKeyboardMarkup(row_width=1)
        for pack_id, titulo in packs:
            markup.add(types.InlineKeyboardButton(f"⭐ {titulo}", callback_data=f"pack_{pack_id}"))
        markup.add(types.InlineKeyboardButton("🔙 Volver al Menú", callback_data="menu_principal"))
        txt = "🕒 **Últimas subidas:**"
        try:
            bot.edit_message_caption(txt, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        except Exception:
            bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        bot.answer_callback_query(call.id)
        return

    if data == "menu_ayuda":
        txt = (
            "💡 **Instrucciones Rápidas:**\n\n"
            "1. Toca en **Catálogo** para navegar las páginas.\n"
            "2. Para buscar rápido escribe: `/buscar [nombre]`.\n"
            f"3. Todo se autodestruye en {TIEMPO_AUTO_ELIMINAR}s."
        )
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 Volver al Menú", callback_data="menu_principal"))
        try:
            bot.edit_message_caption(txt, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        except Exception:
            bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        bot.answer_callback_query(call.id)
        return

    if data == "menu_buscar":
        bot.answer_callback_query(call.id, "✍️ Escribe en el chat: /buscar [nombre]", show_alert=True)
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

        texto = (
            f"🏷 **{titulo}**\n\n"
            f"📦 **Archivos incluidos ({len(archivos)}):**\n{nombres_archivos}\n\n"
            f"📋 **Ficha Técnica:**\n{descripcion}\n\n"
            f"⏱ _Auto-eliminación en {TIEMPO_AUTO_ELIMINAR}s._"
        )
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(types.InlineKeyboardButton(f"⬇️ Descargar Pack ({len(archivos)} archivo/s)", callback_data=f"descargar_pack_{pack_id}"))
        
        if user_id == ADMIN_ID:
            markup.add(types.InlineKeyboardButton("🗑️ Eliminar Pack", callback_data=f"borrar_pack_{pack_id}"))
        
        markup.add(types.InlineKeyboardButton("🔙 Volver al Catálogo", callback_data="menu_catalogo"))

        enviar_temporal(call.message.chat.id, texto, markup, message_thread_id=thread_id)
        bot.answer_callback_query(call.id)

    elif data.startswith("descargar_pack_"):
        pack_id = int(data.replace("descargar_pack_", ""))
        entregar_pack(call.message.chat.id, pack_id, thread_id)
        bot.answer_callback_query(call.id)

    elif data.startswith("borrar_pack_"):
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "⛔ No autorizado.", show_alert=True)
            return

        pack_id = int(data.replace("borrar_pack_", ""))
        eliminar_pack_manual(pack_id)
        bot.answer_callback_query(call.id, "🗑️ Pack eliminado.")
        try:
            bot.edit_message_text("✅ **Elemento eliminado de la base de datos.**", call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        except Exception:
            pass

def entregar_pack(chat_id, pack_id, thread_id=None):
    detalles = obtener_detalles_pack(pack_id)
    if not detalles:
        return

    (titulo, _, _), archivos = detalles
    msg_ids = []

    kwargs = {"parse_mode": "Markdown"}
    if thread_id:
        kwargs["message_thread_id"] = thread_id

    alerta = bot.send_message(
        chat_id, 
        f"⏳ Enviando `{titulo}` ({len(archivos)} archivo/s)...\n**⚠️ Se auto-eliminará en {TIEMPO_AUTO_ELIMINAR} segundos.**",
        **kwargs
    )
    msg_ids.append(alerta.message_id)

    for f_id, _, tipo in archivos:
        file_kwargs = {}
        if thread_id:
            file_kwargs["message_thread_id"] = thread_id

        if tipo == "document":
            m = bot.send_document(chat_id, f_id, **file_kwargs)
        elif tipo == "video":
            m = bot.send_video(chat_id, f_id, **file_kwargs)
        elif tipo == "audio":
            m = bot.send_audio(chat_id, f_id, **file_kwargs)
        elif tipo == "photo":
            m = bot.send_photo(chat_id, f_id, **file_kwargs)
        else:
            m = bot.send_document(chat_id, f_id, **file_kwargs)
        msg_ids.append(m.message_id)

    auto_destruir_mensaje(chat_id, msg_ids, delay=TIEMPO_AUTO_ELIMINAR)

# --- WEBHOOK ENTRYPOINT ---
@app.route("/api/index", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        return "Bot activo correctamente en Vercel", 200

    try:
        json_data = request.get_json(silent=True)
        if json_data:
            update = telebot.types.Update.de_json(json_data)
            bot.process_new_updates([update])
        return "OK", 200
    except Exception:
        return "OK", 200
