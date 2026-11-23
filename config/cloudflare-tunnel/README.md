# Cloudflare Tunnel and Access for the QuantResearch API

This publishes the API that runs on your PC (`127.0.0.1:8000`) so a dashboard hosted on Cloudflare Pages can reach it.
The Python backend stays on your Windows PC because it needs the local Parquet warehouse and MetaTrader 5.

```
Internet user
  -> Cloudflare Access (login gate)            in front of EVERYTHING
       -> Cloudflare Pages (the dashboard)     only after login
       -> Cloudflare Tunnel -> 127.0.0.1:8000  only after login
```

> **The API has no authentication of its own. Cloudflare Access is the only thing protecting it.**
> Never disable Access, never share the tunnel address, and never allow all origins (`*`) in `QR_CORS_ORIGINS`.
> `NEXT_PUBLIC_API_URL` is compiled into the dashboard's JavaScript, so the **dashboard must be behind Access too**,
> otherwise anyone can read the tunnel address from the page source. Do the Access steps (3) **before** you run the tunnel (5).

## Choose your hostnames (this matters)

Use a custom domain on Cloudflare for both, under the same registered domain:

- Dashboard: `dashboard.yourdomain.com` (Pages project > Custom domains)
- API: `api.quantresearch.yourdomain.com`

Same-site hostnames let the browser send the Access cookie to the API from the dashboard. If you use the default
`quantresearch-dashboard.pages.dev` instead, the API is on a different site, and browsers that block third-party
cookies (Safari by default, Chrome in some modes) will refuse the API calls. It can work elsewhere, but it is the fragile choice.

## Setup

1. **Install cloudflared** (PowerShell): `winget install --id Cloudflare.cloudflared`, then open a new terminal.
2. **Create the tunnel:**
   ```
   cloudflared tunnel login
   cloudflared tunnel create quantresearch
   cloudflared tunnel route dns quantresearch api.quantresearch.yourdomain.com
   ```
   `create` prints the tunnel ID and writes `<tunnel-id>.json` in `%USERPROFILE%\.cloudflared`. Keep that file private.
   Copy `config.yml` from this folder to `%USERPROFILE%\.cloudflared\config.yml` and replace `<your-tunnel-id>` and the hostname.
3. **Create the Access applications** at https://one.dash.cloudflare.com (Zero Trust > Access controls > Applications > Add > Self-hosted).
   Zero Trust is free for up to 50 users; you will be asked to pick a team name the first time.
   - **App 1, the dashboard:** the hostnames `dashboard.yourdomain.com` **and** `*.quantresearch-dashboard.pages.dev`.
     The wildcard matters: every Pages deployment also gets its own `<hash>.quantresearch-dashboard.pages.dev` address,
     and an application for the bare domain alone leaves those open. (Alternatively, in the Pages project open
     Settings > General > Access policy and enable it, which covers the production and preview addresses.)
   - **App 2, the API:** the hostname `api.quantresearch.yourdomain.com`.
   - **Policy for both:** Action Allow, Include: Emails, your email address (or a list). Session duration: 24 hours.
   - **App 2 CORS:** in its settings turn on the option to bypass `OPTIONS` requests to the origin (the exact label may differ
     slightly). Browsers send the cross-origin preflight without cookies, so Access would otherwise block it before the API can answer.
4. **Build and deploy the dashboard** with the API address compiled in. With the GitHub workflow, add the repository secrets
   `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` and `NEXT_PUBLIC_API_URL` (value `https://api.quantresearch.yourdomain.com`),
   then push to `main`. It is a GitHub secret, not a Pages dashboard variable, because the workflow builds the site itself and
   Pages dashboard variables are only read when Cloudflare runs the build. Never commit the real value; `frontend/.env.local` is git-ignored.
5. **Start the API and the tunnel.** The API must allow exactly the dashboard's origin, with credentials:
   ```bash
   export QR_CORS_ORIGINS=https://dashboard.yourdomain.com
   PYTHONPATH=src python -m quantresearch.api.server
   ```
   then in a second terminal `cloudflared tunnel run quantresearch`. For automatic start with Windows, run
   `cloudflared service install` from an administrator terminal.
6. **First sign-in.** Open `https://api.quantresearch.yourdomain.com/api/health` in your browser once and complete the Access login
   (this sets the cookie for the API), then open the dashboard. When the 24 hours run out, the dashboard shows
   "Could not reach the API": open the API address again to sign in, and reload.

## Check it is locked down

- In a private window, open the dashboard and the API address. Both must show the Cloudflare login page, not content.
- `curl https://api.quantresearch.yourdomain.com/api/health` with no credentials must return the Access login page or a redirect, not JSON.
- Open `https://<something>.quantresearch-dashboard.pages.dev` (any deployment address from the Pages dashboard). It must also ask for login.

## What this does and does not guarantee

- The tunnel address is not in the git repository, but it **is** in the dashboard's JavaScript. That is only safe because the dashboard is behind Access.
- CORS is limited to the origins you list, which stops other websites from calling the API from a browser.
  It does **not** stop `curl` or server-side callers: someone with the address **and** a valid Access session or service token can still call it.
- Access is a login in front of the API, not a check inside it. If you want defence in depth, the API could also verify the
  `Cf-Access-Jwt-Assertion` header Access adds. It does not do that today.
- Live trading remains blocked behind the activation steps in README section 23, but the paper account and all research data
  are reachable by anyone who passes the Access login. Allow only people you trust with that.

## Quick tunnel (testing only)

`cloudflared tunnel --url http://127.0.0.1:8000` gives a random `trycloudflare.com` address with **no Access in front of it**.
Do not use it with the hosted dashboard, and do not leave it running.
