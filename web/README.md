# Pulse Intelligence — standalone frontend (no Lovable)

`index.html` is the **entire app in one file** — no build step, no framework install,
no monthly credits. It runs live against your Pulse engine and falls back to a demo
view when it can't reach an API (so it always looks alive).

`pulse_app.html` is the same content without the `<html>/<head>/<body>` wrapper — it's
what gets published as a Claude artifact preview. Edit `pulse_app.html`, then rebuild
`index.html` with:

```sh
{ printf '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">\n<title>Pulse Intelligence</title></head><body>\n'; cat pulse_app.html; printf '\n</body></html>\n'; } > index.html
```

## Host it free (pick one)

**Cloudflare Pages (recommended)**
1. Go to https://dash.cloudflare.com → Workers & Pages → Create → Pages → Upload assets.
2. Drag in the `web/` folder (or just `index.html`). Deploy. You get a `*.pages.dev` URL, live 24/7.

**Netlify** — https://app.netlify.com/drop → drag `index.html` in. Done.

**GitHub Pages** — repo Settings → Pages → deploy from branch → `/web` (or move `index.html` to root).

## Connect it to your engine (one-time)

1. Open the hosted site → **Settings** tab.
2. Set **API base URL** to your engine, e.g. `https://pulse-engine-efnd.onrender.com`.
3. Set **API key** if `PULSE_API_KEY` is configured on the engine (else leave blank).
4. **Save & connect** — the status pill turns green "Live".

## Let the browser call the engine (CORS)

Add your hosted URL to the engine's **`ALLOWED_ORIGINS`** env var on Render
(comma-separated), e.g. `https://pulse-intel.pages.dev`. Save → it redeploys.
Without this the browser blocks the requests (you'll see "Unreachable (CORS…)" on the
Settings test). Endpoints used: `/api/report`, `/api/issue-map`, `/api/report/{id}`,
`/api/network`, `/api/baraza`, `/api/admin/metrics`, `/api/admin/source-check`, `/api/health`.
