# Cloudflare Tunnel for the QuantResearch API

This publishes the API that runs on your PC (`127.0.0.1:8000`) at a Cloudflare address, so the dashboard hosted on
Cloudflare Pages can reach it. The Python backend stays on your Windows PC because it needs the local Parquet warehouse
and MetaTrader 5.

> **Read this first: the API has no login.** Anyone who can reach the tunnel address can read your research and
> account data and use the paper-trading and risk endpoints. And because `NEXT_PUBLIC_API_URL` is compiled into the
> dashboard's public JavaScript, **anyone who can open the Pages site can read the tunnel address.** Do not post
> either address publicly. To make it actually private, put Cloudflare Access in front (see the end of this file).
> Live trading stays blocked behind the activation steps in README section 23, but the paper account is not.

## Option A: a named tunnel (needs a domain on Cloudflare, free plan is fine)

1. **Install cloudflared** (PowerShell): `winget install --id Cloudflare.cloudflared`, then open a new terminal.
2. **Log in:** `cloudflared tunnel login`. A browser opens; pick the domain. This saves a certificate in `%USERPROFILE%\.cloudflared`.
3. **Create the tunnel:** `cloudflared tunnel create quantresearch`. Note the tunnel ID it prints; a credentials file
   `<tunnel-id>.json` is written next to the certificate. Keep that file private.
4. **Point a hostname at it:** `cloudflared tunnel route dns quantresearch api.yourdomain.com`.
5. **Configure:** copy `config.yml` from this folder to `%USERPROFILE%\.cloudflared\config.yml`. Replace
   `<tunnel-id-placeholder>` and `<tunnel-id>` with the tunnel ID, and `api.quantresearch.yourdomain.com` with your hostname.
6. **Allow the dashboard's origin** and start the API (Git Bash, from the repository root). The value is the address of
   your Pages site, which you get in step 7:
   ```bash
   export QR_CORS_ORIGINS=https://quantresearch-dashboard.pages.dev
   PYTHONPATH=src python -m quantresearch.api.server
   ```
7. **Run the tunnel:** `cloudflared tunnel run quantresearch`. Check `https://api.yourdomain.com/api/health` returns JSON.
8. **Tell the dashboard where the API is:** set `NEXT_PUBLIC_API_URL=https://api.yourdomain.com` where the dashboard is
   built. For the GitHub workflow that is the repository **variable** of that name (Settings > Secrets and variables >
   Actions > Variables); for Pages' own Git build it is Settings > Variables and Secrets in the Pages project. It is read
   at build time, so redeploy after changing it.

To start the tunnel automatically with Windows: `cloudflared service install` (run from an administrator terminal).

## Option B: a quick tunnel (no domain, random address)

```powershell
cloudflared tunnel --url http://127.0.0.1:8000
```

It prints an address like `https://something-random.trycloudflare.com`. The address changes every time you restart it,
so you would have to rebuild the dashboard each time. Good for a quick look, not for daily use.

## Making it private: Cloudflare Access (free for up to 50 users)

The safest setup serves the dashboard and the API from **one** address and puts a login in front of it, so no
cross-origin settings are needed and nothing is exposed without a login:

1. Build the dashboard without `NEXT_PUBLIC_API_URL` (`cd frontend && npx next build`). The local API already serves it.
2. Point the tunnel at the API as above and use only that one hostname.
3. In the Cloudflare dashboard open **Zero Trust > Access > Applications > Add an application > Self-hosted**, enter the
   hostname, and add a policy that allows only your own email address.

Cloudflare then asks for a one-time email code before any request reaches your PC. Putting Access on the Pages site and the
API as two separate hostnames is possible but needs extra cookie and CORS work, so use the single-hostname setup unless
you need Pages specifically.
