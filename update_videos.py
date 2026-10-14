"""
Incremental updater — the daily chore.

Logic, deliberately simple:
1. List every video currently on the channel (public data, one request).
2. Compare against what's already in the database.
3. For ONLY the videos not already stored, fetch their description/tags
   (fast, via the official API, if a key is set) and compute their
   embedding.
4. Save those rows. Existing videos are never re-fetched, re-scraped, or
   re-embedded.

This is exactly as thorough on day 1 (nothing stored yet -> every video is
"new", so everything gets captured) as it is on day 2 (almost everything is
already stored -> only the handful uploaded since are "new"). There's no
explicit date cutoff to manage — the database itself is the record of what's
already been captured, so "new" is simply "not already in there."

Can be run two ways:
  - Directly: `python update_videos.py` (used for the first, full run)
  - Through the website's protected /update endpoint (used for the daily
    automatic runs — see app.py)
Both paths call the same run_update() function below.
"""
import re
import sys
from datetime import datetime, timezone

from sentence_transformers import SentenceTransformer

import db
import fetch
from common import CREATOR_NAME, MODEL_NAME, YOUTUBE_API_KEY


def _clean(text, limit=2000):
    text = re.sub(r"https?://\S+", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()[:limit]


def run_update():
    """Does one full check-and-update pass. Returns the number of videos
    added. Safe to call repeatedly — every call starts by asking "what's
    already there?" so nothing is ever fetched twice."""
    db.init_db()
    now = datetime.now(timezone.utc).isoformat()
    print(f"[{now}] Checking {CREATOR_NAME}'s channel for new videos...")

    channel_videos = fetch.scan_channel()
    print(f"  Channel currently lists {len(channel_videos)} videos.")

    existing = db.existing_video_ids()
    new_videos = [v for v in channel_videos if v["video_id"] not in existing]
    print(f"  {len(new_videos)} not yet in the database.")

    if not new_videos:
        db.set_meta("last_checked_at", now)
        print("  Nothing new. Done.")
        return 0

    ids = [v["video_id"] for v in new_videos]
    if YOUTUBE_API_KEY:
        details = fetch.fetch_details_via_api(ids)
    else:
        print("  No YOUTUBE_API_KEY set — reading one video at a time. This will be slow "
             "for a large batch of new videos (fine for small daily top-ups, not for a "
             "first full run).")
        details = fetch.fetch_details_fallback(ids)

    print("  Loading embedding model...")
    model = SentenceTransformer(MODEL_NAME)

    rows, texts = [], []
    for v in new_videos:
        d = details.get(v["video_id"], {"description": "", "tags": [], "published": None})
        tags = d.get("tags") or []
        texts.append(". ".join(p for p in [v["title"], ", ".join(tags), _clean(d.get("description", ""))] if p))
        rows.append({"video_id": v["video_id"], "title": v["title"], "description": d.get("description") or "",
                     "tags": tags, "published": d.get("published"), "fetched_at": now, "embedding": None})

    print(f"  Computing embeddings for {len(rows)} new video(s)...")
    embeddings = model.encode(texts, normalize_embeddings=True, batch_size=64, show_progress_bar=False)
    for row, emb in zip(rows, embeddings):
        row["embedding"] = emb

    db.upsert_videos(rows)
    db.set_meta("last_checked_at", now)
    total = len(existing) + len(rows)
    print(f"  Added {len(rows)} new video(s). Total in database: {total}.")
    return len(rows)


if __name__ == "__main__":
    run_update()
    sys.exit(0)
