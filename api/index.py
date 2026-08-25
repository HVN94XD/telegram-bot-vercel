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
TIEMPO_AUTO_ELIMINAR = 60
ITEMS_POR_PAGINA = 8
# =================================================

app = Flask(__name__)
bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

ultimo_pack_id = None
lock_db = threading.Lock()
GRUPOS_REGISTRADOS = set()
DB_PATH = "/tmp/archivos.db"

# --- BASE DE DATOS LOCAL (SQLite en /tmp) ---
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

    cursor.execute("PRAGMA table_info(miembros_autorizados)")
    columnas = [col[1] for col in cursor.fetchall()]
    if "nombre" not in columnas:
        cursor.execute("ALTER TABLE miembros_autorizados ADD COLUMN nombre TEXT DEFAULT 'Usuario'")
    if "origen" not in columnas:
        cursor.execute("ALTER TABLE miembros_autorizados ADD COLUMN origen TEXT DEFAULT 'Manual/Admin'")
    conn.commit()

    cursor.execute("SELECT chat_id FROM grupos_vinculados")
    for row in cursor.fetchall():
        GRUPOS_REGISTRADOS.add(row[0])

    conn.close()

init_db()

# --- AUTODESTRUCCIÓN Y LIMPIEZA ---
def auto_destruir_mensaje(chat_id, message_ids, delay=60):
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
    except Exception as e:
        print("ERROR ENVIAR TEMPORAL:", e)
        return None

# --- REGLA ESTRICTA DE GRUPOS VÁLIDOS ---
def registrar_grupo_en_bd(chat_id, titulo):
    # REGLA: Verificar obligatoriamente que TÚ (ADMIN_ID) estés en este grupo y seas miembro activo/admin.
    try:
        admin_chat_member = bot.get_chat_member(chat_id, ADMIN_ID)
        # Si tú estás en el grupo (creador, admin, miembro o restringido, pero estás ahí)
        if admin_chat_member.status in ['creator', 'administrator', 'member', 'restricted']:
            if chat_id not in GRUPOS_REGISTRADOS:
                GRUPOS_REGISTRADOS.add(chat_id)
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                cursor.execute("INSERT OR IGNORE INTO grupos_vinculados (chat_id, titulo) VALUES (?, ?)", (chat_id, titulo))
                conn.commit()
                conn.close()
            return True
    except Exception:
        # Si la API da error (ej: el bot no puede ver miembros o tú no estás ahí), se rechaza el grupo de inmediato
        pass
    
    # Si metieron al bot a un grupo donde TÚ NO ESTÁS, nos aseguramos de borrarlo de la lista si existía
    if chat_id in GRUPOS_REGISTRADOS:
        GRUPOS_REGISTRADOS.remove(chat_id)
    return False

def autorizar_usuario(user_id, nombre, username, origen="Manual/Admin"):
    with lock_db:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO miembros_autorizados (user_id, nombre, username, origen)
            VALUES (?, ?, ?, ?)
        """, (user_id, nombre or "Usuario", username or "SinAlias", origen))
        conn.commit()
        conn.close()

def eliminar_usuario_autorizado(user_id):
    with lock_db:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM miembros_autorizados WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()

def es_miembro_autorizado(user_id):
    if user_id == ADMIN_ID:
        return True

    if not GRUPOS_REGISTRADOS:
        return False

    # REGLA ESTRICTA: El usuario solo pasa si está en un grupo válido donde TÚ también estás registrado
    for grupo_id in list(GRUPOS_REGISTRADOS):
        try:
            # Primero validamos que TÚ sigas estando en el grupo
            yo_en_grupo = bot.get_chat_member(grupo_id, ADMIN_ID)
            if yo_en_grupo.status not in ['creator', 'administrator', 'member', 'restricted']:
                continue # Si tú ya no estás ahí, ese grupo queda invalidado

            # Luego validamos si el usuario pertenece a ese grupo legítimo
            m = bot.get_chat_member(grupo_id, user_id)
            if m.status in ['creator', 'administrator', 'member', 'restricted']:
                return True
        except Exception:
            continue
    return False

def obtener_lista_miembros():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, nombre, username, origen, fecha FROM miembros_autorizados ORDER BY fecha DESC")
    miembros = cursor.fetchall()
    conn.close()
    return miembros

def notificar_y_bloquear(message):
    borrar_comando_usuario(message)
    user = message.from_user
    username_str = f"@{user.username}" if user.username else "Sin @"
    nombre_str = user.first_name or "Desconocido"
    apellido_str = user.last_name or ""
    nombre_completo = f"{nombre_str} {apellido_str}".strip()
    req_uid = user.id
    idioma = user.language_code or "No disponible"
    es_premium = "⭐ Sí" if getattr(user, 'is_premium', False) else "No"
    hora_actual = time.strftime("%Y-%m-%d %H:%M:%S")

    thread_id = getattr(message, 'message_thread_id', None)

    texto_usuario = (
        "🚫 **ACCESO RESTRINGIDO**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Este bot es de uso **exclusivo para miembros autorizados** en grupos donde el administrador está presente.\n\n"
        f"📩 **Tu acceso ha sido denegado automáticamente.**"
    )
    enviar_temporal(message.chat.id, texto_usuario, message_thread_id=thread_id)

    # Opcional: Avisarte al privado del intento de intrusión en grupo extraño
    txt_admin = (
        "⚠️ **[ALERTA DE SEGURIDAD] INTENTO DE ACCESO NO VÁLIDO**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 **Usuario:** {nombre_completo} ({username_str})\n"
        f"🆔 **ID:** `{req_uid}`\n"
        f"📍 Un usuario intentó usar el bot en un grupo o chat sin autorización o donde no estás vinculado."
    )
    try:
        bot.send_message(ADMIN_ID, txt_admin, parse_mode="Markdown")
    except Exception:
        pass

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

# --- BOTONES FLOTANTES (INLINE) PRINCIPALES ---
def teclado_principal_flotante(es_admin=False):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📁 Catálogo Completo", callback_data="menu_catalogo"),
        types.InlineKeyboardButton("🕒 Últimas Subidas", callback_data="menu_recientes"),
        types.InlineKeyboardButton("🔍 Buscar Archivo", callback_data="menu_buscar"),
        types.InlineKeyboardButton("ℹ️ Ayuda", callback_data="menu_ayuda")
    )
    if es_admin:
        markup.add(types.InlineKeyboardButton("👥 Gestionar Miembros", callback_data="menu_miembros"))
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

# --- MANEJADOR DE COMANDOS /START Y /HVN94 ---
@bot.message_handler(commands=['start', 'hvn94', 'HVN94'])
def cmd_start_hvn94(message):
    borrar_comando_usuario(message)
    thread_id = getattr(message, 'message_thread_id', None)

    if message.chat.type in ['group', 'supergroup']:
        # Solo se registra si TÚ estás en el grupo
        es_grupo_valido = registrar_grupo_en_bd(message.chat.id, message.chat.title or "Grupo Privado")
        if not es_grupo_valido and message.from_user.id != ADMIN_ID:
            return # Ignorar por completo si es un grupo ajeno donde tú no estás

    if not es_miembro_autorizado(message.from_user.id):
        notificar_y_bloquear(message)
        return

    es_admin = (message.from_user.id == ADMIN_ID)
    texto = (
        "👋 **¡Panel HVN94 Convocado!**\n\n"
        "Usa los botones flotantes de abajo para navegar, o busca escribiendo:\n"
        "👉 `/buscar [palabra]` (Ej: `/buscar izzi`)\n\n"
        f"⏱ _Los mensajes y descargas se autodestruyen en {TIEMPO_AUTO_ELIMINAR}s._"
    )
    enviar_temporal(message.chat.id, texto, markup=teclado_principal_flotante(es_admin), message_thread_id=thread_id)

# --- COMANDO DE BÚSQUEDA DIRECTA ---
@bot.message_handler(commands=['buscar', 'search'])
def cmd_buscar(message):
    borrar_comando_usuario(message)
    thread_id = getattr(message, 'message_thread_id', None)
    
    if not es_miembro_autorizado(message.from_user.id):
        notificar_y_bloquear(message)
        return

    texto_parts = message.text.split(maxsplit=1)
    if len(texto_parts) < 2:
        enviar_temporal(message.chat.id, "⚠️ **Uso correcto:** Escribe `/buscar [nombre]`\nEjemplo: `/buscar izzi`", message_thread_id=thread_id)
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

# --- CAPTURA DE MENSAJES Y ARCHIVOS EN GRUPOS ---
@bot.message_handler(func=lambda m: m.chat.type in ['group', 'supergroup'], content_types=['text', 'document', 'video', 'audio', 'photo'])
def capturar_grupo_exclusivo(message):
    # REGLA: Si TÚ no estás en este grupo, el bot lo ignora por completo y no guarda nada
    es_grupo_valido = registrar_grupo_en_bd(message.chat.id, message.chat.title or "Grupo Privado")
    if not es_grupo_valido:
        return

    if message.text and message.text.lower().strip() in ["hvn94", "!hvn94", "#hvn94", "/hvn94"]:
        cmd_start_hvn94(message)
        return

    f_id, f_nombre, f_tipo = extraer_info_archivo(message)
    if f_id:
        caption = message.caption or ""
        registrar_archivo_o_pack(f_id, f_nombre, f_tipo, caption, message.chat.id, message.message_id)

@bot.channel_post_handler(content_types=['document', 'video', 'audio', 'photo', 'text'])
def handle_channel(message):
    es_grupo_valido = registrar_grupo_en_bd(message.chat.id, message.chat.title or "Canal")
    if not es_grupo_valido:
        return
    f_id, f_nombre, f_tipo = extraer_info_archivo(message)
    if f_id:
        caption = message.caption or ""
        registrar_archivo_o_pack(f_id, f_nombre, f_tipo, caption, message.chat.id, message.message_id)

# --- CALLBACKS INTERACTIVOS Y MENÚS FLOTANTES ---
@bot.callback_query_handler(func=lambda call: True)
def callbacks(call):
    user_id = call.from_user.id
    data = call.data
    thread_id = getattr(call.message, 'message_thread_id', None)
    es_admin = (user_id == ADMIN_ID)

    if data == "noop":
        bot.answer_callback_query(call.id)
        return

    if data == "menu_principal":
        texto = (
            "👋 **¡Panel HVN94 Convocado!**\n\n"
            "Usa los botones flotantes de abajo para navegar, o busca escribiendo:\n"
            "👉 `/buscar [palabra]` (Ej: `/buscar izzi`)\n\n"
            f"⏱ _Los mensajes y descargas se autodestruyen en {TIEMPO_AUTO_ELIMINAR}s._"
        )
        try:
            bot.edit_message_text(texto, call.message.chat.id, call.message.message_id, reply_markup=teclado_principal_flotante(es_admin), parse_mode="Markdown")
        except Exception:
            pass
        bot.answer_callback_query(call.id)
        return

    if data == "menu_catalogo":
        if not es_miembro_autorizado(user_id):
            bot.answer_callback_query(call.id, "⛔ Acceso Denegado.", show_alert=True)
            return
        total = obtener_total_packs()
        if total == 0:
            bot.answer_callback_query(call.id, "📂 Aún no hay archivos registrados.", show_alert=True)
            return
        markup, _ = crear_markup_catalogo(1)
        try:
            bot.edit_message_text(f"📂 **Catálogo Disponible** ({total} elementos):", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        except Exception:
            pass
        bot.answer_callback_query(call.id)
        return

    if data == "menu_recientes":
        if not es_miembro_autorizado(user_id):
            bot.answer_callback_query(call.id, "⛔ Acceso Denegado.", show_alert=True)
            return
        packs = obtener_packs_pagina(pagina=1, limite=6)
        if not packs:
            bot.answer_callback_query(call.id, "📂 No hay archivos recientes.", show_alert=True)
            return
        markup = types.InlineKeyboardMarkup(row_width=1)
        for pack_id, titulo in packs:
            markup.add(types.InlineKeyboardButton(f"⭐ {titulo}", callback_data=f"pack_{pack_id}"))
        markup.add(types.InlineKeyboardButton("🔙 Volver al Menú", callback_data="menu_principal"))
        try:
            bot.edit_message_text("🕒 **Últimas subidas:**", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        except Exception:
            pass
        bot.answer_callback_query(call.id)
        return

    if data == "menu_ayuda":
        if not es_miembro_autorizado(user_id):
            bot.answer_callback_query(call.id, "⛔ Acceso Denegado.", show_alert=True)
            return
        texto = (
            "💡 **Instrucciones:**\n\n"
            "1. Usa los botones flotantes para abrir Catálogo o Nuevas Subidas.\n"
            "2. Para buscar un archivo directamente escribe: `/buscar [nombre]`.\n"
            f"3. Los archivos y mensajes se autodestruyen en {TIEMPO_AUTO_ELIMINAR}s."
        )
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 Volver al Menú", callback_data="menu_principal"))
        try:
            bot.edit_message_text(texto, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        except Exception:
            pass
        bot.answer_callback_query(call.id)
        return

    if data == "menu_buscar":
        bot.answer_callback_query(call.id, "✍️ Escribe en el chat: /buscar seguido de tu palabra", show_alert=True)
        return

    if data == "menu_miembros":
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "⛔ No autorizado.", show_alert=True)
            return
        miembros = obtener_lista_miembros()
        if not miembros:
            bot.answer_callback_query(call.id, "📂 No hay miembros autorizados.", show_alert=True)
            return
        for u_id, nom, user_n, origen, fecha in miembros:
            alias = f"@{user_n}" if user_n != "SinAlias" else "Sin @"
            txt_m = (
                f"👤 **Nombre:** {nom}\n"
                f"🔗 **Usuario:** {alias}\n"
                f"🆔 **ID:** `{u_id}`\n"
                f"📍 **Origen:** `{origen}`\n"
                f"📅 **Fecha:** `{fecha}`"
            )
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🗑️ Revocar / Eliminar Acceso", callback_data=f"admin_eliminar_u_{u_id}"))
            enviar_temporal(call.message.chat.id, txt_m, markup, message_thread_id=thread_id)
        bot.answer_callback_query(call.id, "👥 Lista de miembros enviada.")
        return

    if not es_miembro_autorizado(user_id):
        bot.answer_callback_query(call.id, "⛔ Acceso Denegado. No estás en un grupo válido con el administrador.", show_alert=True)
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
            markup.add(types.InlineKeyboardButton("🗑️ Eliminar de la Base de Datos y Grupo", callback_data=f"borrar_pack_{pack_id}"))
        
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
        bot.answer_callback_query(call.id, "🗑️ Eliminado de la Base de Datos y del Grupo.")
        try:
            bot.edit_message_text("✅ **Elemento eliminado de la base de datos y del chat de origen.**", call.message.chat.id, call.message.message_id, parse_mode="Markdown")
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
    except Exception as e:
        return "OK", 200
