// /sign-docs (staff: operator and up; links from «Договоры» and Управление next to the NDA): «Документ на подпись»
// (backend services/sign_docs.py). One fixed template (the NDA's layout): staff paste the title and the text, pick the
// other party (a team member or an outside person) and optionally our second signer; «Отправить» puts David's company
// signature in and gives each signer a link /doc/<token> (the NDA's signing page). черновик → отправлен → подписан.
// Or «Готовый PDF»: staff upload a ready file instead of the text; signers read the file, the signed PDF is that file
// unchanged + the signing record page. Or «Договор между сторонами» (Irakli 11-18; from «Договоры», agents too): a ready
// PDF, no company signature, outside signers only (owner, client…), a link each that staff send by WhatsApp / Telegram.
import { useConfirm } from '../components/ActionDialog';
import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Copy, Download, Eye, FilePen, MessageCircle, Plus, Search, Send, Share2, Trash2, XCircle } from 'lucide-react';
import { api } from '../api';
import { fetchBlob, http, itemsOf } from '../api/client';
import { atLeast, STAFF_ROLES } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { errText, useToast } from '../components/toast';
import { Badge, Button, Card, Empty, Field, Input, Loading, Modal, PageHead, Seg, Select, Tabs, Textarea } from '../components/ui';
import { ContractFrame } from '../components/ContractFrame';
import { fmtDate, fmtDateTime, phoneInput } from '../lib/format';
import { t } from '../lib/i18n';

type Lang = 'KA-RU' | 'KA' | 'RU';
type Ln = 'ka' | 'ru';
interface Signer {
  id: number; side: 'party' | 'company'; role: string | null; user_id: number | null; full_name: string; personal_id: string | null; phone: string | null;
  telegram: string | null; need_tg: boolean; tg_username: string | null; signed_at: string | null; path: string | null;
}
interface Doc {
  id: number; number: string; status: 'draft' | 'sent' | 'signed' | 'cancelled'; status_label: string; lang: Lang;
  title: Partial<Record<Ln, string>>; body?: Partial<Record<Ln, string>>; html?: string; created_at: string;
  created_by: { id: number; name: string } | null; sent_at: string | null; signed_at: string | null; has_pdf: boolean;
  signers: Signer[]; editable: boolean; links?: { id: number; sent: boolean }[]; pdf: { name: string; pages: number; size: number } | null;
  parties: boolean;
}
interface Person { user_id: number | null; full_name: string; personal_id: string; phone: string; telegram: string }
interface Row { id?: number; role: string; full_name: string; personal_id: string; phone: string; telegram: string }
interface Form { lang: Lang; title: Record<Ln, string>; body: Record<Ln, string>; who: 'team' | 'outside'; party: Person; cosigner: number | null;
  mode: 'text' | 'pdf' | 'parties'; file: { name: string; data: string } | null; rows: Row[] }

const LANGS = [{ value: 'KA-RU', label: 'ქართული + Русский' }, { value: 'KA', label: 'ქართული' }, { value: 'RU', label: 'Русский' }];
const LNS: Record<Lang, Ln[]> = { 'KA-RU': ['ka', 'ru'], KA: ['ka'], RU: ['ru'] };
const TABS = [{ value: 'all', label: 'Все' }, { value: 'draft', label: 'Черновики' }, { value: 'sent', label: 'Отправлены' },
  { value: 'signed', label: 'Подписаны' }, { value: 'cancelled', label: 'Отменены' }] as const;
const TONE = { draft: 'warn', sent: 'blue', signed: 'ok', cancelled: 'grey' } as const;
const blankPerson: Person = { user_id: null, full_name: '', personal_id: '', phone: '', telegram: '' };
const row = (role: string): Row => ({ role, full_name: '', personal_id: '', phone: '', telegram: '' });
const blank: Form = { lang: 'KA-RU', title: { ka: '', ru: '' }, body: { ka: '', ru: '' }, who: 'team', party: blankPerson, cosigner: null, mode: 'text', file: null,
  rows: [row('Собственник'), row('Клиент')] };
const MAX_PDF = 15 * 1024 * 1024;  // backend sign_docs.MAX_PDF
const absolute = (p: string) => `${window.location.origin}${p}`;
const titleOf = (d: Doc) => d.title.ru || d.title.ka || d.number;

function fromDoc(d: Doc): Form {
  const p = d.signers.find((s) => s.side === 'party');
  const c = d.signers.find((s) => s.side === 'company');
  return { lang: d.lang, title: { ka: d.title.ka || '', ru: d.title.ru || '' }, body: { ka: d.body?.ka || '', ru: d.body?.ru || '' },
    who: p?.user_id ? 'team' : 'outside', cosigner: c?.user_id ?? null, mode: d.parties ? 'parties' : d.pdf ? 'pdf' : 'text', file: null,
    rows: d.parties ? d.signers.map((s) => ({ id: s.id, role: s.role || '', full_name: s.full_name, personal_id: s.personal_id || '', phone: s.phone || '', telegram: s.telegram ? `@${s.telegram}` : '' })) : blank.rows,
    party: { user_id: p?.user_id ?? null, full_name: p?.full_name || '', personal_id: p?.personal_id || '', phone: p?.phone || '', telegram: p?.telegram ? `@${p.telegram}` : '' } };
}

function DocForm({ doc, onDone, onClose, start }: { doc: Doc | null; onDone: (d: Doc) => void; onClose: () => void; start?: Form['mode'] }) {
  const toast = useToast();
  const { user } = useAuth();
  const staffUser = atLeast(user, 'operator');  // an agent: only «Договор между сторонами»
  const [f, setF] = useState<Form>(doc ? fromDoc(doc) : { ...blank, mode: staffUser ? start || 'text' : 'parties' });
  const users = useQuery({ queryKey: ['users', 'active'], queryFn: () => api.users.list({ active: true }), enabled: staffUser });
  const all = itemsOf(users.data);
  const staff = all.filter((u) => STAFF_ROLES.includes(u.role));
  const save = useMutation({
    mutationFn: () => {
      const party = f.who === 'team' ? { user_id: f.party.user_id, personal_id: f.party.personal_id, full_name: f.party.full_name, phone: f.party.phone }
        : { ...f.party, user_id: null };
      const body = f.mode === 'parties'
        ? { lang: f.lang, title: f.title, mode: 'parties', signers: f.rows, ...(f.file ? { pdf: f.file.data, pdf_name: f.file.name } : {}) }
        : { lang: f.lang, title: f.title, body: f.body, party, cosigner: f.cosigner ? { user_id: f.cosigner } : null,
          ...(f.mode === 'pdf' && f.file ? { pdf: f.file.data, pdf_name: f.file.name } : {}) };
      return doc ? http.put<Doc>(`/sign-docs/${doc.id}`, body) : http.post<Doc>('/sign-docs', body);
    },
    onSuccess: (d) => { toast(doc ? t('Документ сохранён') : t('Черновик создан — проверьте и отправьте')); onDone(d); },
    onError: (e) => toast(errText(e), true),
  });
  const setP = (k: keyof Person) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, party: { ...f.party, [k]: k === 'phone' ? phoneInput(e) : e.target.value } });
  const pick = (id: string) => {
    const u = all.find((x) => String(x.id) === id);
    setF({ ...f, party: u ? { ...blankPerson, user_id: u.id, full_name: [u.first_name, u.last_name].filter(Boolean).join(' ') || u.name, phone: u.phone || '' } : blankPerson });
  };
  const pickPdf = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    if (file.type !== 'application/pdf' && !/\.pdf$/i.test(file.name)) { toast(t('Нужен файл PDF'), true); return; }
    if (file.size > MAX_PDF) { toast(t('PDF больше 15 МБ'), true); return; }
    const r = new FileReader();
    r.onload = () => setF((x) => ({ ...x, file: { name: file.name, data: String(r.result) } }));
    r.readAsDataURL(file);
  };
  const lns = LNS[f.lang];
  const hasPdf = !!f.file || !!doc?.pdf;
  const setRow = (i: number, k: keyof Row) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setF({ ...f, rows: f.rows.map((r, j) => (j === i ? { ...r, [k]: k === 'phone' ? phoneInput(e) : e.target.value } : r)) });
  const ok = f.mode === 'parties'
    ? lns.every((l) => f.title[l].trim()) && hasPdf && f.rows.length > 0 && f.rows.every((r) => r.full_name.trim() && (r.phone.trim() || r.telegram.trim()))
    : lns.every((l) => f.title[l].trim() && (f.mode === 'pdf' ? hasPdf : f.body[l].trim())) && f.party.full_name.trim()
      && (f.who === 'team' ? !!f.party.user_id : !!f.party.phone.trim() && !!f.party.personal_id.trim());
  return (
    <Card label={doc ? `№ ${doc.number} · ${t(doc.status_label)}` : t('Новый документ')} title={doc ? titleOf(doc) : t('Документ на подпись')}
      actions={<Button size="sm" variant="ghost" onClick={onClose}>{t('Закрыть')}</Button>}>
      <div className="col gap">
        {doc?.status === 'sent' && <div className="alert alert-warn">{t('Документ уже отправлен: после сохранения открытые ссылки попросят обновить страницу')}</div>}
        <div className="form-grid">
          <Field label={t('Язык документа')}><Select value={f.lang} onChange={(e) => setF({ ...f, lang: e.target.value as Lang })} options={LANGS} /></Field>
          {lns.map((l) => <Field key={l} label={l === 'ka' ? t('Название по-грузински *') : t('Название по-русски *')}><Input value={f.title[l]} onChange={(e) => setF({ ...f, title: { ...f.title, [l]: e.target.value } })} placeholder={l === 'ka' ? 'ხელშეკრულება' : 'Договор'} /></Field>)}
        </div>
        {!doc && staffUser && <Seg value={f.mode} onChange={(mode) => setF({ ...f, mode })} options={[{ value: 'text', label: t('Вставить текст') }, { value: 'pdf', label: t('Готовый PDF') }, { value: 'parties', label: t('Договор между сторонами') }]} />}
        {f.mode !== 'text' && (
          <Field label={t('Файл PDF *')} hint={t('Документ подписывают как есть: страницы не меняются, в конце добавится страница с подписями. До 15 МБ')}>
            <div className="row row-wrap gap-sm" style={{ alignItems: 'center' }}>
              <input type="file" accept="application/pdf,.pdf" onChange={pickPdf} />
              {(f.file || doc?.pdf) && <small className="muted">{f.file ? f.file.name : `${doc!.pdf!.name} · ${doc!.pdf!.pages} ${t('стр.')}`}</small>}
            </div>
          </Field>
        )}
        {f.mode === 'text' && lns.map((l) => (
          <Field key={l} label={l === 'ka' ? t('Текст по-грузински *') : t('Текст по-русски *')} hint={t('Вставьте текст как есть: пустая строка между абзацами, **жирный** — звёздочками')}>
            <Textarea rows={12} value={f.body[l]} onChange={(e) => setF({ ...f, body: { ...f.body, [l]: e.target.value } })} />
          </Field>
        ))}
        {f.mode === 'parties' && (
          <div className="col gap-sm">
            <span className="label">{t('Подписанты')}</span>
            <span className="muted" style={{ fontSize: 13 }}>{t('Без подписи компании. У каждого своя ссылка: отправьте её сами в WhatsApp или Telegram. Подписанный PDF придёт вам в Telegram')}</span>
            {f.rows.map((r, i) => (
              <div key={r.id ?? `n${i}`} className="form-grid card card-pad" style={{ padding: 10 }}>
                <Field label={t('Кто')}><Input value={r.role} onChange={setRow(i, 'role')} placeholder={t('Собственник')} /></Field>
                <Field label={t('ФИО *')}><Input value={r.full_name} onChange={setRow(i, 'full_name')} placeholder="Nino Beridze" /></Field>
                <Field label="Telegram" hint={t('@username или телефон — одно обязательно')}><Input value={r.telegram} onChange={setRow(i, 'telegram')} placeholder="@username" /></Field>
                <Field label={t('Телефон WhatsApp')}><Input type="tel" value={r.phone} onChange={setRow(i, 'phone')} /></Field>
                <Field label={t('Личный номер / паспорт')}><Input value={r.personal_id} onChange={setRow(i, 'personal_id')} /></Field>
                {f.rows.length > 1 && <div style={{ alignSelf: 'end' }}><Button size="sm" variant="ghost" onClick={() => setF({ ...f, rows: f.rows.filter((_, j) => j !== i) })}><Trash2 size={14} />{t('Убрать')}</Button></div>}
              </div>
            ))}
            {f.rows.length < 10 && <div><Button size="sm" variant="secondary" onClick={() => setF({ ...f, rows: [...f.rows, row('')] })}><Plus size={14} />{t('Добавить подписанта')}</Button></div>}
          </div>
        )}
        {f.mode !== 'parties' && <><div className="col gap-sm">
          <span className="label">{t('Сторона 1 — наша')}</span>
          <span className="muted" style={{ fontSize: 13 }}>{t('Давид Тибелашвили, ИП 326129853 — подпись компании ставится сама при отправке')} (<Link to="/ndas">{t('подпись компании')}</Link>)</span>
          <Field label={t('Второй подписант с нашей стороны')} hint={t('Сотрудник подпишет по своей ссылке с подтверждением в Telegram')}>
            <Select value={f.cosigner ?? ''} onChange={(e) => setF({ ...f, cosigner: e.target.value ? Number(e.target.value) : null })}
              placeholder={t('— не нужен —')} options={staff.map((u) => ({ value: u.id, label: u.name }))} />
          </Field>
        </div>
        <div className="col gap-sm">
          <span className="label">{t('Сторона 2')}</span>
          <Seg value={f.who} onChange={(who) => setF({ ...f, who, party: blankPerson })} options={[{ value: 'team', label: t('Участник команды') }, { value: 'outside', label: t('Внешнее лицо') }]} />
          <div className="form-grid">
            {f.who === 'team' && <Field label={t('Участник *')} hint={t('Подтвердит себя своим Telegram, ссылку пришлёт бот')}>
              <Select value={f.party.user_id ?? ''} onChange={(e) => pick(e.target.value)} placeholder={users.isLoading ? '…' : t('— выберите —')}
                options={all.filter((u) => u.id !== f.cosigner).map((u) => ({ value: u.id, label: u.name }))} />
            </Field>}
            <Field label={t('ФИО *')}><Input value={f.party.full_name} onChange={setP('full_name')} placeholder="Nino Beridze" /></Field>
            <Field label={f.who === 'outside' ? t('Личный номер / паспорт *') : t('Личный номер / паспорт')} hint={f.who === 'team' ? t('Пусто — из профиля участника') : undefined}><Input value={f.party.personal_id} onChange={setP('personal_id')} /></Field>
            <Field label={f.who === 'outside' ? t('Телефон *') : t('Телефон')}><Input type="tel" value={f.party.phone} onChange={setP('phone')} /></Field>
            {f.who === 'outside' && <Field label="Telegram" hint={t('Необязательно: @username — тогда подписать сможет только этот аккаунт')}><Input value={f.party.telegram} onChange={setP('telegram')} placeholder="@username" /></Field>}
          </div>
        </div></>}
        <div><Button disabled={!ok} loading={save.isPending} onClick={() => save.mutate()}><FilePen size={16} />{doc ? t('Сохранить') : t('Создать черновик')}</Button></div>
      </div>
    </Card>
  );
}

function SignerLink({ d, s }: { d: Doc; s: Signer }) {
  const toast = useToast();
  if (!s.path) return null;
  const url = absolute(s.path);
  const text = `Homes in Georgia: ${titleOf(d)} № ${d.number} — ${s.full_name}`;
  const wa = (s.phone || '').replace(/\D/g, '');
  const copy = () => navigator.clipboard?.writeText(url).then(() => toast(t('Ссылка скопирована')), () => undefined);
  const share = async () => { if (navigator.share) { try { await navigator.share({ url, title: text }); } catch { /* closed */ } return; } await copy(); };
  return (
    <div className="row row-wrap gap-sm" style={{ alignItems: 'center' }}>
      <small className="muted">{s.role ? `${s.role} · ` : ''}{s.full_name}:</small>
      <code style={{ fontSize: 12, wordBreak: 'break-all' }}>{url}</code>
      <Button size="sm" variant="secondary" onClick={share}><Share2 size={14} />{t('Отправить')}</Button>
      <Button size="sm" variant="ghost" onClick={copy}><Copy size={14} />{t('Копировать')}</Button>
      <a className="btn btn-sm btn-ghost" href={`https://t.me/share/url?url=${encodeURIComponent(url)}&text=${encodeURIComponent(text)}`} target="_blank" rel="noreferrer"><Send size={14} />Telegram</a>
      {wa && <a className="btn btn-sm btn-ghost" href={`https://wa.me/${wa}?text=${encodeURIComponent(`${text}\n${url}`)}`} target="_blank" rel="noreferrer"><MessageCircle size={14} />WhatsApp</a>}
    </div>
  );
}

export function SignDocsPage() {
  const confirm = useConfirm();
  const toast = useToast();
  const { user } = useAuth();
  const qc = useQueryClient();
  const [tab, setTab] = useState<(typeof TABS)[number]['value']>('all');
  const [find, setFind] = useState('');
  const [params, setParams] = useSearchParams();  // ?new=parties: «Договоры» → «Загрузить готовый PDF на подпись»
  const [form, setForm] = useState<{ doc: Doc | null; start?: Form['mode'] } | null>(params.get('new') ? { doc: null, start: params.get('new') === 'parties' ? 'parties' : undefined } : null);
  const closeForm = () => { setForm(null); if (params.has('new')) setParams({}, { replace: true }); };
  const [view, setView] = useState<Doc | null>(null);
  const q = useQuery({ queryKey: ['sign-docs', tab, find], queryFn: () => http.get<{ items: Doc[]; total: number }>('/sign-docs', { status: tab, q: find || undefined, limit: 100 }) });
  const refresh = () => qc.invalidateQueries({ queryKey: ['sign-docs'] });
  const full = (id: number) => http.get<Doc>(`/sign-docs/${id}`);
  const send = useMutation({
    mutationFn: (d: Doc) => http.post<Doc>(`/sign-docs/${d.id}/send`),
    onSuccess: (d) => {
      refresh();
      if (d.parties) { toast(t('Ссылки готовы — отправьте их подписантам ниже')); return; }
      const miss = (d.links || []).filter((x) => !x.sent).length;
      toast(miss ? t('Отправлено. Ссылок без Telegram: {n} — скопируйте их ниже', { n: miss }) : t('Отправлено: ссылки ушли в Telegram'), miss > 0);
    },
    onError: (e) => toast(errText(e), true),
  });
  const cancel = useMutation({
    mutationFn: (id: number) => http.post<Doc>(`/sign-docs/${id}/cancel`),
    onSuccess: () => { refresh(); toast(t('Документ отменён, ссылки больше не работают')); },
    onError: (e) => toast(errText(e), true),
  });
  const pdf = async (d: Doc) => {
    const url = URL.createObjectURL(await fetchBlob(`/sign-docs/${d.id}/pdf`));
    window.open(url, '_blank');
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  };
  const open = (d: Doc, then: (x: Doc) => void) => full(d.id).then(then, (e) => toast(errText(e), true));
  const rows = itemsOf(q.data);
  return (
    <div className="col gap-lg">
      <div className="row row-wrap gap-sm" style={{ fontSize: 13 }}><Link to="/contracts" className="muted">← {t('Договоры')}</Link>{atLeast(user, 'operator') && <Link to="/ndas" className="muted">{t('Соглашения (NDA)')}</Link>}</div>
      <PageHead label={t('Электронная подпись')} title={t('Документы на подпись')}
        actions={!form && <Button onClick={() => setForm({ doc: null })}><Plus size={16} />{t('Новый документ')}</Button>} />
      {form && <DocForm key={form.doc?.id ?? 'new'} doc={form.doc} start={form.start} onClose={closeForm} onDone={(d) => { closeForm(); refresh(); if (!d.pdf) setView(d); }} />}
      <Card label={t('История')} title={t('Все документы')}>
        <div className="row row-wrap gap-sm" style={{ alignItems: 'center' }}>
          <Tabs value={tab} onChange={setTab} tabs={TABS.map((x) => ({ ...x, label: t(x.label) }))} />
          <div style={{ position: 'relative', minWidth: 220 }}><Search size={14} style={{ position: 'absolute', left: 10, top: 11, opacity: 0.5 }} />
            <Input style={{ paddingLeft: 30 }} value={find} onChange={(e) => setFind(e.target.value)} placeholder={t('Название или подписант')} /></div>
        </div>
        {q.isLoading ? <Loading /> : rows.length === 0 ? <Empty text={t('Пока пусто')} icon={<FilePen size={22} />} /> : (
          <div className="col gap-sm mt">
            {rows.map((d) => (
              <div key={d.id} className="card card-pad col gap-sm" style={{ padding: 12 }}>
                <div className="row row-between row-wrap gap-sm">
                  <span className="col" style={{ gap: 2 }}>
                    <b>{titleOf(d)}</b>
                    <small className="muted">№ {d.number} · {d.lang}{d.pdf ? ` · PDF ${d.pdf.pages} ${t('стр.')}` : ''} · {d.created_by?.name || '—'}, {fmtDate(d.created_at)}{d.signed_at ? ` · ${t('подписан')} ${fmtDateTime(d.signed_at)}` : ''}</small>
                    {d.signers.map((s) => (
                      <small key={s.id} className="muted">{s.signed_at ? '✅' : d.status === 'sent' ? '⏳' : '·'} {s.full_name}
                        {s.role ? ` (${s.role})` : s.side === 'company' ? ` (${t('наш подписант')})` : ''}{s.signed_at ? ` — ${t('подписал')} ${fmtDateTime(s.signed_at)}` : d.status === 'sent' ? ` — ${t('ждём подписи')}` : ''}{s.tg_username ? ` · @${s.tg_username}` : ''}</small>
                    ))}
                  </span>
                  <Badge tone={TONE[d.status]}>{t(d.status_label)}</Badge>
                </div>
                <div className="row row-wrap gap-sm">
                  <Button size="sm" variant="ghost" onClick={() => (d.pdf ? pdf(d).catch((e) => toast(errText(e), true)) : open(d, setView))}><Eye size={14} />{t('Просмотр')}</Button>
                  {d.editable && <Button size="sm" variant="ghost" onClick={() => open(d, (x) => setForm({ doc: x }))}><FilePen size={14} />{t('Изменить')}</Button>}
                  {d.status === 'draft' && <Button size="sm" loading={send.isPending} onClick={async () => { if (await confirm(d.parties ? t('Отправить «{title}» на подпись? Для каждого подписанта появится своя ссылка.', { title: titleOf(d) }) : t('Отправить «{title}» на подпись? Подпись компании поставится сама.', { title: titleOf(d) }))) send.mutate(d); }}><Send size={14} />{t('Отправить на подпись')}</Button>}
                  {d.status === 'sent' && d.signers.some((s) => s.user_id && !s.signed_at) && <Button size="sm" variant="secondary" loading={send.isPending} onClick={() => send.mutate(d)}><Send size={14} />{t('Отправить снова в Telegram')}</Button>}
                  {(d.status === 'draft' || d.status === 'sent') && <Button size="sm" variant="ghost" loading={cancel.isPending} onClick={async () => { if (await confirm(t('Отменить документ? Ссылки перестанут работать.'))) cancel.mutate(d.id); }}><XCircle size={14} />{t('Отменить')}</Button>}
                  {d.status !== 'cancelled' && <Button size="sm" variant={d.status === 'signed' ? 'secondary' : 'ghost'} onClick={() => pdf(d).catch((e) => toast(errText(e), true))}><Download size={14} />{d.status === 'signed' ? 'PDF' : d.pdf ? 'PDF' : t('PDF (черновик)')}</Button>}
                </div>
                {d.status === 'sent' && d.signers.map((s) => <SignerLink key={s.id} d={d} s={s} />)}
              </div>
            ))}
          </div>
        )}
      </Card>
      <Modal open={!!view} onClose={() => setView(null)} title={view ? `${titleOf(view)} · № ${view.number}` : ''} wide>
        {view?.html && <ContractFrame html={view.html} />}
      </Modal>
    </div>
  );
}
