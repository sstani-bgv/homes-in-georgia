// Small fetch client: /api prefix, JWT bearer from localStorage, 401 → login.
export const TOKEN_KEY = 'hig_token';
export const IS_MOCK = import.meta.env.VITE_MOCK === '1';

export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(status: number, message: string, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export function getToken(): string | null {
  try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
}
export function setToken(t: string | null) {
  try {
    if (t) localStorage.setItem(TOKEN_KEY, t);
    else localStorage.removeItem(TOKEN_KEY);
  } catch { /* storage unavailable */ }
}

let onUnauthorized: () => void = () => {
  const path = window.location.pathname;
  if (!path.startsWith('/login')) {
    const back = encodeURIComponent(path + window.location.search);
    window.location.href = `/login?next=${back}`;
  }
};
export function setUnauthorizedHandler(fn: () => void) { onUnauthorized = fn; }

/** Telegram Mini App initData, when the page runs inside Telegram. */
export function telegramInitData(): string {
  const w = window as unknown as { Telegram?: { WebApp?: { initData?: string } } };
  return w.Telegram?.WebApp?.initData || '';
}

let reauth: Promise<boolean> | null = null;
/** Inside Telegram an expired token is renewed from initData instead of sending the user to /login. */
function telegramReauth(): Promise<boolean> {
  const initData = telegramInitData();
  if (!initData) return Promise.resolve(false);
  reauth ??= fetch('/api/auth/telegram-webapp', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ init_data: initData }),
  }).then(async (r) => {
    if (!r.ok) return false;
    const d = (await r.json()) as { token?: string; access_token?: string };
    const t = d.access_token ?? d.token;
    if (t) setToken(t);
    return !!t;
  }).catch(() => false).finally(() => { setTimeout(() => { reauth = null; }, 0); });
  return reauth;
}

type Query = Record<string, string | number | boolean | null | undefined>;

function qs(q?: Query): string {
  if (!q) return '';
  const p = new URLSearchParams();
  Object.entries(q).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') p.set(k, String(v));
  });
  const s = p.toString();
  return s ? `?${s}` : '';
}

const ERROR_TEXT: Record<string, string> = {
  already_taken: 'Заявку уже взял другой оператор',
  taken_by_other: 'Объект уже взят другим агентом',
  not_registered: 'Ваш Telegram не зарегистрирован в системе. Обратитесь к администратору',
  invalid_credentials: 'Неверный логин или пароль',
  forbidden: 'Нет доступа к этой странице или действию',
  not_found: 'Не найдено: запись удалена или ссылка устарела',
};

async function request<T>(method: string, path: string, body?: unknown, query?: Query, retried = false): Promise<T> {
  const url = path + qs(query);
  if (IS_MOCK) {
    const { mockFetch } = await import('./mock/server');
    try {
      return (await mockFetch(method, url, body)) as T;
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) onUnauthorized();
      throw e;
    }
  }
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  let payload: BodyInit | undefined;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }
  const res = await fetch(`/api${url}`, { method, headers, body: payload });
  if (res.status === 401 && !path.startsWith('/auth/')) {
    setToken(null);
    if (!retried && (await telegramReauth())) return request<T>(method, path, body, query, true);
    onUnauthorized();
    throw new ApiError(401, 'Требуется вход');
  }
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try { data = JSON.parse(text); } catch { data = text; }
  }
  if (!res.ok) {
    const d = data as { detail?: unknown; code?: string; message?: string } | null;
    if (res.status === 401 && path === '/auth/login') throw new ApiError(401, 'Неверный логин или пароль', 'invalid_credentials');
    if (res.status === 401 && path.startsWith('/auth/telegram')) throw new ApiError(401, 'Не удалось войти через Telegram. Попробуйте ещё раз', 'unauthorized');
    if (res.status === 401) throw new ApiError(401, 'Сессия истекла, войдите снова', 'unauthorized');
    let code: string | undefined;
    let msg = `Ошибка ${res.status}`;
    if (d && typeof d === 'object') {
      const det = d.detail as { code?: string; message?: string } | string | undefined;
      if (typeof det === 'string') { msg = det; code = d.code || det; }
      else if (det && typeof det === 'object') { code = det.code; msg = det.message || det.code || msg; }
      else if (d.message) { msg = d.message; code = d.code; }
      if (Array.isArray(d.detail)) msg = 'Проверьте поля формы';
    }
    if (code && ERROR_TEXT[code]) msg = ERROR_TEXT[code];
    throw new ApiError(res.status, msg, code);
  }
  return data as T;
}

export const http = {
  get: <T>(p: string, q?: Query) => request<T>('GET', p, undefined, q),
  post: <T>(p: string, b?: unknown, q?: Query) => request<T>('POST', p, b ?? {}, q),
  patch: <T>(p: string, b?: unknown) => request<T>('PATCH', p, b ?? {}),
  put: <T>(p: string, b?: unknown) => request<T>('PUT', p, b ?? {}),
  del: <T>(p: string) => request<T>('DELETE', p),
};

/** Accept either `{items,total}` or a bare array. */
export function itemsOf<T>(d: { items: T[] } | T[] | undefined | null): T[] {
  if (!d) return [];
  return Array.isArray(d) ? d : d.items || [];
}

export function mediaUrl(path?: string | null): string {
  if (!path) return '';
  if (path.startsWith('/api/')) {
    const tk = getToken();
    return tk && !path.includes('token=') ? `${path}${path.includes('?') ? '&' : '?'}token=${encodeURIComponent(tk)}` : path;
  }
  if (/^(https?:|data:|blob:|\/)/.test(path)) return path;
  const t = getToken();
  return `/api/media/${path}${t ? `?token=${encodeURIComponent(t)}` : ''}`;
}
