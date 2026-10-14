# Video Finder — Loy Machedo edition (hosted version)

A search page for youtube.com/@LoyMachedo. Ask a question, get the exact
videos that match — either by exact words (brute-force, literal) or by
topic (semantic). Runs as a real website with its own database, and updates
itself daily with only the videos uploaded since the last check.

## How it's built (plain terms)

| File | What it does |
|---|---|
| `common.py` | Settings — channel URL, model name, where secrets come from |
| `db.py` | The filing cabinet — saves/loads videos in a local SQLite file |
| `fetch.py` | The only file that talks to YouTube |
| `search.py` | Answers a question against whatever's already loaded |
| `update_videos.py` | The daily chore: list channel → diff against DB → fetch only what's missing → save |
| `app.py` | The website itself: the search page, plus one hidden, password-protected `/update` door |

The public website never contacts YouTube. Only the daily update job does,
and only when it's pinged through the locked door.

## Deploying it — step by step

### 1. Put the code on GitHub
Create a new repo (public is fine — no secrets are in this code, ever) and
push this folder to it.

### 2. Create the web service on Render
1. [render.com](https://render.com) → **New → Web Service** → connect your repo.
2. **Build command:** `pip install -r requirements.txt`
3. **Start command:** `python app.py`
4. Under the service's **Disks** tab, add a persistent disk:
   - Mount path: `/data`
   - Size: 1 GB is plenty

### 3. Set environment variables (Render dashboard → Environment)
| Variable | Value |
|---|---|
| `DB_PATH` | `/data/videos.db` |
| `YOUTUBE_API_KEY` | your key (see below if you don't have one) |
| `UPDATE_SECRET` | any long random string you make up — this is the password that protects the daily update |

**Getting a free YouTube API key** (skip if you already have one):
1. https://console.cloud.google.com/apis/library/youtube.googleapis.com → **Enable**
2. **APIs & Services → Credentials → Create Credentials → API key**
3. Restrict it to **YouTube Data API v3** only, Application restrictions: **None**
4. Copy the key into `YOUTUBE_API_KEY` above

### 4. Deploy, then do the first full index
This is the slow, one-time step — reading 4,000+ videos' descriptions takes
a few minutes even with an API key, and this one run is too long to trigger
over a normal web request. Do it directly instead:

1. Once the service is deployed, open its **Shell** tab in the Render dashboard.
2. Run: `python update_videos.py`
3. Wait for `Added N new video(s)` in the output.

### 5. Set up the daily automatic update
This repo includes `.github/workflows/daily-update.yml`, a free scheduled
job that pings your site once a day.

1. In your GitHub repo: **Settings → Secrets and variables → Actions**
2. Add two repository secrets:
   - `UPDATE_SECRET` — the exact same value you put in Render
   - `APP_URL` — your Render URL, e.g. `https://video-finder.onrender.com` (no trailing slash)
3. That's it — it runs automatically every day at 6am UTC. You can change
   the time by editing the `cron:` line in that file, and you can trigger it
   manually any time from the repo's **Actions** tab (**Run workflow**).

**Note:** GitHub pauses scheduled workflows on repos with no activity for
60 days. If that ever happens, just open **Actions → Daily video index
update → Run workflow** once to wake it back up — it doesn't affect the
website itself, only the daily top-up.

### 6. Test it
- Open your Render URL — you should see the search page.
- Search for something you know he's covered — you should get results.
- Manually trigger the update once to confirm it works:
  `curl -H "X-Update-Token: your-secret" https://your-app.onrender.com/update`
  You should get back `{"ok": true, "added": 0, ...}` (0, since you already
  ran the full index in step 4).

## Security notes

- **No secrets in the code.** `YOUTUBE_API_KEY` and `UPDATE_SECRET` only
  ever exist as environment variables on Render (and as GitHub Actions
  secrets, which GitHub encrypts). The repo can be public.
- **The public website is read-only at request time.** It never contacts
  YouTube while answering a search, and it opens the database in a mode
  where SQLite itself refuses write attempts — even a bug in the search
  code couldn't corrupt the data.
- **The `/update` endpoint is locked.** It checks the secret token using a
  timing-safe comparison, and refuses everything if the secret isn't set at
  all (fails locked, not open).
- **Everything fetched is public information** — the channel's own public
  video list, and title/description/tags via YouTube's official API. No
  private data, no login, nothing an ordinary visitor to the channel
  couldn't already see themselves.
