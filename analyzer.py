"""
analyzer.py — Genera el informe de análisis usando la API de Claude
Toma los datos crudos del scraper y produce el mismo formato de informe
que genera la tarea programada actual.
"""

import os
import json
import logging
from datetime import date, timedelta
from anthropic import Anthropic

logger = logging.getLogger(__name__)

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL  = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

TWITTER_HANDLE = os.getenv("TWITTER_HANDLE", "Defensagob")

SYSTEM_PROMPT = """
Eres el analista de redes sociales del Ministerio de Defensa de España (@defensagob).
Tu tarea es generar un informe diario exhaustivo a partir de los datos en bruto de Twitter/X.

FORMATO DEL INFORME — debes seguir exactamente esta estructura Markdown:

# Informe diario @defensagob — {fecha_larga}

**Hora de captura:** {hora} h ({fecha_captura})
**Ranking actualizado:** {estado_ranking}

---

## Datos públicos del perfil

| Métrica | Valor |
|---|---|
| Seguidores | {seguidores} |
| Siguiendo | {siguiendo} |
| Posts publicados | {posts_totales} |

*Fuente: nitter.net/{handle}*

---

## Publicaciones del {fecha_larga}

### Post {N} — {título}

**Hora:** {hora}
**Texto:** "{texto}"
**Formato:** {formato}
**Autor:** [PENDIENTE]
**Score:** {score} *(Likes×3 + Reposts×2 + Guardados×2 + Respuestas×1 + Visualizaciones÷100)*

| Respuestas | Reposts | Likes | Guardados | Visualizaciones |
|---|---|---|---|---|
| {val} | {val} | {val} | {val} | {val} |

**Comentarios:**

| Usuario | Texto | Likes | Tono |
|---|---|---|---|
...

---

[repetir para cada post]

## Análisis de tono general

[Párrafo de 3-5 frases sobre el ambiente general del día, comparando con días anteriores si hay datos]

---

## 📋 Análisis y recomendaciones

### 📊 Rendimiento del día
[métricas clave: mejor/peor post por visualizaciones, likes, reposts, score; score medio; vis totales]

### ⏰ Análisis de timing
[análisis de las franjas horarias usadas y cuáles funcionaron mejor; recomendaciones concretas de horario]

### 📝 Formato y tipo de contenido
[rendimiento por formato: vídeo, fotos, texto; ratio actual vs objetivo]

### 🔥 Temas que funcionan
[análisis de qué tipo de contenido resonó y por qué]

### ⚠️ Alertas y riesgos
[usuarios problemáticos, patrones de hostilidad, riesgos reputacionales]

### 💬 Comentarios a destacar
[los 2-3 comentarios más relevantes con análisis de cómo gestionarlos]

### 💡 Sugerencias de contenido para los próximos días
[5 sugerencias numeradas con fecha sugerida, hora, tipo de contenido y justificación]

### 🎯 Análisis estratégico
[visión global del día en contexto de la semana; oportunidades aprovechadas y perdidas; riesgos 48-72h vista]

---

REGLAS IMPORTANTES:
- El campo **Autor:** de cada post debe ser siempre [PENDIENTE] — nunca lo rellenes
- El Score se calcula SIEMPRE como: Likes×3 + Reposts×2 + Guardados×2 + Respuestas×1 + Visualizaciones÷100
- Si no hay comentarios disponibles, pon "Sin respuestas registradas." o explica por qué
- Sé muy concreto en las recomendaciones: di exactamente qué publicar, cuándo y por qué
- Detecta patrones de hostilidad coordinada (mismo texto en varios posts, cuentas extranjeras)
- Cuando hagas comparativas con días anteriores, usa los datos históricos si se te proporcionan
- Mantén un tono profesional pero directo; no uses bullet points dentro de los análisis narrativos
- La sección de alertas debe priorizar explícitamente: (prioridad alta/media/baja)
"""


def generate_report(scraped_data: dict, historical_context: str = "") -> str:
    """
    Genera el informe completo en Markdown a partir de los datos del scraper.

    scraped_data: dict devuelto por scraper.scrape_yesterday()
    historical_context: resumen de los últimos N días para comparativa
    Returns: string con el Markdown del informe
    """
    target_date_str = scraped_data.get("target_date", "")

    # Construir el contexto de datos para Claude
    data_summary = _build_data_summary(scraped_data)

    user_message = f"""
Genera el informe diario completo para @{TWITTER_HANDLE} correspondiente al {target_date_str}.

DATOS CAPTURADOS:
{data_summary}

{f"CONTEXTO HISTÓRICO (últimos días):\\n{historical_context}" if historical_context else ""}

Recuerda: todos los campos Autor deben ser [PENDIENTE]. Calcula el score con la fórmula exacta.
"""

    logger.info(f"Enviando datos a Claude API para análisis de {target_date_str}")
    response = client.messages.create(
        model=MODEL,
        max_tokens=8192,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    report_md = response.content[0].text
    logger.info(f"Análisis generado: {len(report_md)} caracteres")
    return report_md


def _build_data_summary(data: dict) -> str:
    """Formatea los datos crudos del scraper en texto estructurado para Claude."""
    lines = []

    # Perfil
    profile = data.get("profile", {})
    lines.append("=== PERFIL ===")
    lines.append(f"Seguidores: {profile.get('followers', 'N/D')}")
    lines.append(f"Siguiendo: {profile.get('following', 'N/D')}")
    lines.append(f"Posts totales: {profile.get('total_posts', 'N/D')}")
    lines.append("")

    # Posts
    posts = data.get("posts", [])
    lines.append(f"=== POSTS DEL DÍA ({len(posts)} publicaciones) ===")

    for p in posts:
        stats = p.get("stats", {})
        lines.append(f"\n--- Post {p.get('post_number', '?')} ---")
        lines.append(f"Hora: {p.get('time', '?')}")
        lines.append(f"Texto: {p.get('text', '')}")
        lines.append(f"Formato: {p.get('format', 'Texto')}")
        lines.append(f"URL: {p.get('tweet_url', '')}")
        lines.append(f"Score calculado: {p.get('score', 0)}")
        lines.append(f"Respuestas: {stats.get('respuestas', 0)}")
        lines.append(f"Reposts: {stats.get('reposts', 0)}")
        lines.append(f"Likes: {stats.get('likes', 0)}")
        lines.append(f"Guardados: {stats.get('guardados', 0)}")
        lines.append(f"Visualizaciones: {stats.get('visualizaciones', 0)}")

        comments = p.get("comments", [])
        if comments:
            lines.append(f"Comentarios ({len(comments)}):")
            for c in comments:
                lines.append(f"  @{c.get('usuario', '?')}: \"{c.get('texto', '')}\" | {c.get('likes', 0)} likes | Tono: {c.get('tono', '⚪')}")
        else:
            lines.append("Comentarios: No disponibles o sin respuestas")

    return "\n".join(lines)


def build_historical_context(db_session, days: int = 7) -> str:
    """
    Construye un resumen de los últimos N días para que Claude pueda hacer comparativas.
    """
    from database import DailyReport, Post
    from datetime import date

    today = date.today()
    recent_reports = (
        db_session.query(DailyReport)
        .filter(DailyReport.date >= today - timedelta(days=days))
        .order_by(DailyReport.date.desc())
        .all()
    )

    if not recent_reports:
        return ""

    lines = ["Comparativa de los últimos días (score medio | vis totales | posts):"]
    for r in recent_reports:
        lines.append(
            f"  {r.date} → score medio: {r.score_medio or 'N/D'} | "
            f"vis: {r.total_vis or 'N/D'} | "
            f"posts: {len(r.posts)}"
        )

    # Usuarios reincidentes en comentarios de los últimos días
    from database import flagged_users
    flagged = flagged_users(db_session, min_appearances=2)
    if flagged:
        lines.append("\nUsuarios con apariciones repetidas en comentarios:")
        for u in flagged[:5]:
            lines.append(f"  {u['usuario']}: {u['apariciones']} apariciones | tonos: {', '.join(u['ultimos_tonos'][:3])}")

    return "\n".join(lines)


def parse_report_to_db(report_md: str, scraped_data: dict) -> dict:
    """
    Extrae los datos estructurados del Markdown generado por Claude
    para insertarlos en la base de datos.
    Devuelve un dict con 'report' y 'posts'.
    """
    from datetime import date as date_type
    import re

    target_date_str = scraped_data.get("target_date", "")
    try:
        target_date = date_type.fromisoformat(target_date_str)
    except ValueError:
        target_date = date_type.today() - timedelta(days=1)

    profile = scraped_data.get("profile", {})
    posts_raw = scraped_data.get("posts", [])

    # Score medio
    scores = [p.get("score", 0) for p in posts_raw if p.get("score")]
    score_medio = round(sum(scores) / len(scores), 1) if scores else None

    # Visualizaciones totales
    total_vis = sum(p.get("stats", {}).get("visualizaciones", 0) for p in posts_raw)

    # Extraer sección de análisis de tono (primer bloque de análisis)
    tono_match = re.search(r"## Análisis de tono general\n+(.*?)(?=\n---|\n##)", report_md, re.DOTALL)
    tono_general = tono_match.group(1).strip() if tono_match else ""

    # Extraer sección de análisis completa
    analysis_match = re.search(r"(## 📋 Análisis y recomendaciones.*)", report_md, re.DOTALL)
    analysis_text = analysis_match.group(1).strip() if analysis_match else ""

    # Construir posts para DB
    db_posts = []
    for p in posts_raw:
        stats = p.get("stats", {})
        db_posts.append({
            "post_number": p.get("post_number", 0),
            "title": p.get("title", ""),
            "time": p.get("time", ""),
            "text": p.get("text", ""),
            "format": p.get("format", "Texto"),
            "author": "PENDIENTE",
            "score": p.get("score"),
            "respuestas": stats.get("respuestas", 0),
            "reposts": stats.get("reposts", 0),
            "likes": stats.get("likes", 0),
            "guardados": stats.get("guardados", 0),
            "visualizaciones": stats.get("visualizaciones", 0),
            "tweet_url": p.get("tweet_url", ""),
            "comments": p.get("comments", []),
        })

    return {
        "report": {
            "date": target_date,
            "followers": profile.get("followers"),
            "following": profile.get("following"),
            "total_posts_account": profile.get("total_posts"),
            "score_medio": score_medio,
            "total_vis": total_vis,
            "tono_general": tono_general,
            "analysis_text": analysis_text,
            "raw_markdown": report_md,
            "authors_complete": False,
        },
        "posts": db_posts,
    }
