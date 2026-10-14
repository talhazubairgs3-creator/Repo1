"""
Video Finder — the public website.

Read-only at request time: every search is answered from data already
sitting in the local SQLite database. This file never calls YouTube or any
other outside service while answering a search — the only outbound network
calls anywhere in this project happen in fetch.py, which only
update_videos.py imports.

This file does two things:
  1. Serves the search page (the Gradio UI below).
  2. Exposes one extra, password-protected route, /update, which runs the
     daily incremental-update job when pinged by the scheduler. Nobody can
     trigger it without the secret token set in UPDATE_SECRET.
"""
import base64
import hashlib
import hmac
import html
import os
import threading
import traceback
from pathlib import Path

import gradio as gr
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sentence_transformers import SentenceTransformer
from starlette.routing import Route

import db
import search
import update_videos
from common import CREATOR_NAME, MODEL_NAME, UPDATE_SECRET, VIDEO_ID_RE

APP_TITLE = f"Video Finder — {CREATOR_NAME}"
RESULTS = 8
PORTRAIT_PATH = Path(__file__).parent / "portrait.png"


class UserError(Exception):
    """An error whose message is safe and helpful to show to the user."""


print("Loading language model...")
model = SentenceTransformer(MODEL_NAME)
db.init_db()

# In-memory cache of the database contents, so every search is instant.
# Reloaded automatically whenever the database's "last_checked_at" marker
# changes — i.e. right after the daily update adds new videos.
_cache = {"videos": [], "embeddings": None, "last_checked_at": None}
_cache_lock = threading.Lock()
_update_lock = threading.Lock()   # prevents two update runs overlapping


def _refresh_cache_if_needed():
    last = db.get_meta("last_checked_at")
    if last != _cache["last_checked_at"] or _cache["embeddings"] is None:
        with _cache_lock:
            if last != _cache["last_checked_at"] or _cache["embeddings"] is None:
                videos, embeddings = db.load_all()
                _cache.update(videos=videos, embeddings=embeddings, last_checked_at=last)
    return _cache


# --------------------------------------------------------------------------
# Presentation — same look as the earlier version: portrait tiled into a
# repeating pattern behind the header.
# --------------------------------------------------------------------------
def _portrait_data_uri():
    if not PORTRAIT_PATH.exists():
        return ""
    return f"data:image/png;base64,{base64.b64encode(PORTRAIT_PATH.read_bytes()).decode()}"


_PORTRAIT_URI = _portrait_data_uri()

CSS = """
<style>
.gradio-container { max-width: 860px !important; margin: 0 auto !important; }
footer { display: none !important; }

.vf-pattern {
  position: relative; overflow: hidden; border-radius: 22px;
  background: #f4e400; padding: 46px 16px 34px; text-align: center; margin: 18px 0 22px;
}
.vf-pattern::before {
  content: ""; position: absolute; inset: -20%;
  background-image: url('__PORTRAIT__');
  background-size: 76px 115px; background-repeat: repeat;
  transform: rotate(-8deg); opacity: .5;
}
.vf-pattern::after {
  content: ""; position: absolute; inset: 0;
  background: radial-gradient(ellipse at center, rgba(244,228,0,.55) 0%, rgba(244,228,0,.93) 72%);
}
.vf-pattern-inner { position: relative; z-index: 1; }
.vf-badge { display: inline-block; font-size: .78em; font-weight: 700; letter-spacing: .06em;
  text-transform: uppercase; padding: 4px 12px; border-radius: 999px;
  background: rgba(0,0,0,.08); color: #1a1a1a; }
.vf-pattern h1 { font-size: 2.15em; line-height: 1.15; margin: 14px 0 8px; font-weight: 800; color: #111; }
.vf-pattern p { opacity: .78; font-size: 1.02em; max-width: 520px; margin: 0 auto; color: #111; }

.vf-meta { opacity: .7; margin: 10px 2px 4px; font-size: .95em; }
.vf-note { font-size: .85em; opacity: .6; margin: 2px 2px 8px; }
.vf-card { display: flex; gap: 16px; align-items: center; padding: 12px; margin: 10px 0;
  border: 1px solid rgba(128,128,128,.28); border-radius: 14px; text-decoration: none !important;
  color: inherit !important; transition: transform .12s, box-shadow .12s, border-color .12s; }
.vf-card:hover { transform: translateY(-2px); border-color: #f4c400;
  box-shadow: 0 6px 20px rgba(244,196,0,.25); }
.vf-card img { width: 200px; max-width: 38%; border-radius: 10px; flex-shrink: 0; }
.vf-title { font-size: 1.05em; font-weight: 650; margin-bottom: 8px; line-height: 1.3; }
.vf-tag { font-size: .82em; font-weight: 650; }
.vf-tag2 { font-size: .78em; opacity: .6; margin-left: 8px; }
.vf-snippet { font-size: .85em; opacity: .75; margin-top: 6px; font-style: italic; line-height: 1.4; }
.vf-watch { font-size: .82em; opacity: .55; margin-left: 8px; }
.vf-empty { padding: 22px; text-align: center; opacity: .8;
  border: 1px dashed rgba(128,128,128,.4); border-radius: 14px; margin-top: 12px; }
</style>
""".replace("__PORTRAIT__", _PORTRAIT_URI)

HERO = CSS + f"""
<div class="vf-pattern">
  <div class="vf-pattern-inner">
    <div class="vf-badge">AI video search</div>
    <h1>Find the right {html.escape(CREATOR_NAME)} video, instantly</h1>
    <p>Ask a question in plain English and get the videos from his channel that answer it.</p>
  </div>
</div>
"""


def _message(text):
    return f"<div class='vf-empty'>{html.escape(text)}</div>"


def _percent_colour(pct):
    if pct >= 70:
        return "#16a34a"
    if pct >= 40:
        return "#ca8a04"
    return "#6b7280"


def render_results(video_count, coverage, results, mode):
    if not results:
        if mode == "keyword":
            return _message("That exact word or phrase doesn't appear in any video's title or "
                            "description. Try different wording, or switch to Semantic search.")
        return _message("No sufficiently related videos found. Try describing the topic another way.")

    parts = [f"<div class='vf-meta'>Searched all {video_count:,} videos on {html.escape(CREATOR_NAME)}'s "
             f"channel &middot; {len(results)} result{'s' if len(results) != 1 else ''}</div>"]
    if coverage < 0.5:
        parts.append("<div class='vf-note'>Descriptions weren't available for most videos, "
                     "so matching is based mainly on titles.</div>")

    for r in results:
        if not VIDEO_ID_RE.match(r["video_id"]):
            continue   # doesn't look like a real YouTube id — skip rather than build a link from it
        title = html.escape(r["title"])
        date_html = f"<span class='vf-tag2'>{html.escape(r['published'])}</span>" if r.get("published") else ""
        if mode == "keyword":
            where = "Title" if r["in_title"] else "Description"
            badge = f"<span class='vf-tag' style='color:#16a34a'>✓ Exact match — {where}</span>"
            extra = date_html
            if r["snippet"]:
                extra += f"<div class='vf-snippet'>“{html.escape(r['snippet'])}”</div>"
        else:
            colour = _percent_colour(r["percent"])
            badge = f"<span class='vf-tag' style='color:{colour}'>{r['percent']}% match</span>"
            extra = date_html
        parts.append(
            f"<a class='vf-card' href='{r['url']}' target='_blank' rel='noopener'>"
            f"<img loading='lazy' src='https://i.ytimg.com/vi/{r['video_id']}/mqdefault.jpg'>"
            f"<div><div class='vf-title'>{title}</div>{badge}{extra}"
            f"<span class='vf-watch'>Watch on YouTube ↗</span></div></a>")
    return "".join(parts)


def answer(question, mode_label, progress=gr.Progress()):
    question = (question or "").strip()[:300]   # simple length cap — abuse guard
    mode = "keyword" if mode_label.startswith("Keyword") else "semantic"
    try:
        if len(question) < 3:
            raise UserError("Type a question or topic to search for.")
        cache = _refresh_cache_if_needed()
        if not cache["videos"]:
            raise UserError("The video index is empty — the first update hasn't run yet. Try again shortly.")
        if mode == "keyword":
            results = search.search_keyword(cache["videos"], question)
        else:
            results = search.search_semantic(model, cache["videos"], cache["embeddings"], question, top_k=RESULTS)
        coverage = sum(1 for v in cache["videos"] if v["description"]) / len(cache["videos"])
        return render_results(len(cache["videos"]), coverage, results, mode)
    except UserError as e:
        return _message(str(e))
    except Exception:
        traceback.print_exc()
        return _message("Something went wrong on that search. Please try again.")


def build_ui():
    with gr.Blocks(title=APP_TITLE) as demo:
        gr.HTML(HERO)
        question = gr.Textbox(label="What do you want to know?", lines=2,
                              placeholder="e.g. I'm feeling stuck in my career. Where should I start?")
        mode = gr.Radio(["Keyword search (exact words only)", "Semantic search (related topics)"],
                        value="Keyword search (exact words only)", label="Search mode")
        go = gr.Button("Find videos", variant="primary")
        out = gr.HTML()
        go.click(answer, [question, mode], out, api_name=False)
        question.submit(answer, [question, mode], out, api_name=False)
    return demo


demo = build_ui()

# --------------------------------------------------------------------------
# The protected /update endpoint.
#
# gr.mount_gradio_app is the officially documented way to combine a plain
# FastAPI app with a Gradio UI (https://gradio.app, "Sharing your App" /
# "Server mode" guides). We create our own FastAPI app, mount the Gradio UI
# onto it at "/", then insert our own /update route at the FRONT of the
# route list — Starlette checks routes in order, and a Mount at "/" would
# otherwise swallow every path including "/update" before it gets a chance
# to match.
# --------------------------------------------------------------------------
async def update_handler(request: Request):
    token = request.headers.get("X-Update-Token", "") or request.query_params.get("token", "")
    if not UPDATE_SECRET or not hmac.compare_digest(token, UPDATE_SECRET):
        return PlainTextResponse("Forbidden", status_code=403)
    if not _update_lock.acquire(blocking=False):
        return PlainTextResponse("An update is already running", status_code=409)
    try:
        added = update_videos.run_update()
        return JSONResponse({"ok": True, "added": added, "total": db.video_count()})
    except Exception:
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": "update failed — see server logs"}, status_code=500)
    finally:
        _update_lock.release()


fastapi_app = FastAPI()
fastapi_app = gr.mount_gradio_app(fastapi_app, demo, path="/")
fastapi_app.router.routes.insert(0, Route("/update", update_handler, methods=["GET"]))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    uvicorn.run(fastapi_app, host="0.0.0.0", port=port)
