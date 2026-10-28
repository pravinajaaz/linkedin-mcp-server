# Railway deployment (fork branch `railway`)

Remote LinkedIn MCP for the claude.ai connector and the Claude mobile app.
Streamable HTTP behind Google OAuth for one account. Headed Chromium on Xvfb,
with browser traffic going out through a sticky residential proxy.

## What is different from upstream

- `linkedin_mcp_server/remote_main.py` runs the normal CLI startup over HTTP
  and adds `AllowlistGoogleProvider`. Only verified emails in
  `ALLOWED_EMAILS` get a working token.
- `comment_on_post` and `react_to_post` tools (`scraping/post_actions.py`).
  Both preview by default; `confirm=True` writes. Comments with em dashes are refused.
- `Dockerfile.railway` (generated: stock `Dockerfile` + `railway/Dockerfile.tail`)
  and `railway/start.sh`:
  - hands the root-owned volume to `pwuser`
  - starts `railway/auth_relay.py` on loopback
  - runs the server under tini

  The relay is needed because Chromium cannot answer ScrapeOps' bare
  `Proxy-Authenticate: Basic` challenge.

## Service settings

| Setting | Value |
| --- | --- |
| Source | GitHub `pravinajaaz/linkedin-mcp-server`, branch `railway` |
| Dockerfile | variable `RAILWAY_DOCKERFILE_PATH=Dockerfile.railway` |
| Volume | mount path `/home/pwuser/.linkedin-mcp` |
| Replicas | 1. Only one process may own the browser profile. |
| Sleep | off |

Variables:

| Name | Value |
| --- | --- |
| `BASE_URL` | `https://<service>.up.railway.app` |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google Cloud → APIs & Services → Credentials → OAuth client (Web). Redirect URI `https://<service>.up.railway.app/auth/callback` |
| `ALLOWED_EMAILS` | `pravin@drupaldevelopersstudio.com` |
| `JWT_SIGNING_KEY` | `openssl rand -hex 32`. Keep it stable or connectors must sign in again. |
| `UPSTREAM_PROXY_HOST` / `UPSTREAM_PROXY_PORT` | `residential-proxy.scrapeops.io` / `8181` |
| `UPSTREAM_PROXY_USERNAME` | `scrapeops.sticky_session=7.country=us`. Keep the number fixed; changing it changes the IP. |
| `UPSTREAM_PROXY_PASSWORD` | ScrapeOps API key |
| `LOG_LEVEL` | `WARNING` |

Leave `HEADLESS` alone. The image sets `HEADLESS=false` on purpose:
LinkedIn's Cloudflare blocks headless Chromium (verified 2026-10-28, direct and via proxy).

## First sign-in, and every re-login

1. Set `LINKEDIN_LOGIN=1` and `PORT=6080`, then redeploy.
2. In the deploy logs, find the viewer URL
   (`http://127.0.0.1:6080/vnc_lite.html#?path=websockify%3Ftoken%3D...`).
   Replace `http://127.0.0.1:6080` with `https://<service>.up.railway.app`.
   The token changes on every restart, and the window restarts every 30
   minutes. If the viewer says "connection closed", read the live token with
   `railway ssh -- sh -c 'cut -d: -f1 /tmp/linkedin-mcp-viewer-*/tokens'`.
3. Sign in to LinkedIn in that browser, including 2FA. The process exits once the session is saved.
4. `railway variable delete LINKEDIN_LOGIN`, then `railway variable delete PORT`
   (each delete redeploys). Finish deleting both before any deploy. A deploy that
   still boots in login mode moves the new session into `invalid-state-*`; it is
   put back when that login process ends, so redeploy and check
   `source-state.json` is back.

While step 1 is live, anyone holding the token URL can drive the login browser.
Don't share it, and finish quickly.

## Connect clients

- claude.ai → Settings → Connectors → Add custom connector →
  `https://<service>.up.railway.app/mcp`, then sign in with Google.
  The connector then appears in the iOS and Android apps.
- Claude Code: `claude mcp add --transport http linkedin https://<service>.up.railway.app/mcp`

## Rebasing on upstream

```
git fetch origin && git rebase origin/main
railway/generate-dockerfile.sh
uv run python scripts/generate_scraping_architecture.py
uv run pytest -q
git push --force-with-lease fork railway
```
