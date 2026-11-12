import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useLocation, useNavigate } from 'react-router-dom';
import { api } from '../api';
import { ApiError, getToken, setToken } from '../api/client';
import type { AuthOut, Role, User } from '../api/types';
import { atLeast } from '../api/types';

interface AuthState {
  user: User | null;
  loading: boolean;
  error: string | null;
  signIn: (out: AuthOut) => void;
  signOut: () => void;
  /** the user after editing his own profile (cabinet) */
  setMe: (u: User) => void;
  hasRole: (...roles: Role[]) => boolean;
  isStaff: boolean;
  isAdmin: boolean;
}

const Ctx = createContext<AuthState | null>(null);

interface TgWebApp {
  initData?: string; ready?: () => void; expand?: () => void; colorScheme?: string; setHeaderColor?: (c: string) => void;
  openLink?: (url: string) => void; openTelegramLink?: (url: string) => void;
  disableVerticalSwipes?: () => void; isVersionAtLeast?: (v: string) => boolean;
  BackButton?: { show: () => void; hide: () => void; onClick: (f: () => void) => void; offClick: (f: () => void) => void };
}
export function tgWebApp(): TgWebApp | null {
  const w = window as unknown as { Telegram?: { WebApp?: TgWebApp } };
  return w.Telegram?.WebApp ?? null;
}
export function insideTelegram(): boolean { return !!tgWebApp()?.initData; }
/** What scrolls the page: inside Telegram it's #root (html.tg, app.css), elsewhere the window. */
export function pageScroller(): Window | HTMLElement {
  return (document.documentElement.classList.contains('tg') && document.getElementById('root')) || window;
}

/** Telegram's own ‹ Back in the header on every page but the one the Mini App opened with. */
export function useTgBackButton() {
  const loc = useLocation();
  const navigate = useNavigate();
  useEffect(() => {
    const bb = insideTelegram() ? tgWebApp()?.BackButton : undefined;
    if (!bb) return;
    if (loc.key === 'default') { bb.hide(); return; }
    const back = () => navigate(-1);
    bb.show();
    bb.onClick(back);
    return () => bb.offClick(back);
  }, [loc.key, navigate]);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const wa = tgWebApp();
      try {
        if (wa?.initData) {
          wa.ready?.();
          wa.expand?.();
          // a vertical swipe would collapse/close the Mini App instead of scrolling (Bot API 7.7+); older clients:
          // the document never scrolls, #root does (html.tg in app.css), so there is no page top to pull down from
          if (wa.isVersionAtLeast?.('7.7')) wa.disableVerticalSwipes?.();
          document.documentElement.classList.add('tg');
          try { wa.setHeaderColor?.('#032D6A'); } catch { /* old clients */ }
        }
        let me: User | null = null;
        if (getToken()) {
          try {
            me = await api.auth.me();
          } catch (e) {
            // expired/invalid stored token: drop it; inside Telegram re-auth via initData below
            if (!(e instanceof ApiError && e.status === 401) || !wa?.initData) throw e;
            setToken(null);
          }
        }
        if (!me && wa?.initData) {
          const out = await api.auth.telegramWebApp(wa.initData);
          setToken(out.access_token);
          me = out.user ?? (await api.auth.me());
        }
        if (!cancelled && me) setUser(me);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Ошибка входа');
        setToken(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const signIn = useCallback((out: AuthOut) => {
    setToken(out.access_token);
    setUser(out.user);
    setError(null);
  }, []);
  const signOut = useCallback(() => {
    setToken(null);
    setUser(null);
    qc.clear();
  }, [qc]);

  const value = useMemo<AuthState>(() => {
    const hasRole = (...roles: Role[]) => !!user && roles.includes(user.role);
    return {
      user, loading, error, signIn, signOut, setMe: setUser, hasRole,
      isStaff: atLeast(user, 'operator'),
      isAdmin: atLeast(user, 'admin'),
    };
  }, [user, loading, error, signIn, signOut]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error('useAuth outside AuthProvider');
  return v;
}
