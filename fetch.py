"""
All outbound network access lives in this one file — and only
update_videos.py ever imports it. app.py, the public web app, never does.
That means at search time, the web app makes zero outbound network calls;
it only reads the local SQLite file and runs a local embedding computation.
That's the main thing keeping its attack surface small.

Everything fetched here is public data: the channel's own public video
list, and public video metadata (title/description/tags/publish date) via
YouTube's official Data API. No private data, no scraping of anything an
ordinary visitor to the channel couldn't already see, no authentication
beyond the one read-only API key.
"""
import json
import time
import urllib.parse
import urllib.request

import yt_dlp

from common import CHANNEL_URL, WATCH_URL, YOUTUBE_API_KEY

RETRIES = 2


def _flatten(entries):
    for e in entries or []:
        if not e:
            continue
        if e.get("entries"):
            yield from _flatten(e["entries"])
        elif e.get("id"):
            yield e


def scan_channel():
    """The channel's current public video list (ids + titles) — one
    request, no API key needed. Equivalent to opening the channel's
    Videos tab and scrolling to the bottom."""
    opts = {"quiet": True, "no_warnings": True, "extract_flat": True,
            "skip_download": True, "ignoreerrors": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(CHANNEL_URL, download=False) or {}
    return [{"video_id": e["id"], "title": e.get("title") or "",
             "url": WATCH_URL.format(e["id"])} for e in _flatten(info.get("entries"))]


def fetch_details_via_api(video_ids, on_progress=None):
    """Fast path: up to 50 videos' descriptions/tags/publish dates per
    request, via the official YouTube Data API."""
    results = {}
    batches = [video_ids[i:i + 50] for i in range(0, len(video_ids), 50)]
    for i, batch in enumerate(batches, 1):
        params = urllib.parse.urlencode({"part": "snippet", "id": ",".join(batch), "key": YOUTUBE_API_KEY})
        url = f"https://www.googleapis.com/youtube/v3/videos?{params}"
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                data = json.loads(resp.read().decode())
            if "error" in data:
                raise RuntimeError(data["error"].get("message", "unknown API error"))
            for item in data.get("items", []):
                snip = item.get("snippet", {})
                results[item["id"]] = {"description": snip.get("description") or "",
                                       "tags": snip.get("tags") or [],
                                       "published": snip.get("publishedAt"), "ok": True}
        except Exception as e:
            print(f"  [warn] API batch {i}/{len(batches)} failed: {e}")
        if on_progress:
            on_progress(min(i * 50, len(video_ids)), len(video_ids))
    for vid in video_ids:
        results.setdefault(vid, {"description": "", "tags": [], "published": None, "ok": False})
    return results


def fetch_details_fallback(video_ids, on_progress=None):
    """Slow path, used only if no YOUTUBE_API_KEY is set: opens one video's
    page at a time via yt-dlp. Tolerable for daily incremental runs (a
    handful of new videos) but not for the very first, full-channel run."""
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "ignoreerrors": True}
    results = {}
    for i, vid in enumerate(video_ids, 1):
        last_error = None
        info = None
        for attempt in range(RETRIES + 1):
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(WATCH_URL.format(vid), download=False) or {}
                break
            except Exception as e:
                last_error = e
                time.sleep(0.6 * (attempt + 1))
        if info:
            upload_date = info.get("upload_date")
            published = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}" if upload_date else None
            results[vid] = {"description": info.get("description") or "", "tags": info.get("tags") or [],
                            "published": published, "ok": True}
        else:
            print(f"  [warn] {vid} failed after {RETRIES + 1} attempts: {last_error}")
            results[vid] = {"description": "", "tags": [], "published": None, "ok": False}
        if on_progress and (i % 5 == 0 or i == len(video_ids)):
            on_progress(i, len(video_ids))
    return results
