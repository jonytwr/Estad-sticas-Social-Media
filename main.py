"""
main.py — Punto de entrada de la aplicación
FastAPI + Telegram bot + scheduler diario
Todo corre en un único proceso en Railway.
"""

import os
import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from datetime import date, timedelta

from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import database as db
from database import (
    SessionLocal, get_db, get_all_reports, get_report_by_date,
    get_pending_reports, assign_author, author_stats, flagged_users, Post,
    DailyReport, Comment,
)
import bot as telegram_bot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ─── Scheduler y tarea diaria ─────────────────────────────────────────────────

scheduler = AsyncIOScheduler(timezone="Europe/Madrid")

SCRAPE_HOUR   = int(os.getenv("SCRAPE_HOUR",   "8"))
SCRAPE_MINUTE = int(os.getenv("SCRAPE_MINUTE", "0"))


async def daily_task():
    """
    Tarea diaria:
    1. Scrapeaa Nitter
    2. Genera análisis con Claude API
    3. Guarda en base de datos
    4. Envía notificación de Telegram
    """
    logger.info("⏰ Iniciando tarea diaria de scraping y análisis...")
    yesterday = date.today() - timedelta(days=1)

    # Comprobar si ya existe un informe para ayer
    session = SessionLocal()
    try:
        existing = get_report_by_date(session, yesterday)
        if existing:
            logger.info(f"Informe del {yesterday} ya existe. Saltando scraping.")
            # Enviar notificación si quedan autores pendientes
            if not existing.authors_complete:
                await telegram_bot.send_daily_notification(yesterday)
            return
    finally:
        session.close()

    # 1. Scraping
    try:
        from scraper import scrape_yesterday
        raw_data = await asyncio.to_thread(scrape_yesterday)
    except Exception as e:
        logger.error(f"Error en scraping: {e}")
        raw_data = None

    if not raw_data or not raw_data.get("posts"):
        logger.warning("No se obtuvieron datos del scraper. Abortando tarea diaria.")
        return

    # 2. Análisis con Claude API
    try:
        from analyzer import generate_report, build_historical_context, parse_report_to_db
        session = SessionLocal()
        try:
            hist_context = await asyncio.to_thread(build_historical_context, session, 7)
        finally:
            session.close()

        report_md = await asyncio.to_thread(generate_report, raw_data, hist_context)
        structured = parse_report_to_db(report_md, raw_data)
    except Exception as e:
        logger.error(f"Error en análisis: {e}")
        return

    # 3. Guardar en base de datos
    try:
        session = SessionLocal()
        try:
            # Crear informe
            r = structured["report"]
            new_report = DailyReport(
                date=r["date"],
                followers=r.get("followers"),
                following=r.get("following"),
                total_posts_account=r.get("total_posts_account"),
                score_medio=r.get("score_medio"),
                total_vis=r.get("total_vis"),
                tono_general=r.get("tono_general"),
                analysis_text=r.get("analysis_text"),
                raw_markdown=r.get("raw_markdown"),
                authors_complete=False,
            )
            session.add(new_report)
            session.flush()  # Para obtener el ID

            # Crear posts y comentarios
            for p in structured["posts"]:
                new_post = Post(
                    report_id=new_report.id,
                    post_number=p["post_number"],
                    title=p.get("title"),
                    time=p.get("time"),
                    text=p.get("text"),
                    format=p.get("format"),
                    author="PENDIENTE",
                    score=p.get("score"),
                    respuestas=p.get("respuestas", 0),
                    reposts=p.get("reposts", 0),
                    likes=p.get("likes", 0),
                    guardados=p.get("guardados", 0),
                    visualizaciones=p.get("visualizaciones", 0),
                    tweet_url=p.get("tweet_url"),
                )
                session.add(new_post)
                session.flush()

                for c in p.get("comments", []):
                    new_comment = Comment(
                        post_id=new_post.id,
                        usuario=c.get("usuario"),
                        texto=c.get("texto"),
                        likes=c.get("likes", 0),
                        tono=c.get("tono"),
                    )
                    session.add(new_comment)

            session.commit()
            logger.info(f"✅ Informe del {yesterday} guardado con {len(structured['posts'])} posts.")
        except Exception as e:
            session.rollback()
            logger.error(f"Error guardando en DB: {e}")
            raise
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Error en guardado DB: {e}")
        return

    # 4. Notificación Telegram
    await telegram_bot.send_daily_notification(yesterday)


# ─── Lifespan (startup / shutdown) ───────────────────────────────────────────

telegram_application = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global telegram_application

    # Inicializar DB
    db.init_db()
    logger.info("Base de datos inicializada.")

    # Iniciar bot de Telegram
    if os.getenv("TELEGRAM_BOT_TOKEN"):
        telegram_application = telegram_bot.build_app()
        await telegram_application.initialize()
        await telegram_application.start()
        await telegram_application.updater.start_polling(drop_pending_updates=True)
        logger.info("Bot de Telegram iniciado (polling).")
    else:
        logger.warning("TELEGRAM_BOT_TOKEN no configurado. Bot desactivado.")

    # Iniciar scheduler
    scheduler.add_job(
        daily_task,
        trigger="cron",
        hour=SCRAPE_HOUR,
        minute=SCRAPE_MINUTE,
        id="daily_scrape",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(f"Scheduler iniciado. Próxima ejecución: {SCRAPE_HOUR:02d}:{SCRAPE_MINUTE:02d}")

    yield

    # Shutdown
    scheduler.shutdown(wait=False)
    if telegram_application:
        await telegram_application.updater.stop()
        await telegram_application.stop()
        await telegram_application.shutdown()
    logger.info("Aplicación cerrada correctamente.")


# ─── FastAPI app ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="@defensagob Dashboard API",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Servir archivos estáticos (dashboard.html)
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


# ─── Endpoints API ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Sirve el dashboard HTML."""
    dashboard_path = "static/dashboard.html"
    if os.path.exists(dashboard_path):
        with open(dashboard_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h1>Dashboard no encontrado. Asegúrate de que static/dashboard.html existe.</h1>")


@app.get("/api/reports")
async def list_reports(session: Session = Depends(get_db)):
    """Lista todos los informes (resumen, sin contenido markdown)."""
    reports = get_all_reports(session)
    return [
        {
            "id": r.id,
            "date": r.date.isoformat(),
            "followers": r.followers,
            "score_medio": r.score_medio,
            "total_vis": r.total_vis,
            "post_count": len(r.posts),
            "authors_complete": r.authors_complete,
            "pending_authors": len(r.pending_authors),
        }
        for r in reports
    ]


@app.get("/api/reports/{report_date}")
async def get_report(report_date: str, session: Session = Depends(get_db)):
    """Devuelve un informe completo con todos sus posts y comentarios."""
    try:
        d = date.fromisoformat(report_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Formato de fecha inválido. Usa YYYY-MM-DD")

    report = get_report_by_date(session, d)
    if not report:
        raise HTTPException(status_code=404, detail=f"No hay informe para {report_date}")

    return {
        "id": report.id,
        "date": report.date.isoformat(),
        "followers": report.followers,
        "following": report.following,
        "total_posts_account": report.total_posts_account,
        "score_medio": report.score_medio,
        "total_vis": report.total_vis,
        "tono_general": report.tono_general,
        "analysis_text": report.analysis_text,
        "authors_complete": report.authors_complete,
        "posts": [
            {
                "id": p.id,
                "post_number": p.post_number,
                "title": p.title,
                "time": p.time,
                "text": p.text,
                "format": p.format,
                "author": p.author,
                "score": p.score,
                "respuestas": p.respuestas,
                "reposts": p.reposts,
                "likes": p.likes,
                "guardados": p.guardados,
                "visualizaciones": p.visualizaciones,
                "tweet_url": p.tweet_url,
                "comments": [
                    {
                        "usuario": c.usuario,
                        "texto": c.texto,
                        "likes": c.likes,
                        "tono": c.tono,
                    }
                    for c in p.comments
                ],
            }
            for p in report.posts
        ],
    }


@app.get("/api/reports/{report_date}/markdown")
async def get_report_markdown(report_date: str, session: Session = Depends(get_db)):
    """Devuelve el informe en formato Markdown original."""
    try:
        d = date.fromisoformat(report_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Formato de fecha inválido")
    report = get_report_by_date(session, d)
    if not report:
        raise HTTPException(status_code=404, detail="Informe no encontrado")
    return {"markdown": report.raw_markdown}


@app.patch("/api/posts/{post_id}/author")
async def update_author(post_id: int, payload: dict, session: Session = Depends(get_db)):
    """
    Asigna un autor a un post.
    Body: {"author": "Fernando"}
    """
    author = payload.get("author", "").strip()
    if not author:
        raise HTTPException(status_code=400, detail="El campo 'author' es obligatorio")
    post = assign_author(session, post_id, author)
    if not post:
        raise HTTPException(status_code=404, detail="Post no encontrado")
    return {"post_id": post_id, "author": author, "report_complete": post.report.authors_complete}


@app.get("/api/stats/authors")
async def get_author_stats(session: Session = Depends(get_db)):
    """Estadísticas agregadas por autor."""
    return author_stats(session)


@app.get("/api/stats/flagged-users")
async def get_flagged_users(min_appearances: int = 2, session: Session = Depends(get_db)):
    """Usuarios reincidentes en comentarios."""
    return flagged_users(session, min_appearances)


@app.get("/api/stats/formats")
async def get_format_stats(session: Session = Depends(get_db)):
    """Rendimiento medio por formato de contenido."""
    posts = session.query(Post).filter(
        Post.score.isnot(None),
        Post.format.isnot(None),
    ).all()

    stats: dict[str, dict] = {}
    for p in posts:
        fmt = p.format.split(" ")[0]  # "Fotos (2)" → "Fotos"
        if fmt not in stats:
            stats[fmt] = {"format": fmt, "count": 0, "total_score": 0, "total_vis": 0}
        stats[fmt]["count"] += 1
        stats[fmt]["total_score"] += p.score or 0
        stats[fmt]["total_vis"] += p.visualizaciones or 0

    result = []
    for fmt, s in stats.items():
        result.append({
            "format": fmt,
            "count": s["count"],
            "avg_score": round(s["total_score"] / s["count"], 1),
            "avg_vis": round(s["total_vis"] / s["count"]),
        })
    return sorted(result, key=lambda x: x["avg_score"], reverse=True)


@app.get("/api/stats/timing")
async def get_timing_stats(session: Session = Depends(get_db)):
    """Rendimiento medio por franja horaria."""
    posts = session.query(Post).filter(
        Post.time.isnot(None),
        Post.score.isnot(None),
    ).all()

    slots: dict[str, dict] = {}
    for p in posts:
        try:
            hour = int(p.time.split(":")[0])
        except (ValueError, AttributeError):
            continue

        # Agrupar en franjas de 2h
        slot = f"{hour:02d}:00–{hour+1:02d}:59"
        if slot not in slots:
            slots[slot] = {"slot": slot, "hour": hour, "count": 0, "total_score": 0}
        slots[slot]["count"] += 1
        slots[slot]["total_score"] += p.score or 0

    result = []
    for s in slots.values():
        result.append({
            "slot": s["slot"],
            "count": s["count"],
            "avg_score": round(s["total_score"] / s["count"], 1),
        })
    return sorted(result, key=lambda x: x["hour"])


@app.post("/api/trigger-daily")
async def trigger_daily_manually(secret: str = ""):
    """
    Dispara la tarea diaria manualmente (para pruebas o si falló).
    Requiere el parámetro ?secret=TU_SECRET
    """
    admin_secret = os.getenv("ADMIN_SECRET", "")
    if admin_secret and secret != admin_secret:
        raise HTTPException(status_code=403, detail="Acceso denegado")

    asyncio.create_task(daily_task())
    return {"status": "Tarea diaria iniciada en background"}


@app.get("/health")
async def health():
    return {"status": "ok", "bot_active": telegram_application is not None}
