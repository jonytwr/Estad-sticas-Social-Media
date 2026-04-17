"""
bot.py — Bot de Telegram para asignación de autores
Flujo: notificación diaria → menú de botones por post → confirmación → dashboard listo
"""

import os
import logging
import asyncio
from datetime import date, timedelta
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)

BOT_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID     = os.getenv("TELEGRAM_CHAT_ID", "")   # Tu chat_id personal
APP_URL     = os.getenv("APP_URL", "http://localhost:8000")

# Lista de autores del equipo
AUTHORS = [
    "Jony", "Fernando", "Vicky", "María José",
    "Cecilia", "Luís", "Elena", "Amparo",
]

# Estado en memoria: {chat_id: {"report_date": date, "pending_posts": [...], "current_index": 0}}
_state: dict = {}


# ─── Construcción de la aplicación ───────────────────────────────────────────

def build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("estado",  cmd_estado))
    app.add_handler(CommandHandler("pendientes", cmd_pendientes))
    app.add_handler(CallbackQueryHandler(handle_author_selection, pattern=r"^author\|"))
    app.add_handler(CallbackQueryHandler(handle_skip,             pattern=r"^skip\|"))
    return app


# ─── Comandos ────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"👋 Bot de @defensagob activo.\n"
        f"Tu chat ID es: `{chat_id}`\n\n"
        f"Comandos disponibles:\n"
        f"/estado — ver resumen del día más reciente\n"
        f"/pendientes — revisar posts sin autor asignado",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_estado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from database import SessionLocal, get_all_reports
    db = SessionLocal()
    try:
        reports = get_all_reports(db)
        if not reports:
            await update.message.reply_text("No hay informes en la base de datos todavía.")
            return
        r = reports[0]  # El más reciente
        pending = len([p for p in r.posts if p.author in (None, "", "PENDIENTE", "[PENDIENTE]")])
        status_icon = "✅" if r.authors_complete else f"⏳ ({pending} pendientes)"
        text = (
            f"📋 *Último informe:* {r.date}\n"
            f"Posts: {len(r.posts)} | Score medio: {r.score_medio or 'N/D'}\n"
            f"Autores: {status_icon}\n"
            f"Seguidores: {r.followers or 'N/D'}\n\n"
            f"🔗 [Ver dashboard]({APP_URL})"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    finally:
        db.close()


async def cmd_pendientes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from database import SessionLocal, get_pending_reports
    db = SessionLocal()
    try:
        pending_reports = get_pending_reports(db)
        if not pending_reports:
            await update.message.reply_text("✅ No hay posts pendientes de autor.")
            return
        # Iniciar flujo de asignación para el informe más reciente pendiente
        await _start_assignment_flow(update.effective_chat.id, pending_reports[0], context)
    finally:
        db.close()


# ─── Flujo de asignación ──────────────────────────────────────────────────────

async def send_daily_notification(report_date: date = None):
    """
    Llamado por el scheduler después de generar el informe.
    Envía la notificación de autores pendientes al chat configurado.
    """
    if not BOT_TOKEN or not CHAT_ID:
        logger.error("TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID no configurados.")
        return

    from database import SessionLocal, get_report_by_date

    if report_date is None:
        report_date = date.today() - timedelta(days=1)

    db = SessionLocal()
    try:
        report = get_report_by_date(db, report_date)
        if not report:
            logger.warning(f"No se encontró informe para {report_date}")
            return

        pending = report.pending_authors
        if not pending:
            logger.info(f"No hay autores pendientes para {report_date}")
            return

        bot = Bot(token=BOT_TOKEN)
        await bot.send_message(
            chat_id=CHAT_ID,
            text=(
                f"📋 *Informe del {report_date.strftime('%-d de %B')} listo*\n\n"
                f"Hay *{len(pending)} {'post' if len(pending)==1 else 'posts'}* "
                f"pendientes de autor.\n"
                f"Te preguntaré uno por uno 👇"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
        await asyncio.sleep(1)

        # Iniciar flujo
        _state[str(CHAT_ID)] = {
            "report_date": report_date,
            "pending_post_ids": [p.id for p in pending],
            "current_index": 0,
        }
        await _send_post_question(bot, str(CHAT_ID), pending[0])

    finally:
        db.close()


async def _start_assignment_flow(chat_id_str: str, report, context: ContextTypes.DEFAULT_TYPE):
    """Inicia el flujo de asignación desde un comando manual."""
    pending = report.pending_authors
    if not pending:
        bot = context.bot
        await bot.send_message(chat_id=chat_id_str, text="✅ Todos los autores ya están asignados.")
        return

    _state[str(chat_id_str)] = {
        "report_date": report.date,
        "pending_post_ids": [p.id for p in pending],
        "current_index": 0,
    }
    await context.bot.send_message(
        chat_id=chat_id_str,
        text=(
            f"📋 *{report.date.strftime('%-d de %B')}* — "
            f"{len(pending)} posts pendientes\nVamos allá 👇"
        ),
        parse_mode=ParseMode.MARKDOWN,
    )
    await _send_post_question(context.bot, str(chat_id_str), pending[0])


async def _send_post_question(bot: Bot, chat_id: str, post):
    """Envía el mensaje con los botones de autor para un post concreto."""
    # Texto resumido del tweet (max 120 chars)
    tweet_preview = (post.text or "")[:120]
    if len(post.text or "") > 120:
        tweet_preview += "…"

    # Construir teclado inline con autores (2 por fila) + opción Otro
    keyboard = []
    row = []
    for i, author in enumerate(AUTHORS):
        row.append(InlineKeyboardButton(
            author,
            callback_data=f"author|{post.id}|{author}"
        ))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    # Fila extra: "Otro" y "Saltar"
    keyboard.append([
        InlineKeyboardButton("✏️ Otro (no en lista)", callback_data=f"skip|{post.id}|otro"),
        InlineKeyboardButton("⏭ Saltar por ahora",   callback_data=f"skip|{post.id}|saltar"),
    ])

    text = (
        f"━━━━━━━━━━━━━━━━━\n"
        f"🐦 *Post {post.post_number}* · {post.time}h\n"
        f"_{tweet_preview}_\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"¿Quién lo publicó?"
    )

    await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_author_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback cuando el usuario pulsa un botón de autor."""
    query = update.callback_query
    await query.answer()

    _, post_id_str, author = query.data.split("|", 2)
    post_id  = int(post_id_str)
    chat_id  = str(update.effective_chat.id)

    from database import SessionLocal, assign_author
    db = SessionLocal()
    try:
        post = assign_author(db, post_id, author)
        if not post:
            await query.edit_message_text("❌ Error al guardar. Intenta de nuevo con /pendientes")
            return

        # Editar el mensaje original para confirmar
        await query.edit_message_text(
            f"✅ Post {post.post_number} ({post.time}h) → *{author}*",
            parse_mode=ParseMode.MARKDOWN,
        )

        # Avanzar al siguiente pendiente
        state = _state.get(chat_id)
        if not state:
            return

        state["current_index"] += 1
        idx = state["current_index"]
        pending_ids = state["pending_post_ids"]

        if idx >= len(pending_ids):
            # Todos asignados
            _state.pop(chat_id, None)
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🎉 *¡Todos los autores asignados!*\n\n"
                    f"📊 El dashboard ya está actualizado:\n"
                    f"{APP_URL}"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            # Obtener el siguiente post pendiente
            next_post_id = pending_ids[idx]
            from database import Post
            next_post = db.query(Post).filter(Post.id == next_post_id).first()
            if next_post:
                await _send_post_question(context.bot, chat_id, next_post)
    finally:
        db.close()


async def handle_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """El usuario pulsó 'Saltar' o 'Otro'."""
    query = update.callback_query
    await query.answer()

    _, post_id_str, action = query.data.split("|", 2)
    chat_id = str(update.effective_chat.id)

    if action == "otro":
        await query.edit_message_text(
            "✏️ Escribe el nombre del autor y envíalo como mensaje.\n"
            "_(función próximamente — por ahora usa /pendientes para reiniciar)_",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:  # saltar
        await query.edit_message_text("⏭ Post saltado. Puedes asignarlo más tarde con /pendientes")

        # Avanzar al siguiente
        state = _state.get(chat_id)
        if state:
            state["current_index"] += 1
            idx = state["current_index"]
            pending_ids = state["pending_post_ids"]

            if idx < len(pending_ids):
                from database import SessionLocal, Post
                db = SessionLocal()
                try:
                    next_post = db.query(Post).filter(Post.id == pending_ids[idx]).first()
                    if next_post:
                        await _send_post_question(context.bot, chat_id, next_post)
                finally:
                    db.close()
            else:
                _state.pop(chat_id, None)
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="📋 Fin de la lista. Usa /pendientes para retomar los que saltaste.",
                )
