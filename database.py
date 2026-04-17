"""
database.py — Modelos y conexión a la base de datos
SQLAlchemy síncrono con PostgreSQL (Railway) o SQLite (desarrollo local)
"""

import os
from datetime import date, datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Float,
    Date, Text, DateTime, ForeignKey, Boolean, func
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session

# ─── Conexión ────────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./defensagob.db")

# Railway usa postgres://, SQLAlchemy necesita postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


# ─── Modelos ─────────────────────────────────────────────────────────────────

class DailyReport(Base):
    """Un informe diario completo de @defensagob."""
    __tablename__ = "daily_reports"

    id             = Column(Integer, primary_key=True, index=True)
    date           = Column(Date, unique=True, nullable=False, index=True)
    followers      = Column(Integer, nullable=True)
    following      = Column(Integer, nullable=True)
    total_posts_account = Column(Integer, nullable=True)   # Posts totales del perfil
    score_medio    = Column(Float, nullable=True)
    total_vis      = Column(Integer, nullable=True)         # Visualizaciones totales del día
    tono_general   = Column(Text, nullable=True)            # Resumen del tono del día
    analysis_text  = Column(Text, nullable=True)            # Sección de análisis y recomendaciones
    raw_markdown   = Column(Text, nullable=True)            # Markdown original completo
    authors_complete = Column(Boolean, default=False)       # True cuando todos los autores están asignados
    created_at     = Column(DateTime, default=datetime.utcnow)

    posts = relationship("Post", back_populates="report", cascade="all, delete-orphan", order_by="Post.post_number")

    @property
    def pending_authors(self):
        return [p for p in self.posts if p.author in (None, "", "PENDIENTE", "[PENDIENTE]")]

    def check_and_mark_complete(self):
        if all(p.author and p.author not in ("PENDIENTE", "[PENDIENTE]", "") for p in self.posts):
            self.authors_complete = True


class Post(Base):
    """Un tweet/post individual del día."""
    __tablename__ = "posts"

    id              = Column(Integer, primary_key=True, index=True)
    report_id       = Column(Integer, ForeignKey("daily_reports.id"), nullable=False)
    post_number     = Column(Integer, nullable=False)
    title           = Column(String(500), nullable=True)
    time            = Column(String(10), nullable=True)     # "09:00"
    text            = Column(Text, nullable=True)
    format          = Column(String(50), nullable=True)     # Vídeo, Fotos, Texto
    author          = Column(String(100), nullable=True)    # PENDIENTE → nombre
    score           = Column(Float, nullable=True)
    respuestas      = Column(Integer, default=0)
    reposts         = Column(Integer, default=0)
    likes           = Column(Integer, default=0)
    guardados       = Column(Integer, default=0)
    visualizaciones = Column(Integer, default=0)
    tweet_url       = Column(String(500), nullable=True)

    report   = relationship("DailyReport", back_populates="posts")
    comments = relationship("Comment", back_populates="post", cascade="all, delete-orphan")


class Comment(Base):
    """Un comentario/respuesta a un post."""
    __tablename__ = "comments"

    id      = Column(Integer, primary_key=True, index=True)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=False)
    usuario = Column(String(100), nullable=True)
    texto   = Column(Text, nullable=True)
    likes   = Column(Integer, default=0)
    tono    = Column(String(100), nullable=True)    # ⚪ Indeterminado / ⚠️ Off-topic / etc.

    post = relationship("Post", back_populates="comments")


# ─── Utilidades ──────────────────────────────────────────────────────────────

def init_db():
    """Crea todas las tablas si no existen."""
    Base.metadata.create_all(bind=engine)


def get_db() -> Session:
    """Devuelve una sesión de base de datos. Úsala como context manager."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_report_by_date(db: Session, report_date: date) -> DailyReport | None:
    return db.query(DailyReport).filter(DailyReport.date == report_date).first()


def get_all_reports(db: Session) -> list[DailyReport]:
    return db.query(DailyReport).order_by(DailyReport.date.desc()).all()


def get_pending_reports(db: Session) -> list[DailyReport]:
    """Informes con al menos un autor pendiente."""
    return (
        db.query(DailyReport)
        .filter(DailyReport.authors_complete == False)
        .order_by(DailyReport.date.desc())
        .all()
    )


def assign_author(db: Session, post_id: int, author: str) -> Post | None:
    """Asigna un autor a un post y comprueba si el informe queda completo."""
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        return None
    post.author = author
    post.report.check_and_mark_complete()
    db.commit()
    db.refresh(post)
    return post


def author_stats(db: Session) -> list[dict]:
    """Estadísticas agregadas por autor."""
    posts = db.query(Post).filter(
        Post.author.notin_(["PENDIENTE", "[PENDIENTE]", "", None])
    ).all()

    stats: dict[str, dict] = {}
    for p in posts:
        a = p.author
        if a not in stats:
            stats[a] = {"author": a, "total_posts": 0, "total_likes": 0,
                        "total_reposts": 0, "total_vis": 0, "total_score": 0,
                        "formats": {}}
        stats[a]["total_posts"] += 1
        stats[a]["total_likes"] += p.likes or 0
        stats[a]["total_reposts"] += p.reposts or 0
        stats[a]["total_vis"] += p.visualizaciones or 0
        stats[a]["total_score"] += p.score or 0
        fmt = p.format or "Desconocido"
        stats[a]["formats"][fmt] = stats[a]["formats"].get(fmt, 0) + 1

    result = list(stats.values())
    for r in result:
        r["avg_score"] = round(r["total_score"] / r["total_posts"], 1) if r["total_posts"] else 0
    return sorted(result, key=lambda x: x["avg_score"], reverse=True)


def flagged_users(db: Session, min_appearances: int = 2) -> list[dict]:
    """Usuarios reincidentes en comentarios (≥ min_appearances en distintos posts)."""
    from sqlalchemy import func as sqlfunc
    rows = (
        db.query(Comment.usuario, sqlfunc.count(Comment.id).label("count"))
        .filter(Comment.usuario.isnot(None))
        .group_by(Comment.usuario)
        .having(sqlfunc.count(Comment.id) >= min_appearances)
        .order_by(sqlfunc.count(Comment.id).desc())
        .all()
    )
    result = []
    for usuario, count in rows:
        comments = db.query(Comment).filter(Comment.usuario == usuario).order_by(Comment.id.desc()).limit(5).all()
        result.append({
            "usuario": usuario,
            "apariciones": count,
            "ultimos_tonos": [c.tono for c in comments],
            "ultimos_textos": [c.texto[:100] if c.texto else "" for c in comments],
        })
    return result
