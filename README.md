# yt-app

Free, no-sign-up YouTube downloader — single videos or full playlists, as MP4 or MP3, with an optional one-file ZIP.

- **Backend:** FastAPI + yt-dlp (`main.py`, port 4444 locally, JWT-protected API)
- **Frontend:** plain HTML/CSS/JS in `frontend/` (no build step for local use)

## Run locally

```powershell
pip install -r requirements.txt
copy .env.example .env   # then fill in JWT_SECRET + ACCESS_KEY
node frontend/build-config.js
python main.py
```

Open http://localhost:4444

## Deploy

- Backend → Render (see `render.yaml`), env vars: `JWT_SECRET`, `ACCESS_KEY`, `FRONTEND_URL`, `JWT_EXPIRE_MINUTES`
- Frontend → Cloudflare Pages, build `node frontend/build-config.js`, output `frontend/`, env vars: `API_BASE_URL`, `ACCESS_KEY`
