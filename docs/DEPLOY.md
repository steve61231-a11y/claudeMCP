# Putting Mũũgĩ online — the clicking version

No terminal. No downloads. Everything here happens in a browser.

If you are comfortable with a command line, `docs/DEPLOY_FLY.md` is cheaper
and slightly faster. This file exists because that one is not usable by
somebody who does not already know what `curl` is, and a deploy guide that
cannot be followed is not a deploy guide.

Three steps that matter, and section 3 is the one people skip and then lose
an evening to: **a deploy that never happened looks exactly like a deploy
that happened.** Everything else in this file is setup; 3c is the thirty
seconds that tells you whether any of it took effect.

Two services, and they are deliberately separate:

- **Neon** holds the database — every mention collected, every report.
- **Railway** runs the app — the code that does the analysis and serves the page.

They are kept apart on purpose. When everything lived on Render, one unpaid
invoice took down the app *and* locked the data away at the same time. Split,
that cannot happen: if the app host stops, the data is untouched and you point
a new server at it.

---

## 1. Neon — the database

Already done if you have a connection string. If not:

1. **neon.tech** → **Sign up** → continue with GitHub.
2. It opens **Create project**. Name it `muugi`. Region: whichever is closest.
3. **Create**. It takes a few seconds.
4. On the project page there is a **Connection string** box. Press the copy
   icon. It looks like:
   `postgresql://user:pass@ep-xxxx.eu-central-1.aws.neon.tech/neondb?sslmode=require`

Keep that tab open — you need the string again in step 2.

Free tier, no card. It sleeps when idle and wakes on the next connection, so
the first request after a quiet spell takes a second or two. That is a delay,
not a risk: nothing is deleted.

---

## 2. Railway — the app

1. Go to **railway.com** → **Login** → continue with GitHub.
2. **New Project** → **Deploy from GitHub repo**.
3. If it asks for permission, allow Railway to see `steve61231-a11y/claudeMCP`.
4. Pick **claudeMCP**. It starts building straight away — that is fine, it
   will fail the first time because it has no database yet. Expected.
5. Click the **service box** that appears on the canvas → **Variables** tab →
   **New Variable**, and add these four. (Everything from here on happens
   inside the service, not the project.) Paste the real values; they are in your password manager.

   | Name | Value |
   |---|---|
   | `DATABASE_URL` | the Neon string from step 1 |
   | `ANTHROPIC_API_KEY` | your Anthropic key |
   | `PULSE_API_KEY` | any long random string you make up — it is the password for your own API |
   | `ALLOWED_ORIGINS` | leave until step 7, then put the real URL here |

6. **Click the service box on the canvas first**, then **Settings** →
   **Networking** → **Public Networking** → **Generate Domain**. Copy the
   `something.up.railway.app` address it creates.

   Railway has *two* Settings pages and only one of them has Networking. The
   project settings — reached from the canvas background rather than from a
   service — do not, which reads as "that step does not exist". Open the
   service first and the section is there.

   If there is no service box on the canvas at all, the GitHub import did not
   attach and nothing below will work; redo step 2.
7. Back to **Variables** → set `ALLOWED_ORIGINS` to that full address,
   including `https://`.
8. **Deployments** tab → **Redeploy**.

Watch the log. You want to see alembic run the database migrations, then
`Uvicorn running`. Then open your domain — the page should load.

### Optional, add later

None of these stop it working, and the report says plainly when one is
missing rather than quietly producing a thinner answer:

| Name | What it buys |
|---|---|
| `SOCIALCRAWL_API_KEY` | TikTok, Instagram, LinkedIn, X. Without it there is no social media in the corpus at all, so reports lean on news coverage and read more critical than reality |
| `NEWSAPI_KEY` | more news sources |
| `X_USERNAME`, `X_EMAIL`, `X_PASSWORD` + `ENABLE_TWSCRAPE=true` | free X scraping with a burner account |

---

## 3. Make deploys push instead of pull — and prove they happened

Step 2 leaves Railway *watching* the repository through its GitHub App. That
works until it does not, and when it stops it does not say so anywhere you
would look.

**What happened on 9 October 2026.** The service's **Settings → Source**
still showed the right repository and the right branch. Two lines below it,
in grey: *"Auto deploy unavailable"*, and in red: *"Could not load branches.
Retry"*. Railway had lost its GitHub access for this repository and had
quietly stopped deploying. Twenty-three commits were pushed over the next
three days and **not one of them ever ran.** The site stayed up, serving
9 October's code, looking entirely live. Every fix pushed in that window was
then tested against a container that did not contain it, so each one appeared
not to work and the next three hours went into re-fixing something that was
already fixed.

So: two workflows in this repository, and one habit.

### 3a. Give GitHub a token so it can push the code itself

1. **Railway** → your project → **Settings → Tokens** → **Create token**.
   Make it a **project** token, scoped to the production environment. Copy it
   once — Railway will not show it again.
2. **GitHub repo** → **Settings → Secrets and variables → Actions** →
   **New repository secret** → name it exactly `RAILWAY_TOKEN`, paste, save.

Never paste that token into a chat, an issue or a commit. It can deploy code
to a running service; treat it like the database password.

Optionally, in the **Variables** tab next door:

| Name | Default if unset | What it is |
|---|---|---|
| `ENGINE_URL` | the production domain | the live origin. Also used by `keepalive` and `deploy-drift` |
| `RAILWAY_SERVICE` | `muugi` | the service name inside the Railway project |
| `DEPLOY_VIA_ACTIONS` | on | set to `false` to turn pushing off, if Railway's own connection is healthy and you would rather not deploy twice per push |

From then on, every push to the deploy branch runs `.github/workflows/
deploy.yml`, which uploads the code with the Railway CLI, waits for the build,
**then asks `/api/health` whether the commit it just pushed is the one
answering** — and fails red if it is not. GitHub pushing to Railway cannot be
broken by Railway losing sight of GitHub.

### 3b. Let something notice when neither path ran

`.github/workflows/deploy-drift.yml` runs four times a day, compares the
branch's newest deployable commit against what the live container reports,
and opens one reusable issue on this repository if they have disagreed for
more than 45 minutes. It closes it again by itself. It needs no secrets.

It ignores commits that only touch `docs/` or `*.md`, because `deploy.yml`
skips those too and an alarm that cries wolf is one its reader learns to
skip.

### 3c. The habit: ask the container, not the dashboard

Open **`/api/health`** on your domain and read `build_detail`:

```json
"build_detail": {
  "commit": "4646ce4",
  "built_at": "2026-10-12T03:41:08Z",
  "source": "image build stamp"
}
```

`commit` is what is *running*. If it is not the commit you just pushed, your
fix is not live, whatever the Railway dashboard says and however green the
deployment card looks. That is the one check worth doing before concluding a
fix did not work — this project has now lost three evenings to skipping it.

If `commit` is `null`, the container could not establish its own identity;
`built_at` still answers "is this today's build?".

---

## 4. Confirm the backup

The nightly dump protects whatever is in Neon. It had failed every night for
a month before this was noticed, so confirm it yourself rather than assuming:

1. GitHub repo → **Settings → Secrets and variables → Actions**.
2. `DATABASE_URL` → **Update secret** → paste the same Neon string.
3. GitHub repo → **Actions** tab → in the **left sidebar** click **db-backup**
   → a **Run workflow** button appears on the right → press it.

Green means you have a backup. If it is red, it now opens an issue on the
repository explaining what went wrong, instead of failing in silence.

---

## What this costs

- **Neon**: free.
- **Railway**: needs a card. Usage-based, roughly **$5/month** for this app —
  it is small and it mostly sits idle. Check their current pricing yourself;
  that figure is an estimate, not a quote.

The old Render setup was **$32/month**: a `standard` web service at $25 that
was sized for memory the app does not need, plus a separate SearXNG service at
$7 for an optional data source. Both are gone. See `DEPLOY_FLY.md` for the
measurements behind that.

### Why `sleepApplication` is false in `railway.json`

A report takes tens of minutes and runs in the background — the browser polls
for progress, so the run itself produces no web traffic. Any "sleep when idle"
feature only watches web traffic, so it would suspend the machine in the
middle of a run that was working perfectly, and the page would sit on a report
that never finishes. Paying a few dollars a month is cheaper than reproducing
a bug this project already spent a week removing.

---

## If something goes wrong

- **Build fails immediately** — check Railway picked up `railway.json`. It
  points at `engine/Dockerfile.render`; Railway looks for a `Dockerfile` at
  the root otherwise and finds nothing.
- **Deploy succeeds, page is blank or 502** — look for `Uvicorn running` in
  the log. If it stops before that, the migrations could not reach the
  database: check `DATABASE_URL`.
- **Page loads, every report fails** — open `/api/health` on your domain. It
  reports which model backend is live and whether it is production-grade.
- **Reports come back thin or empty** — that is the app telling the truth, not
  breaking. The run-health banner on the page distinguishes "the model did not
  answer" from "there was nothing to find".
- **A fix you pushed does not work** — before anything else, open
  `/api/health` and read `build_detail.commit`. If it is not the commit you
  pushed, there is nothing wrong with the fix; it is not running. See 3c.
- **"Could not load branches. Retry"** or **"Auto deploy unavailable"** under
  the service's **Settings → Source** — Railway's GitHub App can no longer
  reach this repository, so it has stopped deploying. The branch shown beside
  it may be perfectly correct; the branch is not the problem. In order:

  Press **Retry** first — sometimes it is a transient GitHub API failure.
  If it stays red, work through the appendix at the end of this file, which
  covers the four states the GitHub App can be in and which one you are
  looking at. Do not guess: three of the four look almost identical.

  You do not have to wait for any of that to ship code. With `RAILWAY_TOKEN`
  set (3a), GitHub Actions deploys regardless — the Railway → GitHub
  direction is then only a convenience.

---

## Appendix: reconnecting Railway to GitHub

Written out in full because the short version — "check the repository is in
the list" — is exactly the kind of advice that sent an operator to look at a
setting that was already correct. Three of the four states below show
`claudeMCP` in the list. Only one of them works.

### Facts about this repository that change the steps

Checked, not assumed:

| | |
|---|---|
| Owner `steve61231-a11y` is a **user account**, not an organisation | So this is the *personal* settings path. Most guides online describe the organisation path, which has an extra "approve this app for the org" step that does not exist here and cannot be the problem |
| The repository is **public** | Railway needs no permission at all to *clone* it. So "Could not load branches" is not a missing read scope — it is the installation itself being absent, suspended, or holding a permission it has not been granted |
| The default branch **is** the deploy branch | After any reconnect, Railway picks the right branch by itself. You do not have to hunt for `claude/magical-mayer-yzxz5s` in a dropdown |

### Start from Railway, not from GitHub

Railway's own **Settings → Source** usually has a *Configure GitHub App* or
*Manage GitHub permissions* link. **Use that one.** It opens the installation
page for the GitHub account Railway is actually connected as — which may not
be the account you are logged into GitHub with in that browser.

That matters: if Railway signed in with a different GitHub identity, its App
is installed on *that* account and can never see `steve61231-a11y/claudeMCP`,
no matter what you tick. Railway → your avatar → *Account Settings* shows
which GitHub username it holds. If it is not `steve61231-a11y`, nothing in
this appendix will fix it; you need to connect Railway to the right account.

### "Applications" is three lists, and two of them are decoys

GitHub → avatar → **Settings → Applications** has three tabs. Railway can
appear in all three:

| Tab | What it is | Does it affect deploys? |
|---|---|---|
| **Installed GitHub Apps** | the App's access to your *repositories* | **Yes. This is the only one that matters.** |
| Authorized GitHub Apps | the App's access to your *identity* | No |
| Authorized OAuth Apps | the older login integration | No |

Revoking and re-granting the OAuth entry is the most common wasted hour
here: it fixes logging in, and changes nothing about deploys.

Direct link, which skips all of it: **<https://github.com/settings/installations>**

### The four states, and how to tell them apart

Open that link and find Railway.

**1 — Railway is not in the list at all.** The installation was removed. There
is nothing to configure: the Configure button you are looking for does not
exist. Reinstall from the Railway side — project → service → Settings →
Source → connect the repository again, and accept GitHub's install prompt.

**2 — Railway is listed. Click Configure. Under *Repository access*,
`claudeMCP` is missing.** Either select *All repositories*, or keep *Only
select repositories* and add it with **Select repositories**. Two traps: the
search box only matches repositories **you own**, and the change does not
apply until you press the green **Save** at the bottom.

**3 — Railway is listed, `claudeMCP` is selected, and there is a yellow or
grey banner saying the App is requesting new permissions** — wording along
the lines of *"Railway is requesting updated permissions"* with a **Review
request** or **Accept new permissions** button. This is the state that best
matches your symptom: it worked for months, then stopped on one specific day
without you touching anything. When an App adds a permission, GitHub
**withholds the new one until you approve it**, and the App keeps running
half-crippled in the meantime. Approving it is the whole fix.

**4 — Railway is listed, `claudeMCP` is selected, no banner, and it still
does not work.** Look for a **Suspended** badge next to the name. A suspended
installation still shows every repository as granted — which is why "the repo
is listed" proves nothing. If suspended, there is an **Unsuspend** option; if
not, scroll to the bottom of the Configure page and **Uninstall**, then
reinstall from Railway's side as in state 1.

### Confirming it worked

Not on GitHub — GitHub will happily show a healthy grant for an integration
that is doing nothing. Go back to Railway → the service → **Settings →
Source**:

- the red *"Could not load branches"* is gone and the branch dropdown opens;
- *"Auto deploy unavailable"* is gone;
- the 🚫 beside the repository name is gone.

Then push anything, or press **Deploy**, and check `/api/health` →
`build_detail.commit` against what you pushed. That last step is the only one
that proves it, and it is section 3c.

### If none of it works

You do not need Railway's GitHub connection to deploy. `RAILWAY_TOKEN` and
section 3a make GitHub push the code instead, and that path cannot be broken
by anything in this appendix. The App connection is a convenience; it is not
the product.
