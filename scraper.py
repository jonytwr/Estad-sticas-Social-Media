"""
scraper.py — Extracción de datos de @defensagob desde Nitter (sin Chrome abierto)
Usa Playwright en modo headless, ejecutado en Railway.
"""

import os
import re
import time
import logging
from datetime import date, timedelta, datetime
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

logger = logging.getLogger(__name__)

# Instancias de Nitter a probar en orden (si una falla, se usa la siguiente)
NITTER_INSTANCES = [
    "https://nitter.tiekoetter.com",
    "https://nitter.rawbit.ninja",
    "https://nitter.d420.de",
    "https://nitter.mint.lgbt",
    "https://nitter.fdn.fr",
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.woodland.cafe",
]
TWITTER_HANDLE = os.getenv("TWITTER_HANDLE", "Defensagob")


def _try_nitter(handle: str, target_date: date) -> dict | None:
    """
    Intenta scrapeaar con Playwright. Devuelve los datos crudos o None si falla.
    """
    result = {
        "profile": {},
        "posts": [],
        "scrape_date": datetime.now().isoformat(),
        "target_date": target_date.isoformat(),
        "source": None,
    }

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        for instance in NITTER_INSTANCES:
            try:
                url = f"{instance}/{handle}"
                logger.info(f"Probando Nitter: {url}")
                page.goto(url, timeout=20000, wait_until="domcontentloaded")
                time.sleep(2)

                # Verificar que la página cargó correctamente
                if page.query_selector(".error-panel") or "instance is down" in page.content().lower():
                    logger.warning(f"Instancia caída: {instance}")
                    continue

                result["source"] = instance

                # ── Datos del perfil ──────────────────────────────────────
                try:
                    followers_el = page.query_selector(".followers .profile-stat-num")
                    following_el = page.query_selector(".following .profile-stat-num")
                    tweets_el    = page.query_selector(".tweets .profile-stat-num")

                    def parse_num(el):
                        if not el:
                            return None
                        txt = el.inner_text().strip().replace(",", "").replace(".", "")
                        return int(txt) if txt.isdigit() else None

                    result["profile"] = {
                        "followers": parse_num(followers_el),
                        "following": parse_num(following_el),
                        "total_posts": parse_num(tweets_el),
                    }
                except Exception as e:
                    logger.warning(f"Error leyendo perfil: {e}")

                # ── Posts del día objetivo ────────────────────────────────
                posts_data = []
                pages_checked = 0
                target_str = target_date.strftime("%-d de")  # "14 de"

                while pages_checked < 5:  # Máximo 5 páginas de timeline
                    tweet_items = page.query_selector_all(".timeline-item:not(.show-more)")

                    for item in tweet_items:
                        try:
                            # Fecha del tweet
                            date_el = item.query_selector(".tweet-date a")
                            tweet_datetime_str = date_el.get_attribute("title") if date_el else ""
                            tweet_date = _parse_nitter_date(tweet_datetime_str)

                            if tweet_date is None:
                                continue
                            if tweet_date < target_date:
                                # Hemos pasado el día objetivo
                                pages_checked = 999
                                break
                            if tweet_date != target_date:
                                continue

                            # Texto
                            text_el = item.query_selector(".tweet-content")
                            text = text_el.inner_text().strip() if text_el else ""

                            # Hora
                            hour = ""
                            if tweet_datetime_str:
                                m = re.search(r"(\d{2}:\d{2})", tweet_datetime_str)
                                hour = m.group(1) if m else ""

                            # Formato (inferido de los adjuntos)
                            format_ = "Texto"
                            if item.query_selector(".attachments video, .gif"):
                                format_ = "Vídeo"
                            elif item.query_selector(".attachments img"):
                                imgs = item.query_selector_all(".attachments img")
                                format_ = f"Fotos ({len(imgs)})" if len(imgs) > 1 else "Fotos (1)"

                            # Estadísticas
                            stats = _parse_stats(item)

                            # URL del tweet
                            tweet_link_el = item.query_selector(".tweet-date a")
                            tweet_path = tweet_link_el.get_attribute("href") if tweet_link_el else ""
                            tweet_url = f"https://x.com{tweet_path}" if tweet_path else ""

                            # Comentarios/respuestas
                            comments = _get_comments(page, instance, tweet_path)

                            posts_data.append({
                                "time": hour,
                                "text": text,
                                "format": format_,
                                "author": "PENDIENTE",
                                "stats": stats,
                                "tweet_url": tweet_url,
                                "comments": comments,
                            })

                        except Exception as e:
                            logger.warning(f"Error procesando tweet: {e}")
                            continue

                    if pages_checked >= 999:
                        break

                    # Siguiente página
                    next_btn = page.query_selector(".show-more a")
                    if next_btn:
                        next_url = next_btn.get_attribute("href")
                        if next_url:
                            page.goto(f"{instance}{next_url}", timeout=20000, wait_until="domcontentloaded")
                            time.sleep(1.5)
                    else:
                        break
                    pages_checked += 1

                # Numerar posts (orden cronológico)
                posts_data.reverse()
                for i, p in enumerate(posts_data, 1):
                    p["post_number"] = i
                    p["title"] = _generate_title(p["text"])

                result["posts"] = posts_data
                browser.close()
                return result

            except PwTimeout:
                logger.warning(f"Timeout en {instance}")
                continue
            except Exception as e:
                logger.warning(f"Error en {instance}: {e}")
                continue

        browser.close()

    logger.error("Todas las instancias de Nitter fallaron.")
    return None


def _get_comments(page, instance: str, tweet_path: str) -> list[dict]:
    """Abre el hilo del tweet y extrae los comentarios."""
    if not tweet_path:
        return []
    comments = []
    try:
        thread_url = f"{instance}{tweet_path}"
        thread_page = page.context.new_page()
        thread_page.goto(thread_url, timeout=15000, wait_until="domcontentloaded")
        time.sleep(1)

        replies = thread_page.query_selector_all(".reply")
        for reply in replies[:10]:  # Máximo 10 comentarios por post
            try:
                user_el  = reply.query_selector(".username")
                text_el  = reply.query_selector(".tweet-content")
                likes_el = reply.query_selector(".icon-heart + .tweet-stat")

                usuario = user_el.inner_text().strip() if user_el else ""
                texto   = text_el.inner_text().strip() if text_el else ""
                likes_txt = likes_el.inner_text().strip() if likes_el else "0"
                likes   = _safe_int(likes_txt)

                tono = _classify_tone(texto)

                if usuario and texto:
                    comments.append({
                        "usuario": usuario,
                        "texto": texto,
                        "likes": likes,
                        "tono": tono,
                    })
            except Exception:
                continue
        thread_page.close()
    except Exception as e:
        logger.warning(f"No se pudieron obtener comentarios de {tweet_path}: {e}")

    return comments


def _parse_stats(item) -> dict:
    """Extrae likes, reposts, respuestas, guardados y visualizaciones de un tweet."""
    stats = {"respuestas": 0, "reposts": 0, "likes": 0, "guardados": 0, "visualizaciones": 0}
    try:
        stat_els = item.query_selector_all(".tweet-stats .tweet-stat")
        icons = ["comment", "retweet", "heart", "bookmark"]
        for el in stat_els:
            txt = el.inner_text().strip()
            icon_el = el.query_selector("[class*='icon-']")
            if not icon_el:
                continue
            cls = icon_el.get_attribute("class") or ""
            val = _safe_int(txt)
            if "comment" in cls:
                stats["respuestas"] = val
            elif "retweet" in cls:
                stats["reposts"] = val
            elif "heart" in cls:
                stats["likes"] = val
            elif "bookmark" in cls:
                stats["guardados"] = val
            elif "eye" in cls or "views" in cls:
                stats["visualizaciones"] = val
    except Exception:
        pass
    return stats


def _calculate_score(stats: dict) -> float:
    """Score = Likes×3 + Reposts×2 + Guardados×2 + Respuestas×1 + Visualizaciones÷100"""
    return round(
        stats.get("likes", 0) * 3
        + stats.get("reposts", 0) * 2
        + stats.get("guardados", 0) * 2
        + stats.get("respuestas", 0) * 1
        + stats.get("visualizaciones", 0) / 100,
        1,
    )


def _parse_nitter_date(date_str: str) -> date | None:
    """
    Nitter muestra fechas en varios formatos:
    - 'Apr 14, 2026 · 9:00 AM UTC'
    - '14/04/2026, 09:00:00'
    """
    if not date_str:
        return None
    # Intentar varios formatos
    formats = [
        "%b %d, %Y",
        "%d/%m/%Y",
        "%Y-%m-%d",
    ]
    # Limpiar cadena
    clean = re.sub(r"[·,].*", "", date_str).strip()
    for fmt in formats:
        try:
            return datetime.strptime(clean, fmt).date()
        except ValueError:
            continue

    # Buscar patrón dd/mm/yyyy
    m = re.search(r"(\d{1,2})/(\d{2})/(\d{4})", date_str)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass

    # Buscar mes en inglés (Apr 14, 2026)
    m = re.search(r"(\w+)\s+(\d{1,2}),?\s+(\d{4})", date_str)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%b %d %Y").date()
        except ValueError:
            pass

    return None


def _generate_title(text: str) -> str:
    """Genera un título corto a partir del texto del tweet."""
    clean = re.sub(r"http\S+", "", text).strip()
    clean = re.sub(r"#\S+", "", clean).strip()
    clean = re.sub(r"@\S+", "", clean).strip()
    # Primeras 8 palabras
    words = clean.split()[:8]
    title = " ".join(words)
    return title.rstrip(".,;:") if title else "Post sin título"


def _classify_tone(text: str) -> str:
    """Clasificación básica de tono. Claude API refinará esto en el análisis."""
    text_lower = text.lower()
    negative_keywords = ["mal", "vergüenza", "corrupción", "mentira", "dimite", "roba",
                         "fascist", "nazi", "murder", "kill", "war criminal", "disolver"]
    offtopic_keywords  = ["gaza", "palestin", "ukraine", "ucrania", "bitcoin", "crypto",
                          "unifil", "iran", "compra", "gana dinero"]
    positive_keywords  = ["bravo", "gracias", "orgullo", "excelente", "genial", "bien hecho",
                          "felicidades", "enhorabuena"]

    for kw in negative_keywords:
        if kw in text_lower:
            return "🔴 Negativo/Hostil"
    for kw in offtopic_keywords:
        if kw in text_lower:
            return "⚠️ Off-topic/propagandístico"
    for kw in positive_keywords:
        if kw in text_lower:
            return "✅ Positivo"
    return "⚪ Indeterminado"


def _safe_int(s: str) -> int:
    try:
        return int(re.sub(r"[^\d]", "", s))
    except (ValueError, TypeError):
        return 0


# ─── Punto de entrada público ─────────────────────────────────────────────────

def scrape_yesterday() -> dict | None:
    """Scrapeaa los posts del día anterior. Devuelve el dict de datos crudos."""
    yesterday = date.today() - timedelta(days=1)
    logger.info(f"Scrapeando @{TWITTER_HANDLE} para el {yesterday}")
    data = _try_nitter(TWITTER_HANDLE, yesterday)
    if data and data["posts"]:
        # Calcular scores
        for p in data["posts"]:
            p["score"] = _calculate_score(p["stats"])
        logger.info(f"Obtenidos {len(data['posts'])} posts para {yesterday}")
    else:
        logger.warning(f"No se encontraron posts para {yesterday}")
    return data


def scrape_date(target_date: date) -> dict | None:
    """Scrapeaa los posts de una fecha concreta (para re-scraping manual)."""
    data = _try_nitter(TWITTER_HANDLE, target_date)
    if data and data["posts"]:
        for p in data["posts"]:
            p["score"] = _calculate_score(p["stats"])
    return data
