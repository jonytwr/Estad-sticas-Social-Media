"""
parser.py — Importa archivos .md/.txt históricos a la base de datos
Ejecuta este script una sola vez para migrar todos tus archivos existentes.

Uso:
    python parser.py /ruta/a/la/carpeta/con/archivos
    python parser.py ./informes/          # carpeta relativa
    python parser.py informe-2026-04-15.md  # un solo archivo
"""

import re
import sys
import os
import logging
from datetime import date, datetime
from pathlib import Path
from sqlalchemy.orm import Session

# Añadir el directorio actual al path para importar database
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import (
    init_db, SessionLocal, DailyReport, Post, Comment,
    get_report_by_date,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ─── Parser del formato Markdown ─────────────────────────────────────────────

def parse_md_file(filepath: str) -> dict | None:
    """
    Parsea un archivo .md de informe diario y devuelve un dict estructurado.
    Devuelve None si no se puede parsear.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        logger.error(f"No se pudo leer {filepath}: {e}")
        return None

    result = {
        "raw_markdown": content,
        "date": None,
        "followers": None,
        "following": None,
        "total_posts_account": None,
        "score_medio": None,
        "total_vis": None,
        "tono_general": "",
        "analysis_text": "",
        "posts": [],
    }

    # ── Fecha del informe ──────────────────────────────────────────────────
    # "# Informe diario @defensagob — 14 de abril de 2026"
    date_match = re.search(r"# Informe diario.*?—\s*(.+)", content)
    if date_match:
        result["date"] = _parse_spanish_date(date_match.group(1).strip())

    # Si no encontramos la fecha en el header, intentar desde el nombre del archivo
    if not result["date"]:
        fname = Path(filepath).stem
        m = re.search(r"(\d{4}-\d{2}-\d{2})", fname)
        if m:
            try:
                result["date"] = date.fromisoformat(m.group(1))
            except ValueError:
                pass

    if not result["date"]:
        logger.warning(f"No se pudo determinar la fecha de {filepath}")
        return None

    # ── Datos del perfil ───────────────────────────────────────────────────
    followers_m = re.search(r"\|\s*Seguidores\s*\|\s*([\d.,]+)\s*\|", content)
    following_m = re.search(r"\|\s*Siguiendo\s*\|\s*([\d.,]+)\s*\|", content)
    posts_acc_m = re.search(r"\|\s*Posts publicados\s*\|\s*([\d.,]+)\s*\|", content)

    result["followers"]           = _parse_num(followers_m.group(1)) if followers_m else None
    result["following"]           = _parse_num(following_m.group(1)) if following_m else None
    result["total_posts_account"] = _parse_num(posts_acc_m.group(1)) if posts_acc_m else None

    # ── Posts ──────────────────────────────────────────────────────────────
    # Dividir por "### Post N"
    post_sections = re.split(r"(?=### Post \d+)", content)
    post_number = 0

    for section in post_sections:
        if not section.strip().startswith("### Post"):
            continue
        post_number += 1
        post = _parse_post_section(section, post_number)
        if post:
            result["posts"].append(post)

    # ── Score medio y visualizaciones totales ─────────────────────────────
    scores = [p["score"] for p in result["posts"] if p.get("score")]
    result["score_medio"] = round(sum(scores) / len(scores), 1) if scores else None
    result["total_vis"] = sum(p.get("visualizaciones", 0) for p in result["posts"])

    # ── Análisis de tono ───────────────────────────────────────────────────
    tono_m = re.search(
        r"## Análisis de tono general\n+(.*?)(?=\n---|\n## 📋)",
        content, re.DOTALL
    )
    if tono_m:
        result["tono_general"] = tono_m.group(1).strip()

    # ── Análisis y recomendaciones ─────────────────────────────────────────
    analysis_m = re.search(r"(## 📋 Análisis y recomendaciones.*)", content, re.DOTALL)
    if analysis_m:
        result["analysis_text"] = analysis_m.group(1).strip()

    return result


def _parse_post_section(section: str, fallback_number: int) -> dict | None:
    """Extrae los datos de un bloque de post individual."""
    post = {
        "post_number": fallback_number,
        "title": "",
        "time": "",
        "text": "",
        "format": "Texto",
        "author": "PENDIENTE",
        "score": None,
        "respuestas": 0,
        "reposts": 0,
        "likes": 0,
        "guardados": 0,
        "visualizaciones": 0,
        "tweet_url": "",
        "comments": [],
    }

    # Número y título: "### Post 3 — SEDEF firma adhesión..."
    header_m = re.search(r"### Post (\d+)\s*(?:—\s*(.+))?", section)
    if header_m:
        post["post_number"] = int(header_m.group(1))
        post["title"] = (header_m.group(2) or "").strip()

    # Hora
    time_m = re.search(r"\*\*Hora:\*\*\s*(\d{2}:\d{2})", section)
    if time_m:
        post["time"] = time_m.group(1)

    # Texto del tweet
    text_m = re.search(r'\*\*Texto:\*\*\s*"([^"]+)"', section, re.DOTALL)
    if text_m:
        post["text"] = text_m.group(1).strip()

    # Formato
    format_m = re.search(r"\*\*Formato:\*\*\s*(.+)", section)
    if format_m:
        post["format"] = format_m.group(1).strip()

    # Autor
    author_m = re.search(r"\*\*Autor:\*\*\s*\[([^\]]+)\]", section)
    if author_m:
        raw_author = author_m.group(1).strip()
        post["author"] = raw_author if raw_author.upper() != "PENDIENTE" else "PENDIENTE"

    # Score
    score_m = re.search(r"\*\*Score:\*\*\s*([\d.,]+)", section)
    if score_m:
        try:
            post["score"] = float(score_m.group(1).replace(",", "."))
        except ValueError:
            pass

    # Tabla de estadísticas
    # | Respuestas | Reposts | Likes | Guardados | Visualizaciones |
    # | 2 | 28 | 100 | — | 4.919 |
    stats_m = re.search(
        r"\|\s*(\d+|—)\s*\|\s*(\d+|—)\s*\|\s*(\d+|—)\s*\|\s*(\d+|—)\s*\|\s*([\d.,]+|—)\s*\|",
        section
    )
    if stats_m:
        post["respuestas"]      = _parse_num(stats_m.group(1))
        post["reposts"]         = _parse_num(stats_m.group(2))
        post["likes"]           = _parse_num(stats_m.group(3))
        post["guardados"]       = _parse_num(stats_m.group(4))
        post["visualizaciones"] = _parse_num(stats_m.group(5))

    # Comentarios
    # | @usuario | "texto" | likes | Tono |
    comment_pattern = re.compile(
        r"\|\s*(@\S+)\s*\|\s*\"?([^|]+?)\"?\s*\|\s*(\d+)\s*\|\s*([^|]+)\s*\|"
    )
    for cm in comment_pattern.finditer(section):
        post["comments"].append({
            "usuario": cm.group(1).strip(),
            "texto":   cm.group(2).strip().strip('"'),
            "likes":   int(cm.group(3)),
            "tono":    cm.group(4).strip(),
        })

    return post


# ─── Helpers ─────────────────────────────────────────────────────────────────

SPANISH_MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}

def _parse_spanish_date(s: str) -> date | None:
    """'14 de abril de 2026' → date(2026, 4, 14)"""
    m = re.search(r"(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})", s.lower())
    if m:
        day   = int(m.group(1))
        month = SPANISH_MONTHS.get(m.group(2))
        year  = int(m.group(3))
        if month:
            try:
                return date(year, month, day)
            except ValueError:
                pass
    return None


def _parse_num(s: str | None) -> int:
    if not s or s.strip() == "—":
        return 0
    try:
        return int(str(s).replace(".", "").replace(",", "").strip())
    except ValueError:
        return 0


# ─── Importación a la base de datos ──────────────────────────────────────────

def import_file(filepath: str, db: Session, overwrite: bool = False) -> bool:
    """Importa un solo archivo .md a la base de datos."""
    logger.info(f"Procesando {filepath}...")
    data = parse_md_file(filepath)
    if not data:
        logger.error(f"No se pudo parsear {filepath}")
        return False

    existing = get_report_by_date(db, data["date"])
    if existing:
        if not overwrite:
            logger.info(f"  → Ya existe informe para {data['date']}. Saltando (usa --overwrite para sobreescribir).")
            return False
        else:
            db.delete(existing)
            db.commit()
            logger.info(f"  → Sobreescribiendo informe del {data['date']}")

    # Comprobar si todos los autores están asignados
    all_assigned = all(
        p["author"] and p["author"] not in ("PENDIENTE", "[PENDIENTE]", "")
        for p in data["posts"]
    )

    report = DailyReport(
        date=data["date"],
        followers=data["followers"],
        following=data["following"],
        total_posts_account=data["total_posts_account"],
        score_medio=data["score_medio"],
        total_vis=data["total_vis"],
        tono_general=data["tono_general"],
        analysis_text=data["analysis_text"],
        raw_markdown=data["raw_markdown"],
        authors_complete=all_assigned,
    )
    db.add(report)
    db.flush()

    for p in data["posts"]:
        post = Post(
            report_id=report.id,
            post_number=p["post_number"],
            title=p["title"],
            time=p["time"],
            text=p["text"],
            format=p["format"],
            author=p["author"],
            score=p["score"],
            respuestas=p["respuestas"],
            reposts=p["reposts"],
            likes=p["likes"],
            guardados=p["guardados"],
            visualizaciones=p["visualizaciones"],
            tweet_url=p["tweet_url"],
        )
        db.add(post)
        db.flush()

        for c in p["comments"]:
            db.add(Comment(
                post_id=post.id,
                usuario=c["usuario"],
                texto=c["texto"],
                likes=c["likes"],
                tono=c["tono"],
            ))

    db.commit()
    logger.info(f"  ✅ Importado: {data['date']} — {len(data['posts'])} posts | autores: {'completos' if all_assigned else 'pendientes'}")
    return True


def import_folder(folder: str, overwrite: bool = False):
    """Importa todos los archivos .md/.txt de una carpeta."""
    folder_path = Path(folder)
    if not folder_path.is_dir():
        logger.error(f"No es una carpeta válida: {folder}")
        return

    files = sorted(list(folder_path.glob("*.md")) + list(folder_path.glob("*.txt")))
    logger.info(f"Encontrados {len(files)} archivos en {folder}")

    init_db()
    db = SessionLocal()
    imported = 0
    skipped  = 0
    errors   = 0

    try:
        for f in files:
            ok = import_file(str(f), db, overwrite=overwrite)
            if ok:
                imported += 1
            else:
                skipped += 1
    except Exception as e:
        logger.error(f"Error inesperado: {e}")
        errors += 1
    finally:
        db.close()

    logger.info(f"\n{'─'*40}")
    logger.info(f"Importación completada:")
    logger.info(f"  ✅ Importados: {imported}")
    logger.info(f"  ⏭  Saltados:   {skipped}")
    logger.info(f"  ❌ Errores:    {errors}")


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso:")
        print("  python parser.py /ruta/a/carpeta/          # importar toda la carpeta")
        print("  python parser.py /ruta/a/archivo.md        # importar un archivo")
        print("  python parser.py /ruta/a/carpeta/ --overwrite  # sobreescribir existentes")
        sys.exit(1)

    target  = sys.argv[1]
    overwrite = "--overwrite" in sys.argv

    init_db()
    path = Path(target)

    if path.is_dir():
        import_folder(target, overwrite=overwrite)
    elif path.is_file():
        db_session = SessionLocal()
        try:
            import_file(target, db_session, overwrite=overwrite)
        finally:
            db_session.close()
    else:
        logger.error(f"Ruta no válida: {target}")
        sys.exit(1)
