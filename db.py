"""
SQLite storage for the video index.

Kept deliberately simple:
- One file, two small tables (`videos`, and a one-row-per-key `meta`).
  No ORM, no migrations framework — just SQL.
- Every query below uses "?" placeholders, never string-built SQL. That's
  what prevents SQL injection, though in this app it's belt-and-suspenders:
  search itself runs over data already loaded into memory (see search.py),
  so a search query never reaches the database at all.
- WAL (write-ahead log) mode lets a search in progress keep reading while
  the update job writes new rows, without either blocking the other.
- The web app always opens the database read-only (SQLite enforces this at
  the OS level via the `mode=ro` URI) — so even a bug in the search code
  can't accidentally write to or corrupt the data. Only update_videos.py
  ever opens it for writing.
"""
import json
import sqlite3
from pathlib import Path

import numpy as np

from common import DB_PATH, EMBED_DIM

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    video_id    TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    tags        TEXT NOT NULL DEFAULT '[]',
    published   TEXT,
    fetched_at  TEXT NOT NULL,
    embedding   BLOB
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def _connect(readonly=False):
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    if readonly and Path(DB_PATH).exists():
        return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = _connect()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def get_meta(key, default=None):
    if not Path(DB_PATH).exists():
        return default
    conn = _connect(readonly=True)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    except sqlite3.OperationalError:
        row = None
    conn.close()
    return row[0] if row else default


def set_meta(key, value):
    conn = _connect()
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def existing_video_ids():
    if not Path(DB_PATH).exists():
        return set()
    conn = _connect(readonly=True)
    try:
        rows = conn.execute("SELECT video_id FROM videos").fetchall()
    except sqlite3.OperationalError:
        rows = []
    conn.close()
    return {r[0] for r in rows}


def upsert_videos(rows):
    """rows: list of dicts with video_id, title, description, tags (list),
    published, fetched_at, embedding (np.ndarray or None)."""
    conn = _connect()
    conn.executemany(
        "INSERT INTO videos (video_id, title, description, tags, published, fetched_at, embedding) "
        "VALUES (:video_id, :title, :description, :tags, :published, :fetched_at, :embedding) "
        "ON CONFLICT(video_id) DO UPDATE SET "
        "title=excluded.title, description=excluded.description, tags=excluded.tags, "
        "published=excluded.published, fetched_at=excluded.fetched_at, embedding=excluded.embedding",
        [
            {
                "video_id": r["video_id"], "title": r["title"], "description": r["description"],
                "published": r.get("published"), "fetched_at": r["fetched_at"],
                "tags": json.dumps(r.get("tags") or []),
                "embedding": r["embedding"].astype("float32").tobytes() if r.get("embedding") is not None else None,
            }
            for r in rows
        ],
    )
    conn.commit()
    conn.close()


def load_all():
    """Everything needed for search: a list of video dicts, and their
    embeddings stacked into one array in the same order."""
    if not Path(DB_PATH).exists():
        return [], np.zeros((0, EMBED_DIM), dtype="float32")
    conn = _connect(readonly=True)
    try:
        rows = conn.execute(
            "SELECT video_id, title, description, tags, published, embedding FROM videos"
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    conn.close()

    videos, embeds = [], []
    for video_id, title, description, tags_json, published, emb_blob in rows:
        videos.append({"video_id": video_id, "title": title, "description": description,
                       "tags": json.loads(tags_json or "[]"), "published": published})
        embeds.append(np.frombuffer(emb_blob, dtype="float32") if emb_blob else np.zeros(EMBED_DIM, dtype="float32"))
    embeddings = np.vstack(embeds) if embeds else np.zeros((0, EMBED_DIM), dtype="float32")
    return videos, embeddings


def video_count():
    if not Path(DB_PATH).exists():
        return 0
    conn = _connect(readonly=True)
    try:
        n = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
    except sqlite3.OperationalError:
        n = 0
    conn.close()
    return n
