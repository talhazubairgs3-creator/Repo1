"""
Incremental updater — the daily chore.

Logic, deliberately simple, with two jobs now instead of one:

1. ADD new videos — list every video currently on the channel, compare
   against what's already stored, and fetch/save only the ones that are new.

2. REFRESH stale videos — YouTube's Developer Policies only allow caching
   public video metadata (title/description/tags) for 30 days before it
   must be re-checked ("reflect metadata changes... as quickly as
   possible"). So once a day, this also finds every video that hasn't been
   re-checked in the last 30 days and does one of two things:
     - if it's still listed on the channel: re-fetches its current
       title/description/tags and re-saves it, resetting its 30-day clock
     - if it's no longer listed (deleted, or made private): removes it
       from the database entirely, so the tool stops surfacing it

Both jobs reuse the exact same fetch/embed logic — see _fetch_and_embed()
below — so there's only one place that knows how to turn a list of video
ids into saved rows.

Can be run two ways:
  - Directly: `python update_videos.py` (used for the first, full run)
  - Through the website's protected /update endpoint (used for the daily
    automatic runs — see app.py)
Both paths call the same run_update() function below.
"""
import re  # used to strip URLs and extra whitespace out of descriptions before embedding them
import sys  # only used for sys.exit(0) at the very bottom
from datetime import datetime, timezone  # used to stamp every saved row with "when we last checked it"

from sentence_transformers import SentenceTransformer  # turns text into the vectors semantic search compares

import db  # our SQLite storage layer — see db.py
import fetch  # the only file allowed to contact YouTube — see fetch.py
from common import CREATOR_NAME, MODEL_NAME, YOUTUBE_API_KEY  # shared settings


def _clean(text, limit=2000):
    # Strips raw URLs (they add noise, not meaning, to the embedding) and
    # collapses any run of whitespace (spaces, tabs, line breaks) down to a
    # single space, then cuts off at `limit` characters so one extremely
    # long description can't dominate the embedding computation.
    text = re.sub(r"https?://\S+", " ", text or "")  # replace every URL with a single space
    return re.sub(r"\s+", " ", text).strip()[:limit]  # collapse whitespace, trim ends, cap the length


def _fetch_and_embed(videos, model, now):
    """Shared by both the "add new" and "refresh stale" phases below.
    Takes a list of {video_id, title, url} dicts (from fetch.scan_channel),
    fetches each one's current description/tags, computes an embedding for
    each, and returns a list of rows ready for db.upsert_videos()."""
    ids = [v["video_id"] for v in videos]  # pull out just the ids, since that's all the fetch functions need

    if YOUTUBE_API_KEY:
        # fast path: one request covers up to 50 videos at once
        details = fetch.fetch_details_via_api(ids)
    else:
        # slow path: one request per video — fine for a handful, not for hundreds
        print("  No YOUTUBE_API_KEY set — reading one video at a time. This will be slow "
             "for a large batch. (See common.py for how to set the key.)")
        details = fetch.fetch_details_fallback(ids)

    rows, texts = [], []  # rows = what gets saved to the database; texts = what gets embedded
    for v in videos:  # go through each video one at a time, in the same order as `videos`
        # look up this video's fetched description/tags/publish date;
        # fall back to empty values if the fetch failed for this one video
        d = details.get(v["video_id"], {"description": "", "tags": [], "published": None})
        tags = d.get("tags") or []  # tags can come back as None — normalize to an empty list
        # build the single text blob the embedding model will read: title,
        # then tags joined by commas, then the cleaned description — skipping
        # any piece that's empty, joined with ". " so it reads like a sentence
        texts.append(". ".join(p for p in [v["title"], ", ".join(tags), _clean(d.get("description", ""))] if p))
        rows.append({
            "video_id": v["video_id"],                   # YouTube's own id for this video — our primary key
            "title": v["title"],                          # the video's current title
            "description": d.get("description") or "",    # the video's current description (empty string if missing)
            "tags": tags,                                  # the video's current tags
            "published": d.get("published"),               # when the video was originally uploaded
            "fetched_at": now,                              # when WE checked it — this is the 30-day clock
            "embedding": None,                              # filled in just below, after we compute it
        })

    print(f"  Computing embeddings for {len(rows)} video(s)...")
    # turn every text blob into a vector in one batched call (much faster
    # than calling the model once per video); normalize_embeddings=True
    # makes the vectors comparable with a simple dot product at search time
    embeddings = model.encode(texts, normalize_embeddings=True, batch_size=64, show_progress_bar=False)
    for row, emb in zip(rows, embeddings):  # walk both lists together, in lockstep
        row["embedding"] = emb  # attach each video's computed vector to its row

    return rows  # ready to hand straight to db.upsert_videos()


def run_update():
    """Does one full check-and-update pass: adds new videos, then refreshes
    or removes stale ones. Returns the number of videos added (not counting
    refreshes/deletions, since "added" is the number most worth reporting).
    Safe to call repeatedly — every call starts by asking "what's already
    there, and how old is it?" so nothing is redone unnecessarily."""
    db.init_db()  # creates the database file/tables on first run; does nothing if they already exist
    now = datetime.now(timezone.utc).isoformat()  # one consistent "current time" used throughout this run
    print(f"[{now}] Checking {CREATOR_NAME}'s channel...")

    print("  Loading embedding model...")
    model = SentenceTransformer(MODEL_NAME)  # loaded once up front, reused by both phases below

    # ----------------------------------------------------------------
    # Phase 1: add videos that are on the channel but not in our database.
    # ----------------------------------------------------------------
    channel_videos = fetch.scan_channel()  # ask YouTube: what videos does this channel currently list?
    channel_ids = {v["video_id"] for v in channel_videos}  # just the ids, as a set, for fast lookups below
    print(f"  Channel currently lists {len(channel_videos)} videos.")

    existing_ids = db.existing_video_ids()  # ask our database: which video ids do we already have?
    new_videos = [v for v in channel_videos if v["video_id"] not in existing_ids]  # the ones we don't have yet
    print(f"  {len(new_videos)} not yet in the database.")

    added = 0  # tracks how many brand-new videos we end up saving, for the summary line at the end
    if new_videos:
        new_rows = _fetch_and_embed(new_videos, model, now)  # fetch + embed just the new ones
        db.upsert_videos(new_rows)  # save them (INSERT, since they're new — see db.py's upsert_videos)
        added = len(new_rows)
        print(f"  Added {added} new video(s).")

    # ----------------------------------------------------------------
    # Phase 2: refresh (or remove) videos we already have but haven't
    # re-checked in 30+ days — required by YouTube's Developer Policies.
    # ----------------------------------------------------------------
    stale_ids = db.stale_video_ids(max_age_days=30)  # video ids whose fetched_at is older than 30 days
    if stale_ids:
        print(f"  {len(stale_ids)} video(s) haven't been re-checked in 30+ days.")

        # split the stale ids into two groups: ones still on the channel
        # (need a fresh re-fetch) and ones no longer listed (need deletion)
        still_live = [vid for vid in stale_ids if vid in channel_ids]
        gone = [vid for vid in stale_ids if vid not in channel_ids]

        if gone:
            db.delete_videos(gone)  # remove videos that were deleted or made private on YouTube
            print(f"  Removed {len(gone)} video(s) no longer on the channel.")

        if still_live:
            # we need each stale video's title to rebuild its embedding
            # text, so look titles up from the channel listing we already
            # fetched above, rather than making another request
            title_by_id = {v["video_id"]: v["title"] for v in channel_videos}
            stale_videos = [{"video_id": vid, "title": title_by_id[vid]} for vid in still_live]
            refreshed_rows = _fetch_and_embed(stale_videos, model, now)  # re-fetch their current details
            db.upsert_videos(refreshed_rows)  # save them (UPDATE this time, since the id already exists)
            print(f"  Refreshed {len(refreshed_rows)} video(s).")
    else:
        print("  No videos are due for a refresh yet.")

    db.set_meta("last_checked_at", now)  # record when this run happened, for the website's cache check
    print(f"  Done. Total in database: {db.video_count()}.")
    return added  # the website's /update endpoint reports this number back


if __name__ == "__main__":
    run_update()  # only runs when this file is executed directly (`python update_videos.py`), not when imported
    sys.exit(0)  # explicit success exit code
