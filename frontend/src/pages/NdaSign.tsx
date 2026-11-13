// /nda/<token>: a new agent signs his NDA (backend services/nda.py, docs/NDA.md) — no CRM login, the link is the access.
// 1) read the agreement; 2) confirm yourself with Telegram (Telegram Login: the app asks to confirm; the server checks
// it, and only the NDA's @username passes); 3) sign with a finger + consent. The signed PDF comes by Telegram and here.
import { useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { CheckCircle2, Download, ShieldCheck } from 'lucide-react';
import { http } from '../api/client';
import { api } from '../api';
import { errText } from '../components/toast';
import { Button, ErrorBox, Loading, Seg } from '../components/ui';
import { SignaturePad, signaturePng } from '../components/SignaturePad';
import { ContractFrame } from './ESign';
import { fmtDateTime } from '../lib/format';

interface NdaView {
  lang: 'KA' | 'KA-RU' | 'KA-EN'; status: 'sent' | 'signed'; full_name: string; personal_id: string; phone: string | null;
  telegram: string | null; role: string | null; doc_sha256: string; confirmed: boolean; tg_username: string | null;
  tg_name: string | null; signed_at: string | null; has_pdf: boolean; test_mode: boolean; html: string | null;
}
type L = 'ka' | 'ru' | 'en';
const LABEL: Record<L, string> = { ka: 'ქართული', ru: 'Русский', en: 'English' };
const UI: Record<L, Record<string, string>> = {
  ka: { title: 'კონფიდენციალურობის შეთანხმება (NDA)', read: 'გთხოვთ, ყურადღებით წაიკითხოთ შეთანხმება', toSign: 'ხელმოწერაზე გადასვლა ↓',
    step1: '1. დაადასტურეთ თქვენი ვინაობა Telegram-ით', tgBtn: 'Telegram-ით დადასტურება', tgHint: 'Telegram გთხოვთ დადასტურებას აპლიკაციაში.',
    tgOnly: 'მხოლოდ ანგარიში', tgOk: 'დადასტურებულია', step2: '2. მოაწერეთ ხელი თითით', clear: 'წაშლა',
    agree: 'წავიკითხე შეთანხმება და ვეთანხმები მის პირობებს', send: 'ხელმოწერა', done: 'NDA ხელმოწერილია. გმადლობთ!',
    pdf: 'PDF-ის ჩამოტვირთვა', pdfWait: 'PDF მზადდება, განაახლეთ გვერდი ერთ წუთში.', sent: 'ხელმოწერილი PDF გამოგეგზავნათ Telegram-ში.',
    you: 'მიმღები', test: 'დადასტურება (სატესტო რეჟიმი)', noTg: 'Telegram-ის ჩატვირთვა ვერ მოხერხდა. განაახლეთ გვერდი.' },
  ru: { title: 'Соглашение о конфиденциальности (NDA)', read: 'Пожалуйста, внимательно прочитайте соглашение', toSign: 'К подписи ↓',
    step1: '1. Подтвердите себя через Telegram', tgBtn: 'Подтвердить через Telegram', tgHint: 'Telegram попросит подтвердить вход в приложении.',
    tgOnly: 'Только аккаунт', tgOk: 'Подтверждено', step2: '2. Распишитесь пальцем', clear: 'Стереть',
    agree: 'Я прочитал(а) соглашение и согласен(на) с его условиями', send: 'Подписать', done: 'NDA подписано. Спасибо!',
    pdf: 'Скачать PDF', pdfWait: 'PDF готовится, обновите страницу через минуту.', sent: 'Подписанный PDF отправлен вам в Telegram.',
    you: 'Получатель', test: 'Подтвердить (тестовый режим)', noTg: 'Не удалось загрузить Telegram. Обновите страницу.' },
  en: { title: 'Non-Disclosure Agreement (NDA)', read: 'Please read the agreement carefully', toSign: 'Go to signing ↓',
    step1: '1. Confirm yourself with Telegram', tgBtn: 'Confirm with Telegram', tgHint: 'Telegram will ask you to confirm in the app.',
    tgOnly: 'Only the account', tgOk: 'Confirmed', step2: '2. Sign with your finger', clear: 'Clear',
    agree: 'I have read the agreement and agree to its terms', send: 'Sign', done: 'NDA signed. Thank you!',
    pdf: 'Download PDF', pdfWait: 'The PDF is being prepared, refresh in a minute.', sent: 'The signed PDF was sent to you in Telegram.',
    you: 'Recipient', test: 'Confirm (test mode)', noTg: 'Could not load Telegram. Refresh the page.' },
};

type TgUser = Record<string, string | number>;
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

export function NdaSignPage() {
  const { token = '' } = useParams();
  const q = useQuery({ queryKey: ['nda', token], queryFn: () => http.get<NdaView>(`/public/nda/${token}`), retry: false, refetchOnWindowFocus: false });
  const cfg = useQuery({ queryKey: ['app-config'], queryFn: api.config, staleTime: Infinity, retry: false });
  const [lang, setLang] = useState<L | null>(null);
  const [ink, setInk] = useState(false);
  const [agree, setAgree] = useState(false);
  const [err, setErr] = useState('');
  const pad = useRef<HTMLCanvasElement>(null);
  const v = q.data;
  const l: L = lang || (v?.lang === 'KA-EN' ? 'en' : v?.lang === 'KA-RU' ? 'ru' : 'ka');
  const t = UI[l];

  const confirm = useMutation({
    mutationFn: (body: object) => http.post<NdaView>(`/public/nda/${token}/telegram`, body),
    onSuccess: () => { setErr(''); q.refetch(); },
    onError: (e) => setErr(errText(e)),
  });
  const telegram = () => {
    const botId = String(cfg.data?.bot_id || '');
    if (!botId) { setErr(t.noTg); return; }
    loadTelegram().then((tg) => tg.auth({ bot_id: botId, request_access: 'write' }, (u) => { if (u) confirm.mutate(u); }),
      () => setErr(t.noTg));
  };
  const sign = useMutation({
    mutationFn: () => http.post<NdaView>(`/public/nda/${token}`, { signature: signaturePng(pad.current!), consent: true, doc_sha256: v!.doc_sha256 }),
    onSuccess: () => { setErr(''); q.refetch(); },
    onError: (e) => setErr(errText(e)),
  });

  const wrap = (body: React.ReactNode) => <div style={{ maxWidth: 860, margin: '0 auto', padding: '24px 16px' }}><div className="card card-pad col gap-lg">{body}</div></div>;
  if (q.isLoading) return wrap(<Loading />);
  if (q.error || !v) return wrap(<ErrorBox error={q.error} />);
  const langs: L[] = v.lang === 'KA' ? ['ka', 'ru', 'en'] : v.lang === 'KA-RU' ? ['ka', 'ru'] : ['ka', 'en'];
  const head = (
    <>
      <div className="row row-between row-wrap gap-sm">
        <b style={{ color: '#1D3560' }}>Homes in Georgia · Rent in Tbilisi</b>
        <Seg value={l} onChange={setLang} options={langs.map((x) => ({ value: x, label: LABEL[x] }))} />
      </div>
      <h2 style={{ fontSize: 20 }}><ShieldCheck size={20} style={{ verticalAlign: -3, marginRight: 6 }} />{t.title}</h2>
      <div className="muted">{t.you}: <b>{v.full_name}</b>{v.role ? ` · ${v.role}` : ''}{v.telegram ? ` · @${v.telegram}` : ''}</div>
    </>
  );
  if (v.status === 'signed') {
    return wrap(<>
      {head}
      <div className="alert alert-ok"><CheckCircle2 size={16} style={{ verticalAlign: -3 }} /> {t.done} · {fmtDateTime(v.signed_at)}</div>
      {v.has_pdf
        ? <><a className="btn btn-ok btn-lg" href={`/api/public/nda/${token}/pdf?download=1`}><Download size={18} />{t.pdf}</a><span className="muted" style={{ fontSize: 13 }}>{t.sent}</span></>
        : <span className="muted">{t.pdfWait}</span>}
    </>);
  }
  return wrap(<>
    {head}
    <div className="col gap-sm">
      <div className="row row-between row-wrap gap-sm">
        <span className="label">{t.read}</span>
        <Button size="sm" variant="secondary" onClick={() => document.getElementById('nda-form')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>{t.toSign}</Button>
      </div>
      {v.html && <ContractFrame html={v.html} />}
    </div>
    <div className="col gap-sm" id="nda-form">
      <span className="label">{t.step1}</span>
      {v.confirmed
        ? <div className="alert alert-ok"><CheckCircle2 size={16} style={{ verticalAlign: -3 }} /> {t.tgOk}: {v.tg_username ? `@${v.tg_username}` : v.tg_name}</div>
        : <>
            <span className="muted" style={{ fontSize: 13 }}>{t.tgHint}{v.telegram ? ` ${t.tgOnly} @${v.telegram}.` : ''}</span>
            <div className="row row-wrap gap-sm">
              <Button loading={confirm.isPending} onClick={telegram}>{t.tgBtn}</Button>
              {v.test_mode && <Button variant="ghost" loading={confirm.isPending} onClick={() => confirm.mutate({ test: true })}>{t.test}</Button>}
            </div>
          </>}
    </div>
    <div className="col gap-sm" style={v.confirmed ? undefined : { opacity: 0.45, pointerEvents: 'none' }}>
      <span className="label">{t.step2}</span>
      <SignaturePad padRef={pad} onInk={setInk} clearLabel={t.clear} />
      <label className="row gap-sm" style={{ cursor: 'pointer' }}><input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} />{t.agree}</label>
    </div>
    {err && <div className="alert alert-warn">{err}</div>}
    <Button size="lg" variant="ok" disabled={!(v.confirmed && ink && agree)} loading={sign.isPending} onClick={() => sign.mutate()}>{t.send}</Button>
  </>);
}
