"""
Search logic. Operates only on data already sitting in memory (a list of
video dicts + their embeddings) — never touches the network or the
database directly. That makes it simple to test and simple to audit.
"""
import re
from datetime import datetime

import numpy as np

from common import WATCH_URL

# Maps "smart"/typographic punctuation to its plain equivalent, so a search
# typed with a straight apostrophe still matches a description that happens
# to use a curly one (or vice versa). This is a 1-for-1 character swap, so
# it never changes string length or character positions — that matters
# because match positions found this way are reused to slice the ORIGINAL,
# untranslated text for the on-screen snippet, preserving its real spelling.
_QUOTE_MAP = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-",
})


def _fmt_date(published):
    if not published:
        return None
    try:
        return datetime.fromisoformat(published.replace("Z", "+00:00")).strftime("%b %-d, %Y")
    except Exception:
        return None


def _build_pattern(query):
    """Builds a whole-word, whole-phrase, case-insensitive pattern from the
    query. "an" will match "an" as its own word but NOT buried inside
    "Canada" or "channel". A multi-word query like "Vince McMahon" still
    requires that exact wording and word order, as real words — but is
    still allowed to sit right next to punctuation, so a search for
    "mcmahon" still matches "McMahon's". Any run of whitespace between the
    query's words (a space, a line break, several spaces) matches a
    description's whitespace in between, however that text happens to be
    formatted."""
    phrase = (query or "").translate(_QUOTE_MAP).lower().strip()
    phrase = re.sub(r"\s+", " ", phrase)
    if not phrase:
        return None
    words = [re.escape(w) for w in phrase.split(" ")]
    return re.compile(rf"\b{r'\s+'.join(words)}\b", flags=re.IGNORECASE)


def _find_match(text, pattern):
    if not text:
        return None
    return pattern.search(text.translate(_QUOTE_MAP))   # translate only, so indices still line up with the original


def _snippet_from_match(text, match, radius=90):
    """The text around a hit, so you can see exactly why a video matched
    instead of trusting a bare score. Built from the ORIGINAL text (not the
    quote-normalized copy used for matching), so it displays exactly as he
    wrote it."""
    start, end = max(0, match.start() - radius), min(len(text), match.end() + radius)
    snippet = re.sub(r"\s+", " ", text[start:end].strip())
    return ("…" if start > 0 else "") + snippet + ("…" if end < len(text) else "")


def search_keyword(videos, query, top_k=None):
    """Brute-force exact, whole-word match, case-insensitive, over every
    video's title + description (never tags, never comments). No cap on how
    many videos are checked, no truncation of the description. If the exact
    words aren't there, the video isn't returned — no guessing, no partial
    credit, no fuzzy correction."""
    pattern = _build_pattern(query)
    if pattern is None:
        return []

    rows = []
    for v in videos:
        title, desc = v["title"] or "", v["description"] or ""
        in_title = _find_match(title, pattern) is not None
        desc_match = None if in_title else _find_match(desc, pattern)
        if not in_title and not desc_match:
            continue
        snippet = _snippet_from_match(desc, desc_match) if desc_match else ""
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
