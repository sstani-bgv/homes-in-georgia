import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
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
  hasRole: (...roles: Role[]) => boolean;
  isStaff: boolean;
  isAdmin: boolean;
}

const Ctx = createContext<AuthState | null>(null);

interface TgWebApp {
  initData?: string; ready?: () => void; expand?: () => void; colorScheme?: string; setHeaderColor?: (c: string) => void;
  openLink?: (url: string) => void; openTelegramLink?: (url: string) => void;
}
export function tgWebApp(): TgWebApp | null {
  const w = window as unknown as { Telegram?: { WebApp?: TgWebApp } };
  return w.Telegram?.WebApp ?? null;
}
export function insideTelegram(): boolean { return !!tgWebApp()?.initData; }

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
      user, loading, error, signIn, signOut, hasRole,
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
