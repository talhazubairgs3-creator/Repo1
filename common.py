"""
Shared configuration. Every secret comes from an environment variable —
nothing sensitive is ever hardcoded here or anywhere else in this project.
"""
import os
import re

CHANNEL_URL = "https://www.youtube.com/@LoyMachedo/videos"
CREATOR_NAME = "Loy Machedo"
WATCH_URL = "https://www.youtube.com/watch?v={}"
MODEL_NAME = "all-MiniLM-L6-v2"
EMBED_DIM = 384   # output size of MODEL_NAME; used to size empty/placeholder embeddings

# Set these as environment variables on whatever host runs this (Render's
# dashboard, a local .env you don't commit, etc.) — never edit them in here.
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
UPDATE_SECRET = os.getenv("UPDATE_SECRET", "").strip()   # required to trigger /update
DB_PATH = os.getenv("DB_PATH", "data/videos.db")

# A real YouTube video id: letters, digits, "-", "_", 6-15 chars. Used to
# sanity-check ids before building any URL from them — defense in depth,
# since these ids always come from YouTube's own API responses anyway.
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,15}$")
