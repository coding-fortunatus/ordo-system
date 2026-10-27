import axios, { AxiosError } from "axios";

const TOKEN_KEY = "ems.token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(SUPPORT_KEY);
}

/*
 * When the school token is an Ordo support token, its expiry is kept beside
 * it. It only decides what the browser shows (a suspended school still opens
 * for support); the server enforces the expiry on every call.
 */
const SUPPORT_KEY = "ems.support.expires";

export function setSupportExpiry(expiresAt: string) {
  localStorage.setItem(SUPPORT_KEY, expiresAt);
}

/** Whether the stored token is a support token that hasn't run out. */
export function hasLiveSupportToken(): boolean {
  const expires = localStorage.getItem(SUPPORT_KEY);
  return !!expires && !!getToken() && Date.parse(expires) > Date.now();
}

export const api = axios.create({
  baseURL: "/api",
  headers: { "Content-Type": "application/json" },
});

// Django answers on the page's own origin (Traefik routes /api and /ws to it).
// The server works it out per request and the root route hands it over here
// before any page renders.
let apiOrigin = "";

export function setApiOrigin(origin: string) {
  apiOrigin = origin;
  api.defaults.baseURL = `${origin}/api`;
  platformApi.defaults.baseURL = `${origin}/api/platform`;
}

/** HTTP URL on the Django origin, for calls made outside axios. */
export function apiUrl(path: string): string {
  return (apiOrigin || window.location.origin) + path;
}

/** WebSocket URL on the Django origin, e.g. wsUrl("/ws/notifications/"). */
export function wsUrl(path: string): string {
  return (apiOrigin || window.location.origin).replace(/^http/, "ws") + path;
}

function randomHex(bytes: number): string {
  const values = crypto.getRandomValues(new Uint8Array(bytes));
  return Array.from(values, (b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * A fresh W3C trace context for one API call (spec 0006, AC-26). Django
 * continues it, so the server's trace id is this one. The sampled flag is
 * ignored server side; no browser spans are stored.
 */
export function traceparent(): string {
  return `00-${randomHex(16)}-${randomHex(8)}-01`;
}

api.interceptors.request.use((config) => {
  config.headers.traceparent ??= traceparent();
  const token = getToken();
  if (token) {
    config.headers.Authorization = `Token ${token}`;
  }
  return config;
});

/** Fired when the school turns out to be closed mid session (RequireOpenSchool listens). */
export const SCHOOL_CLOSED_EVENT = "ordo:school-closed";

const CLOSED_CODES = new Set(["school_suspended", "school_not_ready"]);

api.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    const code = (error.response?.data as { code?: string } | undefined)?.code;
    if (code && CLOSED_CODES.has(code)) {
      window.dispatchEvent(new Event(SCHOOL_CLOSED_EVENT));
    }
    if (error.response?.status === 401) {
      clearToken();
      if (!window.location.pathname.startsWith("/login")) {
        window.location.assign("/login");
      }
    }
    return Promise.reject(error);
  },
);

/*
 * The platform console (console.<base>) has its own token, kept apart from the
 * school one, sent as a Bearer token. Django never accepts either on the
 * other's host.
 */
const PLATFORM_TOKEN_KEY = "ordo.platform.token";

export function getPlatformToken(): string | null {
  return localStorage.getItem(PLATFORM_TOKEN_KEY);
}

export function setPlatformToken(token: string) {
  localStorage.setItem(PLATFORM_TOKEN_KEY, token);
}

export function clearPlatformToken() {
  localStorage.removeItem(PLATFORM_TOKEN_KEY);
}

export const platformApi = axios.create({
  baseURL: "/api/platform",
  headers: { "Content-Type": "application/json" },
});

platformApi.interceptors.request.use((config) => {
  config.headers.traceparent ??= traceparent();
  const token = getPlatformToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

platformApi.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    // Expired or revoked session. Sign in steps answer 400/410/429, not 401.
    if (error.response?.status === 401 && getPlatformToken()) {
      clearPlatformToken();
      window.location.assign("/console/login");
    }
    return Promise.reject(error);
  },
);

export interface ApiErrorEnvelope {
  detail: string;
  errors?: Record<string, string[]>;
  /** For a server error: what the user quotes to Ordo support. */
  reference?: string;
}

/**
 * The first 8 characters of a failed response's trace id (spec 0006), for a
 * server error (5xx). None when no response arrived, so nothing to look up.
 */
export function errorReference(error: unknown): string | undefined {
  if (!(error instanceof AxiosError) || !error.response || error.response.status < 500) return undefined;
  const traceId: unknown = error.response.headers?.["x-trace-id"];
  return typeof traceId === "string" && /^[0-9a-f]{32}$/.test(traceId) ? traceId.slice(0, 8) : undefined;
}

export function extractErrorEnvelope(error: unknown): ApiErrorEnvelope {
  const reference = errorReference(error);
  // The reference rides on the message, so every toast and inline error
  // that shows ``detail`` shows it too.
  const withReference = (detail: string) => (reference ? `${detail} Reference: ${reference}` : detail);
  if (error instanceof AxiosError && error.response?.data) {
    const data = error.response.data as Partial<ApiErrorEnvelope>;
    if (data.detail) {
      return { detail: withReference(data.detail), errors: data.errors, reference };
    }
  }
  return { detail: withReference("Something went wrong. Please try again."), reference };
}
