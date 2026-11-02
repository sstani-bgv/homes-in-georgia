import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../api';
import { IS_MOCK } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { Button, Field, Input } from '../components/ui';
import { BrandMark } from '../layout/AppLayout';

function TelegramWidget({ onAuth }: { onAuth: (u: Record<string, unknown>) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [bot, setBot] = useState<string | undefined>(import.meta.env.VITE_BOT_USERNAME || undefined);
  // Latest callback in a ref: the widget script is injected once per bot username, not on every parent render.
  const onAuthRef = useRef(onAuth);
  useEffect(() => { onAuthRef.current = onAuth; }, [onAuth]);

  useEffect(() => {
    if (bot) return;
    // /api/config is optional (not in the backend yet): fall back to the public bot username from ARCHITECTURE §2.
    api.config().then((c) => setBot(c.bot_username || 'HomesinGeorgia_bot')).catch(() => setBot('HomesinGeorgia_bot'));
  }, [bot]);

  useEffect(() => {
    if (!bot || !ref.current || IS_MOCK) return;
    (window as unknown as { onTelegramAuth: (u: Record<string, unknown>) => void }).onTelegramAuth = (u) => onAuthRef.current(u);
    const s = document.createElement('script');
    s.src = 'https://telegram.org/js/telegram-widget.js?22';
    s.async = true;
    s.setAttribute('data-telegram-login', bot);
    s.setAttribute('data-size', 'large');
    s.setAttribute('data-radius', '12');
    s.setAttribute('data-request-access', 'write');
    s.setAttribute('data-onauth', 'onTelegramAuth(user)');
    ref.current.innerHTML = '';
    ref.current.appendChild(s);
  }, [bot]);

  if (IS_MOCK) {
    return <Button variant="secondary" block onClick={() => onAuthRef.current({ id: 1 })}>Войти через Telegram (demo)</Button>;
  }
  return <div ref={ref} style={{ display: 'flex', justifyContent: 'center', minHeight: 40 }} />;
}

export function LoginPage() {
  const { user, signIn, error: bootError } = useAuth();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [login, setLogin] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(bootError);
  const next = params.get('next') || '/';

  if (user) return <Navigate to={next} replace />;

  const done = (p: Promise<Awaited<ReturnType<typeof api.auth.login>>>) => {
    setBusy(true);
    setError(null);
    p.then((out) => { signIn(out); navigate(next, { replace: true }); })
      .catch((e) => setError(e instanceof Error ? e.message : 'Не удалось войти'))
      .finally(() => setBusy(false));
  };
  const submit = (e: FormEvent) => { e.preventDefault(); done(api.auth.login(login.trim(), password)); };

  return (
    <div className="login-wrap">
      <div className="login-card">
        <div className="row" style={{ marginBottom: 22 }}>
          <span className="brand-mark" style={{ width: 44, height: 44, borderRadius: 12, background: 'var(--brand-soft)', display: 'grid', placeItems: 'center' }}><BrandMark size={28} /></span>
          <div>
            <div className="label label-red">Homes in Georgia</div>
            <h2 style={{ fontWeight: 900 }}>Вход в CRM</h2>
          </div>
        </div>
        <TelegramWidget onAuth={(u) => done(api.auth.telegramLogin(u))} />
        <div className="row" style={{ margin: '18px 0' }}>
          <div className="divider grow" style={{ margin: 0 }} /><span className="label">или</span><div className="divider grow" style={{ margin: 0 }} />
        </div>
        <form onSubmit={submit} className="col">
          <Field label="Логин"><Input value={login} onChange={(e) => setLogin(e.target.value)} autoComplete="username" required /></Field>
          <Field label="Пароль"><Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required /></Field>
          {error && <div className="alert alert-warn">{error}</div>}
          <Button type="submit" size="lg" block loading={busy}>Войти</Button>
        </form>
        {IS_MOCK && (
          <div className="alert alert-info mt" style={{ fontSize: 12, display: 'block' }}>
            Demo-режим: логины <b>admin</b> (Давид), <b>nino</b> (оператор), <b>georgiy</b> (агент), пароль любой.
          </div>
        )}
      </div>
    </div>
  );
}
