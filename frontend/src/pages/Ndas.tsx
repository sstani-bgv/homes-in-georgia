// /ndas (staff): NDA for new agents (backend services/nda.py, docs/NDA.md). David draws the company's signature once;
// staff fill in the agent and send him the personal link /nda/<token>; he confirms himself with Telegram and signs.
import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Copy, Download, Send, Share2, ShieldCheck, XCircle } from 'lucide-react';
import { fetchBlob, http, itemsOf } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { errText, useToast } from '../components/toast';
import { Badge, Button, Card, Empty, Field, Input, Loading, PageHead, Select, Tabs } from '../components/ui';
import { SignaturePad, signaturePng } from '../components/SignaturePad';
import { fmtDate, fmtDateTime, phoneInput } from '../lib/format';

interface Nda {
  id: number; status: 'sent' | 'signed' | 'cancelled'; status_label: string; lang: 'KA' | 'KA-RU' | 'KA-EN';
  full_name: string; personal_id: string; phone: string | null; telegram: string | null; role: string | null;
  path: string | null; created_at: string; created_by: { id: number; name: string } | null; signed_at: string | null;
  tg_username: string | null; has_pdf: boolean;
}
interface CompanySig { signature: string | null; at: string | null; by_name: string | null }
const LANGS = [{ value: 'KA-RU', label: 'ქართული + Русский' }, { value: 'KA-EN', label: 'ქართული + English' }, { value: 'KA', label: 'ქართული' }];
const TONE = { sent: 'blue', signed: 'ok', cancelled: 'grey' } as const;
const absolute = (p: string) => `${window.location.origin}${p}`;

function CompanySignature() {
  const { user } = useAuth();
  const toast = useToast();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['nda-company-sig'], queryFn: () => http.get<CompanySig>('/ndas/company-signature') });
  const [edit, setEdit] = useState(false);
  const [ink, setInk] = useState(false);
  const pad = useRef<HTMLCanvasElement>(null);
  const save = useMutation({
    mutationFn: () => http.put<CompanySig>('/ndas/company-signature', { signature: signaturePng(pad.current!) }),
    onSuccess: () => { setEdit(false); qc.invalidateQueries({ queryKey: ['nda-company-sig'] }); toast('Подпись компании сохранена'); },
    onError: (e) => toast(errText(e), true),
  });
  const boss = user && ['superadmin', 'superagent'].includes(user.role);
  const d = q.data;
  return (
    <Card label="Один раз" title="Подпись компании (Давид Тибелашвили)">
      {q.isLoading ? <Loading /> : (
        <div className="col gap">
          {d?.signature && !edit && (
            <div className="row row-wrap gap" style={{ alignItems: 'center' }}>
              <img src={d.signature} alt="" style={{ height: 56, background: '#fff', border: '1px solid var(--line, #e3e8f0)', borderRadius: 8, padding: 4 }} />
              <span className="muted" style={{ fontSize: 13 }}>Ставится в каждое новое NDA · {d.by_name || '—'}, {fmtDate(d.at)}</span>
            </div>
          )}
          {!d?.signature && !edit && <span className="muted">Подписи ещё нет — без неё NDA не отправить.</span>}
          {edit && <>
            <SignaturePad padRef={pad} onInk={setInk} clearLabel="Стереть" />
            <div className="row gap-sm">
              <Button disabled={!ink} loading={save.isPending} onClick={() => save.mutate()}>Сохранить подпись</Button>
              <Button variant="ghost" onClick={() => setEdit(false)}>Отмена</Button>
            </div>
          </>}
          {!edit && (boss
            ? <div><Button variant="secondary" onClick={() => setEdit(true)}>{d?.signature ? 'Заменить подпись' : 'Расписаться за компанию'}</Button></div>
            : !d?.signature && <span className="faint" style={{ fontSize: 12 }}>Расписаться может только суперадмин.</span>)}
        </div>
      )}
    </Card>
  );
}

function LinkActions({ n }: { n: Nda }) {
  const toast = useToast();
  if (!n.path) return null;
  const url = absolute(n.path);
  const text = `Homes in Georgia: NDA для подписи — ${n.full_name}`;
  const share = async () => {
    if (navigator.share) { try { await navigator.share({ url, title: text }); } catch { /* closed */ } return; }
    await navigator.clipboard?.writeText(url).then(() => toast('Ссылка скопирована'), () => undefined);
  };
  return (
    <div className="row row-wrap gap-sm">
      <Button size="sm" variant="secondary" onClick={share}><Share2 size={14} />Отправить</Button>
      <Button size="sm" variant="ghost" onClick={() => navigator.clipboard?.writeText(url).then(() => toast('Ссылка скопирована'), () => undefined)}><Copy size={14} />Копировать</Button>
      <a className="btn btn-sm btn-ghost" href={`https://t.me/share/url?url=${encodeURIComponent(url)}&text=${encodeURIComponent(text)}`} target="_blank" rel="noreferrer"><Send size={14} />Telegram</a>
    </div>
  );
}

function NewNda({ onMade }: { onMade: (n: Nda) => void }) {
  const toast = useToast();
  const [f, setF] = useState({ full_name: '', personal_id: '', phone: '', telegram: '', role: 'Агент', lang: 'KA-RU' });
  const m = useMutation({
    mutationFn: () => http.post<Nda>('/ndas', f),
    onSuccess: (n) => { onMade(n); setF({ ...f, full_name: '', personal_id: '', phone: '', telegram: '' }); toast('NDA создано — отправьте ссылку агенту'); },
    onError: (e) => toast(errText(e), true),
  });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  return (
    <Card label="Новому агенту" title="Новое NDA">
      <div className="form-grid">
        <Field label="ФИО агента *"><Input value={f.full_name} onChange={set('full_name')} placeholder="Как в паспорте" /></Field>
        <Field label="Личный номер / паспорт *"><Input value={f.personal_id} onChange={set('personal_id')} /></Field>
        <Field label="Телефон"><Input type="tel" value={f.phone} onChange={(e) => setF({ ...f, phone: phoneInput(e) })} /></Field>
        <Field label="Telegram" hint="@username: подписать сможет только этот аккаунт"><Input value={f.telegram} onChange={set('telegram')} placeholder="@username" /></Field>
        <Field label="Роль"><Input value={f.role} onChange={set('role')} placeholder="Агент, рекрут, фотограф…" /></Field>
        <Field label="Язык"><Select value={f.lang} onChange={set('lang')} options={LANGS} /></Field>
      </div>
      <div className="mt"><Button disabled={!f.full_name.trim() || !f.personal_id.trim()} loading={m.isPending} onClick={() => m.mutate()}><ShieldCheck size={16} />Создать ссылку</Button></div>
    </Card>
  );
}

const TABS = [{ value: 'sent', label: 'Ждут подписи' }, { value: 'signed', label: 'Подписаны' }, { value: 'all', label: 'Все' }] as const;

export function NdasPage() {
  const toast = useToast();
  const qc = useQueryClient();
  const [tab, setTab] = useState<(typeof TABS)[number]['value']>('sent');
  const [made, setMade] = useState<Nda | null>(null);
  const q = useQuery({ queryKey: ['ndas', tab], queryFn: () => http.get<{ items: Nda[]; total: number }>('/ndas', { status: tab, limit: 100 }) });
  const cancel = useMutation({
    mutationFn: (id: number) => http.post<Nda>(`/ndas/${id}/cancel`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['ndas'] }); toast('NDA отменено, ссылка больше не работает'); },
    onError: (e) => toast(errText(e), true),
  });
  const pdf = async (n: Nda) => {
    const url = URL.createObjectURL(await fetchBlob(`/ndas/${n.id}/pdf`));
    window.open(url, '_blank');
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  };
  const rows = itemsOf(q.data);
  return (
    <div className="col gap-lg">
      <PageHead label="Новые агенты" title="NDA" />
      <CompanySignature />
      <NewNda onMade={(n) => { setMade(n); setTab('sent'); qc.invalidateQueries({ queryKey: ['ndas'] }); }} />
      {made?.path && (
        <Card label="Готово" title={`Ссылка для ${made.full_name}`}>
          <div className="col gap-sm">
            <a href={absolute(made.path)} target="_blank" rel="noreferrer" style={{ wordBreak: 'break-all' }}>{absolute(made.path)}</a>
            <LinkActions n={made} />
          </div>
        </Card>
      )}
      <Card label="История" title="Все NDA">
        <Tabs value={tab} onChange={setTab} tabs={TABS.map((t) => ({ ...t }))} />
        {q.isLoading ? <Loading /> : rows.length === 0 ? <Empty text="Пока пусто" icon={<ShieldCheck size={22} />} /> : (
          <div className="col gap-sm mt">
            {rows.map((n) => (
              <div key={n.id} className="card card-pad col gap-sm" style={{ padding: 12 }}>
                <div className="row row-between row-wrap gap-sm">
                  <span className="col" style={{ gap: 2 }}>
                    <b>{n.full_name}</b>
                    <small className="muted">#{n.id} · {n.role || '—'} · {n.lang}{n.telegram ? ` · @${n.telegram}` : ''} · {n.created_by?.name || '—'}, {fmtDate(n.created_at)}</small>
                    {n.signed_at && <small className="muted">Подписал(а) {fmtDateTime(n.signed_at)}{n.tg_username ? ` · Telegram @${n.tg_username}` : ''}</small>}
                  </span>
                  <Badge tone={TONE[n.status]}>{n.status_label}</Badge>
                </div>
                <div className="row row-wrap gap-sm">
                  {n.status === 'sent' && <LinkActions n={n} />}
                  {n.status === 'sent' && <Button size="sm" variant="ghost" loading={cancel.isPending} onClick={() => { if (window.confirm(`Отменить NDA для ${n.full_name}? Ссылка перестанет работать.`)) cancel.mutate(n.id); }}><XCircle size={14} />Отменить</Button>}
                  {n.has_pdf && <Button size="sm" variant="secondary" onClick={() => pdf(n).catch((e) => toast(errText(e), true))}><Download size={14} />PDF</Button>}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
