// Web side of a staff invite (/invite/<token>): «Войти через Telegram» binds the account and signs in.
import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api } from '../api';
import type { InviteInfo } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { ROLE_LABEL, fmtDateTime } from '../lib/format';
import { TG_ICON, loadTelegram } from './Login';

const GONE: Record<string, string> = { accepted: 'Это приглашение уже использовано.', expired: 'Срок приглашения истёк.', revoked: 'Приглашение отозвано.' };

export function InvitePage() {
  const { token = '' } = useParams();
  const { signIn } = useAuth();
  const navigate = useNavigate();
  const [info, setInfo] = useState<InviteInfo | null>(null);
  const [botId, setBotId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.auth.inviteInfo(token).then(setInfo).catch((e) => setError(e instanceof Error ? e.message : 'Приглашение не найдено'));
    api.config().then((c) => setBotId(String((c as unknown as { bot_id?: string }).bot_id || ''))).catch(() => setBotId(''));
    loadTelegram().catch(() => undefined);
  }, [token]);

  const accept = () => {
    if (!botId) return setError('Вход через Telegram сейчас недоступен');
    setError(null);
    loadTelegram().then((tg) => tg.auth({ bot_id: botId, request_access: 'write' }, (u) => {
      if (!u) return;
      setBusy(true);
      api.auth.acceptInvite(token, u)
        .then((out) => { signIn(out); navigate('/', { replace: true }); })
        .catch((e) => setError(e instanceof Error ? e.message : 'Не удалось принять приглашение'))
        .finally(() => setBusy(false));
    })).catch(() => setError('Не удалось открыть Telegram. Обновите страницу'));
  };

  const pending = info?.status === 'pending';
  return (
    <div className="tg-login">
      <div className="tg-login-box">
        <img className="tg-login-logo" src="/brand/logo-white.svg" alt="Homes in Georgia" />
        <h1>Приглашение в CRM</h1>
        {info && <p className="tg-login-sub">{info.invited_by ? `${info.invited_by} приглашает вас` : 'Вас приглашают'} в команду Homes in Georgia: {ROLE_LABEL[info.role].toLowerCase()}</p>}
        {pending && <>
          <button type="button" className="tg-login-btn" onClick={accept} disabled={busy}>
            <span className="tg-login-ico"><svg viewBox="0 0 24 24" aria-hidden="true"><path d={TG_ICON} /></svg></span>
            {busy ? 'Подключаем…' : 'Войти через Telegram'}
          </button>
          <p className="tg-login-note">Ваш Telegram станет входом в CRM. Ссылка одноразовая, действует до {fmtDateTime(info.expires_at)}</p>
        </>}
        {info && !pending && <div className="tg-login-err">{GONE[info.status]} Попросите новую ссылку.</div>}
        {error && <div className="tg-login-err">{error}</div>}
      </div>
      <div className="tg-login-copy">© {new Date().getFullYear()} Homes in Georgia</div>
    </div>
  );
}
