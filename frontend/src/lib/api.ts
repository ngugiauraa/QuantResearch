"use client";
import { useCallback, useEffect, useRef, useState } from "react";

// In development the page runs on :3000 and the API on :8000. When built, FastAPI serves both.
export const API = typeof window !== "undefined" && window.location.port === "3000" ? "http://127.0.0.1:8000" : "";

export class ApiError extends Error {}

export async function api<T = any>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const response = await fetch(API + path, {
    method: init?.method ?? "GET",
    headers: init?.body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
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

  const load = useCallback(async () => {
    if (!path) return;
    try {
      const result = await api<T>(path);
      if (alive.current) {
        setData(result);
        setError(null);
      }
    } catch (e) {
      if (alive.current) setError((e as Error).message);
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    alive.current = true;
    setLoading(true);
    load();
    if (refreshMs) {
      const id = setInterval(load, refreshMs);
      return () => {
        alive.current = false;
        clearInterval(id);
      };
    }
    return () => {
      alive.current = false;
    };
  }, [load, refreshMs]);

  return { data, error, loading, reload: load };
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
