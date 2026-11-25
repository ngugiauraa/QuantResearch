// Forwards /api/* on quantresearch.pages.dev to the Railway backend.
// Cloudflare Access has already checked the user before this runs; we pass
// the Access token on so the backend can verify it too.
export async function onRequest({ request, env }) {
  const origin = env.API_ORIGIN;
  if (!origin) return new Response("API_ORIGIN not configured", { status: 500 });

  const url = new URL(request.url);
  const headers = new Headers(request.headers);
  if (!headers.get("Cf-Access-Jwt-Assertion")) {
    const m = (headers.get("Cookie") || "").match(/CF_Authorization=([^;]+)/);
    if (m) headers.set("Cf-Access-Jwt-Assertion", m[1]);
  }
  headers.delete("Host");

  return fetch(origin + url.pathname + url.search, {
    method: request.method,
    headers,
    body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
    redirect: "manual",
  });
}
