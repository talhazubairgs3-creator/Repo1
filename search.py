"""
Search logic. Operates only on data already sitting in memory (a list of
video dicts + their embeddings) — never touches the network or the
database directly. That makes it simple to test and simple to audit.
"""
import re
from datetime import datetime

import numpy as np

from common import WATCH_URL


def _fmt_date(published):
    if not published:
        return None
    try:
        return datetime.fromisoformat(published.replace("Z", "+00:00")).strftime("%b %-d, %Y")
    except Exception:
        return None


def _snippet(text, phrase, radius=90):
    """The text around the first hit, so you can see exactly why a video
    matched instead of trusting a bare score."""
    m = re.search(re.escape(phrase), text, flags=re.IGNORECASE)
    if not m:
        return ""
    start, end = max(0, m.start() - radius), min(len(text), m.end() + radius)
    snippet = re.sub(r"\s+", " ", text[start:end].strip())
    return ("…" if start > 0 else "") + snippet + ("…" if end < len(text) else "")


def search_keyword(videos, query, top_k=None):
    """Brute-force exact substring match, case-insensitive, over every
    video's title + description. No cap on how many videos are checked, no
    truncation of the description. If the exact phrase isn't there, the
    video isn't returned — no guessing, no partial credit."""
    phrase = re.sub(r"\s+", " ", (query or "").strip().lower())
    if not phrase:
        return []

    rows = []
    for v in videos:
        title, desc = v["title"] or "", v["description"] or ""
        in_title = phrase in re.sub(r"\s+", " ", title.lower())
        in_desc = phrase in re.sub(r"\s+", " ", desc.lower())
        if not in_title and not in_desc:
            continue
        snippet = "" if in_title else _snippet(desc, query.strip())
        rows.append({"video_id": v["video_id"], "title": title, "url": WATCH_URL.format(v["video_id"]),
                     "in_title": in_title, "snippet": snippet, "published": _fmt_date(v.get("published"))})

    rows.sort(key=lambda x: x["in_title"], reverse=True)   # title matches first
    return rows[:top_k] if top_k else rows


def search_semantic(model, videos, embeddings, query, top_k=8, min_score=0.20):
    """Ranks videos by embedding similarity to the question. Can surface
    related videos that use different words — and occasionally a loosely
    related one too, which is the known tradeoff of this mode."""
    if not videos:
        return []
    q_emb = model.encode([query], normalize_embeddings=True)[0]
    scores = embeddings @ q_emb
    order = np.argsort(scores)[::-1]

    rows = []
    for i in order:
        if scores[i] < min_score:
            break
        v = videos[i]
        rows.append({"video_id": v["video_id"], "title": v["title"], "url": WATCH_URL.format(v["video_id"]),
                     "percent": round(100 * max(0.0, min(1.0, float(scores[i])))), "in_title": False,
                     "snippet": "", "published": _fmt_date(v.get("published"))})
        if len(rows) >= top_k:
            break
    return rows
