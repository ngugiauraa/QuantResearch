"use client";
import { useCallback, useEffect, useRef, useState } from "react";

// NEXT_PUBLIC_API_URL (set at build time) points a hosted copy of the dashboard, such as Cloudflare Pages, at the API.
// Otherwise: in development the page runs on :3000 and the API on :8000, and when built locally FastAPI serves both.
export const API: string = (
  process.env.NEXT_PUBLIC_API_URL ??
  (typeof window !== "undefined" && window.location.port === "3000" ? "http://127.0.0.1:8000" : "")
).replace(/\/+$/, "");

export class ApiError extends Error {}

const FETCH_TIMEOUT_MS = 45_000;

export async function api<T = any>(path: string, init?: { method?: string; body?: unknown; timeoutMs?: number }): Promise<T> {
  let response: Response;
  // A request the server never answers would otherwise leave the page loading forever.
  const controller = init?.timeoutMs ? new AbortController() : null;
  const timer = controller ? setTimeout(() => controller.abort(), init!.timeoutMs) : null;
  try {
    response = await fetch(API + path, {
      signal: controller?.signal,
      method: init?.method ?? "GET",
      headers: init?.body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
      // A hosted dashboard calls the API on another origin, and Cloudflare Access recognises you by its cookie.
      credentials: API ? "include" : "same-origin",
    });
  } catch {
    if (controller?.signal.aborted) {
      throw new ApiError(`The API did not answer ${path.split("?")[0]} within ${Math.round(init!.timeoutMs! / 1000)} s. It may be busy; the page will retry.`);
    }
    throw new ApiError(
      API
        ? "Could not reach the API. If your Cloudflare Access session has expired, open the API address once in this browser to sign in again, then reload."
        : "Could not reach the API. Is the server running?",
    );
  } finally {
    if (timer) clearTimeout(timer);
  }
  const text = await response.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    throw new ApiError(`Server returned something unreadable (status ${response.status})`);
  }
  if (!response.ok) {
    const detail = data?.detail;
    throw new ApiError(typeof detail === "string" ? detail : `Request failed (status ${response.status})`);
  }
  return data as T;
}

export function useFetch<T = any>(path: string | null, refreshMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const alive = useRef(true);
  const busy = useRef(false);

  // A poll is skipped while the previous one is still running, so a slow endpoint cannot pile up requests.
  const load = useCallback(async (force = false) => {
    if (!path || (busy.current && !force)) return;
    busy.current = true;
    try {
      const result = await api<T>(path, { timeoutMs: FETCH_TIMEOUT_MS });
      if (alive.current) {
        setData(result);
        setError(null);
      }
    } catch (e) {
      if (alive.current) setError((e as Error).message);
    } finally {
      busy.current = false;
      if (alive.current) setLoading(false);
    }
  }, [path]);
  const reload = useCallback(() => load(true), [load]);

  useEffect(() => {
    alive.current = true;
    setLoading(true);
    load(true);
    if (refreshMs) {
      const id = setInterval(() => load(), refreshMs);
      return () => {
        alive.current = false;
        clearInterval(id);
      };
    }
    return () => {
      alive.current = false;
    };
  }, [load, refreshMs]);

  return { data, error, loading, reload };
}

export async function waitForJob(id: string, onUpdate?: (job: any) => void): Promise<any> {
  for (;;) {
    const job = await api(`/api/jobs/${id}`);
    onUpdate?.(job);
    if (job.status !== "running") return job;
    await new Promise((r) => setTimeout(r, 2000));
  }
}

export const fmt = (value: unknown, digits = 2): string => {
  if (value === null || value === undefined || value === "") return "–";
  if (typeof value === "number") return Number.isFinite(value) ? value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits }) : "–";
  return String(value);
};
export const pct = (value: unknown, digits = 2): string => (typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(digits)}%` : "–");
export const when = (value: unknown): string => {
  if (!value) return "–";
  const d = new Date(typeof value === "number" ? value * 1000 : String(value));
  return Number.isNaN(d.getTime()) ? String(value) : d.toISOString().slice(0, 16).replace("T", " ") + " UTC";
};
