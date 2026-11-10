// CRM sign-in: Telegram only (own button, no widget iframe).
// Made with Claude (Anthropic) - Homes in Georgia - Created by Irakli Kvirkvelia
import { FormEvent, useEffect, useState } from 'react';
import { Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../api';
import { IS_MOCK } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { Button, Field, Input } from '../components/ui';

type TgUser = Record<string, unknown>;
type TgLogin = { auth: (o: { bot_id: string; request_access?: string }, cb: (u: TgUser | false) => void) => void };

function loadTelegram(): Promise<TgLogin> {
  const w = window as unknown as { Telegram?: { Login?: TgLogin } };
  if (w.Telegram?.Login) return Promise.resolve(w.Telegram.Login);
  return new Promise((ok, fail) => {
    const s = document.createElement('script');
    s.src = 'https://telegram.org/js/telegram-widget.js?22';
    s.async = true;
    s.onload = () => (w.Telegram?.Login ? ok(w.Telegram.Login) : fail(new Error('no Telegram')));
    s.onerror = () => fail(new Error('no Telegram'));
    document.head.appendChild(s);
  });
}

const TG_ICON = 'M9.78 18.65l.28-4.23 7.68-6.92c.34-.31-.07-.46-.52-.19L7.74 13.3 3.64 12c-.88-.25-.89-.86.2-1.3l15.97-6.16c.73-.33 1.43.18 1.15 1.3l-2.72 12.81c-.19.91-.74 1.13-1.5.71L12.6 16.3l-1.99 1.93c-.23.23-.42.42-.83.42z';

export function LoginPage() {
  const { user, signIn, error: bootError } = useAuth();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [botId, setBotId] = useState('');
  const [login, setLogin] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(bootError);
  const next = params.get('next') || '/';
  const showPassword = IS_MOCK || params.get('password') === '1';

  useEffect(() => {
    api.config().then((c) => setBotId(String((c as unknown as { bot_id?: string }).bot_id || ''))).catch(() => setBotId(''));
    loadTelegram().catch(() => undefined);
  }, []);

  if (user) return <Navigate to={next} replace />;

  const done = (p: Promise<Awaited<ReturnType<typeof api.auth.login>>>) => {
    setBusy(true);
    setError(null);
    p.then((out) => { signIn(out); navigate(next, { replace: true }); })
      .catch((e) => setError(e instanceof Error ? e.message : 'Не удалось войти'))
      .finally(() => setBusy(false));
  };
  const submit = (e: FormEvent) => { e.preventDefault(); done(api.auth.login(login.trim(), password)); };
  const telegram = () => {
    if (IS_MOCK) return done(api.auth.telegramLogin({ id: 1 }));
    if (!botId) return setError('Вход через Telegram сейчас недоступен');
    setError(null);
    loadTelegram()
      .then((tg) => tg.auth({ bot_id: botId, request_access: 'write' }, (u) => { if (u) done(api.auth.telegramLogin(u)); }))
      .catch(() => setError('Не удалось открыть Telegram. Обновите страницу'));
  };

  return (
    <div className="tg-login">
      <div className="tg-login-box">
        <img className="tg-login-logo" src="/brand/logo-white.svg" alt="Homes in Georgia" />
        <h1>Вход в CRM</h1>
        <p className="tg-login-sub">For Homes in Georgia staff only</p>
        <button type="button" className="tg-login-btn" onClick={telegram} disabled={busy}>
          <span className="tg-login-ico"><svg viewBox="0 0 24 24" aria-hidden="true"><path d={TG_ICON} /></svg></span>
          {busy ? 'Входим…' : 'Войти через Telegram'}
        </button>
        <p className="tg-login-note">Вход подтверждается в приложении Telegram</p>
        {error && <div className="tg-login-err">{error}</div>}
        {showPassword && (
          <form onSubmit={submit} className="col tg-login-form">
            <Field label="Логин"><Input value={login} onChange={(e) => setLogin(e.target.value)} autoComplete="username" required /></Field>
            <Field label="Пароль"><Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required /></Field>
            <Button type="submit" size="lg" block loading={busy}>Войти</Button>
          </form>
        )}
      </div>
      <div className="tg-login-copy">© {new Date().getFullYear()} Homes in Georgia</div>
    </div>
  );
}
