from flask import Flask, request
import os
import re
import sqlite3
import threading
import time
import telebot

# =========================================================
# VERCEL / FLASK
# =========================================================

app = Flask(__name__)

# =========================================================
# CONFIGURACIÓN
# =========================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

CONTACTO_ADMIN = "@HVN94"
TIEMPO_AUTO_ELIMINAR = 60
ITEMS_POR_PAGINA = 8

if not BOT_TOKEN:
    raise RuntimeError("Falta la variable BOT_TOKEN")

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

ultimo_pack_id = None

lock_db = threading.Lock()

GRUPOS_REGISTRADOS = set()

DB_PATH = "/tmp/archivos.db"


# =========================================================
# BASE DE DATOS
# =========================================================

def init_db():
    conn = sqlite3.connect(DB_PATH)

    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS packs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            titulo TEXT NOT NULL,
            descripcion TEXT,
            fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pack_archivos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pack_id INTEGER,
            file_id TEXT NOT NULL,
            nombre_archivo TEXT,
            tipo TEXT,
            chat_id INTEGER,
            message_id INTEGER,
            FOREIGN KEY (pack_id)
                REFERENCES packs(id)
                ON DELETE CASCADE
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


# =========================================================
# UTILIDADES
# =========================================================

def auto_destruir_mensaje(chat_id, message_ids, delay=60):

    def tarea():
        time.sleep(delay)

        for msg_id in message_ids:
            try:
                bot.delete_message(chat_id, msg_id)
            except Exception as e:
                print("No se pudo eliminar mensaje:", e)

    threading.Thread(
        target=tarea,
        daemon=True
    ).start()


def enviar_temporal(
    chat_id,
    texto,
    markup=None,
    parse_mode="Markdown"
):

    try:

        msg = bot.send_message(
            chat_id,
            texto,
            reply_markup=markup,
            parse_mode=parse_mode
        )

        auto_destruir_mensaje(
            chat_id,
            [msg.message_id],
            TIEMPO_AUTO_ELIMINAR
        )

        return msg

    except Exception as e:

        print("ERROR send_message:", e)

        return None


# =========================================================
# ACCESOS
# =========================================================

def registrar_grupo_en_bd(chat_id, titulo):

    if chat_id in GRUPOS_REGISTRADOS:
        return

    try:

        GRUPOS_REGISTRADOS.add(chat_id)

        conn = sqlite3.connect(DB_PATH)

        cursor = conn.cursor()

        cursor.execute("""
            INSERT OR IGNORE INTO grupos_vinculados
            (chat_id, titulo)
            VALUES (?, ?)
        """, (
            chat_id,
            titulo
        ))

        conn.commit()
        conn.close()

    except Exception as e:

        print("ERROR registrando grupo:", e)


def es_miembro_autorizado(user_id):

    if user_id == ADMIN_ID:
        return True

    try:

        conn = sqlite3.connect(DB_PATH)

        cursor = conn.cursor()

        cursor.execute("""
            SELECT user_id
            FROM miembros_autorizados
            WHERE user_id = ?
        """, (user_id,))

        resultado = cursor.fetchone()

        conn.close()

        return resultado is not None

    except Exception as e:

        print("ERROR comprobando autorización:", e)

        return False


# =========================================================
# TÍTULOS
# =========================================================

def limpiar_titulo(texto):

    if not texto:
        return None

    patron_ignorar = re.compile(
        r'^(\.(obmx?|svb|opk|espk|spk|ice|exe|zip|rar)'
        r'|obmx?|svb|opk|espk|spk|ice|exe'
        r'|\d+(\.\d+)?\s*(kb|mb|gb|b))\b',
        re.IGNORECASE
    )

    lineas = [
        l.strip()
        for l in texto.split("\n")
        if l.strip()
    ]

    for linea in lineas:

        if patron_ignorar.match(linea):
            continue

        linea_limpia = re.sub(
            r'[🎖️⭐🎖🔥📌🚀📁📄💥✨\t]',
            '',
            linea
        ).strip()

        if len(linea_limpia) > 2:
            return linea_limpia

    return lineas[0] if lineas else None


# =========================================================
# REGISTRAR ARCHIVOS
# =========================================================

def registrar_archivo_o_pack(
    file_id,
    nombre_archivo,
    tipo,
    caption,
    chat_id,
    message_id
):

    global ultimo_pack_id

    with lock_db:

        conn = sqlite3.connect(DB_PATH)

        cursor = conn.cursor()

        tiene_caption = bool(
            caption and len(caption.strip()) > 5
        )

        if tiene_caption:

            titulo = (
                limpiar_titulo(caption)
                or nombre_archivo
            )

            descripcion = caption.strip()

            cursor.execute("""
                INSERT INTO packs
                (titulo, descripcion)
                VALUES (?, ?)
            """, (
                titulo,
                descripcion
            ))

            pack_id = cursor.lastrowid

            ultimo_pack_id = pack_id

        else:

            if ultimo_pack_id is not None:

                pack_id = ultimo_pack_id

            else:

                titulo = nombre_archivo

                cursor.execute("""
                    INSERT INTO packs
                    (titulo, descripcion)
                    VALUES (?, ?)
                """, (
                    titulo,
                    "Sin descripción"
                ))

                pack_id = cursor.lastrowid

                ultimo_pack_id = pack_id

        cursor.execute("""
            INSERT INTO pack_archivos
            (
                pack_id,
                file_id,
                nombre_archivo,
                tipo,
                chat_id,
                message_id
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            pack_id,
            file_id,
            nombre_archivo,
            tipo,
            chat_id,
            message_id
        ))

        conn.commit()

        conn.close()

        print(
            f"ARCHIVO REGISTRADO: "
            f"pack={pack_id} "
            f"archivo={nombre_archivo}"
        )


# =========================================================
# CONSULTAS
# =========================================================

def obtener_total_packs():

    conn = sqlite3.connect(DB_PATH)

    cursor = conn.cursor()

    cursor.execute(
        "SELECT COUNT(*) FROM packs"
    )

    total = cursor.fetchone()[0]

    conn.close()

    return total


def obtener_packs_pagina(
    pagina=1,
    limite=ITEMS_POR_PAGINA
):

    offset = (pagina - 1) * limite

    conn = sqlite3.connect(DB_PATH)

    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, titulo
        FROM packs
        ORDER BY id DESC
        LIMIT ?
        OFFSET ?
    """, (
        limite,
        offset
    ))

    resultado = cursor.fetchall()

    conn.close()

    return resultado


def obtener_detalles_pack(pack_id):

    conn = sqlite3.connect(DB_PATH)

    cursor = conn.cursor()

    cursor.execute("""
        SELECT titulo, descripcion, fecha
        FROM packs
        WHERE id = ?
    """, (pack_id,))

    pack = cursor.fetchone()

    if not pack:

        conn.close()

        return None

    cursor.execute("""
        SELECT
            file_id,
            nombre_archivo,
            tipo
        FROM pack_archivos
        WHERE pack_id = ?
    """, (pack_id,))

    archivos = cursor.fetchall()

    conn.close()

    return pack, archivos


# =========================================================
# EXTRAER ARCHIVOS DE TELEGRAM
# =========================================================

def extraer_info_archivo(message):

    if message.document:

        return (
            message.document.file_id,
            message.document.file_name or "Archivo",
            "document"
        )

    if message.video:

        return (
            message.video.file_id,
            message.video.file_name or "Video",
            "video"
        )

    if message.audio:

        return (
            message.audio.file_id,
            message.audio.file_name or "Audio",
            "audio"
        )

    if message.photo:

        return (
            message.photo[-1].file_id,
            "Foto",
            "photo"
        )

    return None, None, None


# =========================================================
# TECLADOS
# =========================================================

def teclado_principal():

    markup = telebot.types.ReplyKeyboardMarkup(
        resize_keyboard=True,
        row_width=2
    )

    markup.add(
        telebot.types.KeyboardButton(
            "📁 Catálogo Completo"
        ),
        telebot.types.KeyboardButton(
            "🕒 Últimas Subidas"
        ),
        telebot.types.KeyboardButton(
            "ℹ️ Ayuda"
        )
    )

    return markup


def crear_markup_catalogo(pagina=1):

    total = obtener_total_packs()

    total_paginas = max(
        1,
        (total + ITEMS_POR_PAGINA - 1)
        // ITEMS_POR_PAGINA
    )

    packs = obtener_packs_pagina(pagina)

    markup = telebot.types.InlineKeyboardMarkup(
        row_width=1
    )

    for pack_id, titulo in packs:

        markup.add(
            telebot.types.InlineKeyboardButton(
                f"⭐ {titulo}",
                callback_data=f"pack_{pack_id}"
            )
        )

    botones_nav = []

    if pagina > 1:

        botones_nav.append(
            telebot.types.InlineKeyboardButton(
                "⬅️ Anterior",
                callback_data=f"pag_{pagina - 1}"
            )
        )

    botones_nav.append(
        telebot.types.InlineKeyboardButton(
            f"📄 {pagina}/{total_paginas}",
            callback_data="noop"
        )
    )

    if pagina < total_paginas:

        botones_nav.append(
            telebot.types.InlineKeyboardButton(
                "Siguiente ➡️",
                callback_data=f"pag_{pagina + 1}"
            )
        )

    markup.row(*botones_nav)

    return markup


# =========================================================
# START
# =========================================================

@bot.message_handler(
    commands=['start', 'hvn94']
)
def cmd_start_hvn94(message):

    print(
        "START recibido de:",
        message.from_user.id
    )

    if message.chat.type in [
        'group',
        'supergroup'
    ]:

        registrar_grupo_en_bd(
            message.chat.id,
            message.chat.title or "Grupo"
        )

    if not es_miembro_autorizado(
        message.from_user.id
    ):

        enviar_temporal(
            message.chat.id,
            "⛔ **Acceso Restringido**.\n\n"
            "No estás autorizado para usar este bot."
        )

        return

    enviar_temporal(
        message.chat.id,
        "👋 **¡Panel HVN94 Convocado!**\n\n"
        "Usa los botones de abajo para navegar "
        "por el catálogo.",
        markup=teclado_principal()
    )


# =========================================================
# CATÁLOGO
# =========================================================

@bot.message_handler(
    func=lambda msg:
        msg.text == "📁 Catálogo Completo"
        or msg.text == "/list"
)
def ver_catalogo(message):

    if not es_miembro_autorizado(
        message.from_user.id
    ):
        return

    total = obtener_total_packs()

    if total == 0:

        enviar_temporal(
            message.chat.id,
            "📂 Aún no hay archivos registrados."
        )

        return

    markup = crear_markup_catalogo(1)

    enviar_temporal(
        message.chat.id,
        f"📂 **Catálogo Disponible** ({total} elementos):",
        markup
    )


# =========================================================
# AYUDA
# =========================================================

@bot.message_handler(
    func=lambda msg:
        msg.text == "ℹ️ Ayuda"
)
def ayuda(message):

    if not es_miembro_autorizado(
        message.from_user.id
    ):
        return

    enviar_temporal(
        message.chat.id,
        "ℹ️ **Ayuda HVN94**\n\n"
        "📁 Catálogo Completo: ver todos los packs.\n"
        "🕒 Últimas Subidas: próximamente.\n\n"
        f"👤 Contacto: {CONTACTO_ADMIN}"
    )


# =========================================================
# CAPTURAR ARCHIVOS DE GRUPOS
# =========================================================

@bot.message_handler(
    content_types=[
        'document',
        'video',
        'audio',
        'photo'
    ],
    func=lambda msg:
        msg.chat.type in [
            'group',
            'supergroup'
        ]
)
def capturar_grupo_exclusivo(message):

    try:

        registrar_grupo_en_bd(
            message.chat.id,
            message.chat.title or "Grupo"
        )

        f_id, f_nombre, f_tipo = (
            extraer_info_archivo(message)
        )

        if f_id:

            registrar_archivo_o_pack(
                f_id,
                f_nombre,
                f_tipo,
                message.caption or "",
                message.chat.id,
                message.message_id
            )

    except Exception as e:

        print(
            "ERROR capturando archivo:",
            e
        )


# =========================================================
# CALLBACKS
# =========================================================

@bot.callback_query_handler(
    func=lambda call: True
)
def callbacks(call):

    try:

        user_id = call.from_user.id
        data = call.data

        if data == "noop":

            bot.answer_callback_query(
                call.id
            )

            return

        if not es_miembro_autorizado(user_id):

            bot.answer_callback_query(
                call.id,
                "⛔ Acceso Denegado.",
                show_alert=True
            )

            return

        # -------------------------
        # PAGINACIÓN
        # -------------------------

        if data.startswith("pag_"):

            pagina = int(
                data.replace("pag_", "")
            )

            markup = crear_markup_catalogo(
                pagina
            )

            try:

                bot.edit_message_reply_markup(
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=markup
                )

            except Exception as e:

                print(
                    "ERROR editando catálogo:",
                    e
                )

            bot.answer_callback_query(
                call.id
            )

            return

        # -------------------------
        # VER PACK
        # -------------------------

        if data.startswith("pack_"):

            pack_id = int(
                data.replace("pack_", "")
            )

            detalles = obtener_detalles_pack(
                pack_id
            )

            if not detalles:

                bot.answer_callback_query(
                    call.id,
                    "El elemento ya no existe."
                )

                return

            (titulo, descripcion, _), archivos = (
                detalles
            )

            nombres_archivos = "\n".join(
                [
                    f"• `{a[1]}`"
                    for a in archivos
                ]
            )

            texto = (
                f"🏷 **{titulo}**\n\n"
                f"📦 **Archivos ({len(archivos)}):**\n"
                f"{nombres_archivos}\n\n"
                f"📋 **Descripción:**\n"
                f"{descripcion}"
            )

            markup = (
                telebot.types.InlineKeyboardMarkup()
            )

            markup.add(
                telebot.types.InlineKeyboardButton(
                    "⬇️ Descargar Pack",
                    callback_data=f"descargar_{pack_id}"
                )
            )

            enviar_temporal(
                call.message.chat.id,
                texto,
                markup
            )

            bot.answer_callback_query(
                call.id
            )

            return

        # -------------------------
        # DESCARGAR
        # -------------------------

        if data.startswith("descargar_"):

            pack_id = int(
                data.replace("descargar_", "")
            )

            detalles = obtener_detalles_pack(
                pack_id
            )

            if not detalles:

                bot.answer_callback_query(
                    call.id,
                    "Pack no encontrado."
                )

                return

            _, archivos = detalles

            for file_id, nombre, tipo in archivos:

                try:

                    if tipo == "document":

                        bot.send_document(
                            call.message.chat.id,
                            file_id
                        )

                    elif tipo == "video":

                        bot.send_video(
                            call.message.chat.id,
                            file_id
                        )

                    elif tipo == "audio":

                        bot.send_audio(
                            call.message.chat.id,
                            file_id
                        )

                    elif tipo == "photo":

                        bot.send_photo(
                            call.message.chat.id,
                            file_id
                        )

                except Exception as e:

                    print(
                        "ERROR enviando archivo:",
                        e
                    )

            bot.answer_callback_query(
                call.id,
                "Pack enviado."
            )

    except Exception as e:

        print(
            "ERROR callback:",
            e
        )


# =========================================================
# WEBHOOK VERCEL
# =========================================================

@app.route("/", methods=["GET", "POST"])
def webhook():

    if request.method == "GET":
        return "Bot activo correctamente en Vercel", 200

    try:
        json_data = request.get_json(silent=True)

        print("WEBHOOK RECIBIDO:", json_data)

        if json_data:
            update = telebot.types.Update.de_json(json_data)
            bot.process_new_updates([update])

        return "OK", 200

    except Exception as e:
        print("ERROR WEBHOOK:", repr(e))
        return "OK", 200
