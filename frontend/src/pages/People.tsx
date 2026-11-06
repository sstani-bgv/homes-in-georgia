// Клиенты (table + the full client page /clients/:id, stage 8) and Собственники (table + drawer).
import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Heart, Plus, Search, ThumbsDown } from 'lucide-react';
import { api } from '../api';
import { itemsOf } from '../api/client';
import type { ClientEvent, ClientEventType, ClientRefusal, Owner } from '../api/types';
import { LeadStatusBadge, PropertyStatusBadge } from '../components/domain';
import { errText, useToast } from '../components/toast';
import { Badge, Button, Card, Drawer, Empty, ErrorBox, Field, Input, Loading, PageHead, Seg, Switch, Textarea } from '../components/ui';
import { ProfileView } from '../features/leads/LeadDetail';
import { fmtDate, fmtDateTime, money } from '../lib/format';

function SearchBox({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  return (
    <div className="input-icon" style={{ maxWidth: 420, marginBottom: 14 }}>
      <Search size={16} />
      <input className="input" placeholder={placeholder} value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

const BY: Record<string, string> = { client: 'Клиент', agent: 'Агент', operator: 'Оператор' };
const CHANNEL: Record<string, string> = { tg: 'Telegram' };
const LANG: Record<string, string> = { ru: 'Русский', en: 'English', ka: 'ქართული' };
const FILTERS = [
  { value: 'all', label: 'Всё' }, { value: 'pick', label: 'Подбор' }, { value: 'lead', label: 'Заявки' }, { value: 'message', label: 'Переписка' },
  { value: 'viewing', label: 'Просмотры' }, { value: 'refusal', label: 'Отказы' }, { value: 'profile', label: 'Анкета' }, { value: 'deal', label: 'Сделки' },
];
const GROUP: Record<ClientEventType, string> = { profile: 'profile', search: 'pick', shown: 'pick', reaction: 'pick', lead: 'lead', message: 'message', viewing: 'viewing', refusal: 'refusal', deal: 'deal' };
const ICON: Record<string, typeof Heart> = { like: Heart, dislike: ThumbsDown };

function EventLinks({ links }: { links: ClientEvent['links'] }) {
  const out = [
    links.lead_id && <Link key="l" to={`/leads/${links.lead_id}`}>заявка #{links.lead_id}</Link>,
    links.property_id && <Link key="p" to={`/objects/${links.property_id}`}>объект #{links.property_id}</Link>,
    links.deal_id && <Link key="d" to={`/deals/${links.deal_id}`}>сделка #{links.deal_id}</Link>,
  ].filter(Boolean);
  return out.length ? <>{out.map((x, i) => <span key={i}>{x}</span>)}</> : null;
}

function ClientTimeline({ items }: { items: ClientEvent[] }) {
  const [f, setF] = useState('all');
  const shown = items.filter((e) => f === 'all' || GROUP[e.type] === f);
  const count = (v: string) => (v === 'all' ? items.length : items.filter((e) => GROUP[e.type] === v).length);
  return (
    <Card label="История" title="Лента событий">
      <div style={{ marginBottom: 14, overflowX: 'auto' }}>
        <Seg value={f} onChange={setF} options={FILTERS.filter((o) => o.value === 'all' || count(o.value)).map((o) => ({ value: o.value, label: `${o.label} ${count(o.value)}` }))} />
      </div>
      {shown.length === 0 ? <div className="muted">Событий пока нет</div> : (
        <div className="timeline">
          {shown.map((e, i) => {
            const Icon = e.action ? ICON[e.action] : undefined;
            return (
              <div className="tl-item" key={i}>
                <span className={`tl-dot ${e.actor === 'Бот' ? 'bot' : ''}`} />
                <div>
                  <div className="tl-text row gap-sm">{Icon && <Icon size={14} />}<span>{e.text}</span></div>
                  <div className="tl-meta row row-wrap gap-sm">
                    {e.actor && <span>{e.actor}</span>}
                    <span>{fmtDateTime(e.at)}</span>
                    <EventLinks links={e.links} />
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}

function Refusals({ items }: { items: ClientRefusal[] }) {
  return (
    <Card label="Отказы" title={items.length ? `Отказов: ${items.length}` : 'Отказов нет'}>
      {items.length === 0 ? <div className="muted">Ни клиент, ни агент не отказывались</div> : items.map((r, i) => (
        <div key={i} className="list-row" style={{ alignItems: 'flex-start' }}>
          <div className="grow col" style={{ gap: 4 }}>
            <div style={{ fontWeight: 600 }}><Link to={`/leads/${r.lead_id}`}>Заявка #{r.lead_id}</Link> · {r.object}</div>
            <div style={{ fontSize: 13 }}>Отказал: <b>{BY[r.by] || r.by}{r.by_name && r.by_name !== BY[r.by] ? ` · ${r.by_name}` : ''}</b></div>
            <div style={{ fontSize: 13 }}>Почему: <b>{r.reason || 'не указано'}</b>{r.note ? <span className="muted"> · {r.note}</span> : null}</div>
            <div className="row gap-sm" style={{ fontSize: 13 }}>На этапе: {r.stage ? <LeadStatusBadge status={r.stage} /> : <span className="muted">неизвестно</span>}</div>
          </div>
          <span className="faint" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>{fmtDate(r.at)}</span>
        </div>
      ))}
    </Card>
  );
}

export function ClientPage() {
  const { id } = useParams();
  const cid = Number(id);
  const { data: c, isLoading, error } = useQuery({ queryKey: ['client', cid], queryFn: () => api.clients.get(cid) });
  const back = <Link to="/clients" className="row gap-sm label" style={{ marginBottom: 12, display: 'inline-flex' }}><ArrowLeft size={14} />Клиенты</Link>;
  if (error) return <>{back}<ErrorBox error={error} /></>;
  if (isLoading || !c) return <>{back}<Loading /></>;
  const leads = Array.isArray(c.leads) ? c.leads : [];
  return (
    <>
      {back}
      <div className="col gap-lg">
        <div className="card card-pad">
          <div className="label">Клиент #{c.id} · {CHANNEL[c.channel || ''] || c.channel || '—'}</div>
          <h1 style={{ fontSize: 24 }}>{c.name || (c.username ? `@${c.username}` : `Клиент #${c.id}`)}</h1>
          <div className="row row-wrap gap-sm mt" style={{ fontSize: 13 }}>
            {c.username && <a href={`https://t.me/${c.username}`} target="_blank" rel="noreferrer">@{c.username}</a>}
            {c.phone && <a href={`tel:${c.phone.replace(/[^+\d]/g, '')}`}>{c.phone}</a>}
            <Badge tone="grey">{LANG[c.lang || ''] || (c.lang || '—').toUpperCase()}</Badge>
            {c.profile_done ? <Badge tone="ok">анкета заполнена</Badge> : <Badge tone="grey">анкета не заполнена</Badge>}
            <span className="muted">с нами с {fmtDate(c.created_at)}</span>
          </div>
          {c.status && <div className="alert alert-info mt" style={{ fontWeight: 700 }}>{c.status}</div>}
        </div>
        <div className="split">
          <div className="col gap-lg"><ClientTimeline items={c.timeline || []} /></div>
          <div className="col gap-lg">
            <Card label="Анкета" title={c.profile_done ? 'Заполнена' : 'Не заполнена'}><ProfileView profile={c.profile || {}} /></Card>
            <Refusals items={c.refusals || []} />
            <Card label="Заявки" title={`${leads.length}`}>
              {leads.length === 0 ? <div className="muted">Заявок нет</div> : leads.map((l) => (
                <Link key={l.id} to={`/leads/${l.id}`} className="list-row" style={{ color: 'inherit', textDecoration: 'none' }}>
                  <span className="label">#{l.id}</span><span className="grow ellipsis" style={{ fontWeight: 600 }}>{l.listing?.title}</span><LeadStatusBadge status={l.status} />
                </Link>
              ))}
            </Card>
          </div>
        </div>
      </div>
    </>
  );
}

export function ClientsPage() {
  const [q, setQ] = useState('');
  const navigate = useNavigate();
  const { data, isLoading, error } = useQuery({ queryKey: ['clients', q], queryFn: () => api.clients.list(q) });
  const items = itemsOf(data);
  return (
    <>
      <PageHead title="Клиенты" />
      <SearchBox value={q} onChange={setQ} placeholder="Имя, @username, телефон" />
      {isLoading ? <Loading /> : error ? <ErrorBox error={error} /> : items.length === 0 ? <Empty text="Клиентов не найдено" /> : (
        <div className="card table-wrap">
          <table className="table">
            <thead><tr><th>Клиент</th><th>Бюджет</th><th className="hide-mobile">Районы</th><th className="hide-mobile">Анкета</th><th>Заявки</th><th className="hide-mobile">Создан</th></tr></thead>
            <tbody>
              {items.map((c) => (
                <tr key={c.id} className="clickable" onClick={() => navigate(`/clients/${c.id}`)}>
                  <td><b>{c.name || '—'}</b><div className="faint" style={{ fontSize: 12 }}>{c.username ? `@${c.username}` : ''}</div></td>
                  <td className="num">{c.profile?.budget_max ? `до ${money(c.profile.budget_max)}` : '—'}</td>
                  <td className="hide-mobile">{(c.profile?.districts || []).map((d) => (d === 'any' ? 'любой' : d)).join(', ') || '—'}</td>
                  <td className="hide-mobile">{c.profile_done ? <Badge tone="ok">готова</Badge> : <Badge tone="grey">нет</Badge>}</td>
                  <td className="num">{typeof c.leads === 'number' ? c.leads : Array.isArray(c.leads) ? c.leads.length : '—'}</td>
                  <td className="muted hide-mobile">{fmtDate(c.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function OwnerForm({ owner, onDone }: { owner?: Owner; onDone: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [f, setF] = useState<Partial<Owner>>({ name: owner?.name || '', phone: owner?.phone || '', address: owner?.address || '', link: owner?.link || '', notes: owner?.notes || '', is_agent: !!owner?.is_agent });
  const m = useMutation({
    mutationFn: () => (owner ? api.owners.update(owner.id, f) : api.owners.create(f)),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['owners'] }); toast('Сохранено'); onDone(); },
    onError: (e) => toast(errText(e), true),
  });
  return (
    <div className="col">
      <div className="form-grid">
        <Field label="Имя"><Input value={f.name || ''} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Телефон"><Input value={f.phone || ''} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Field>
        <Field label="Адрес" className="span2"><Input value={f.address || ''} onChange={(e) => setF({ ...f, address: e.target.value })} /></Field>
        <Field label="Ссылка" className="span2"><Input value={f.link || ''} onChange={(e) => setF({ ...f, link: e.target.value })} /></Field>
        <Field label="Заметки" className="span2"><Textarea value={f.notes || ''} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
      </div>
      <Switch checked={!!f.is_agent} onChange={(v) => setF({ ...f, is_agent: v })} label="Это агент" />
      <Button loading={m.isPending} onClick={() => m.mutate()}>Сохранить</Button>
    </div>
  );
}

export function OwnersPage() {
  const [q, setQ] = useState('');
  const [open, setOpen] = useState<Owner | 'new' | null>(null);
  const { data, isLoading, error } = useQuery({ queryKey: ['owners', q], queryFn: () => api.owners.list(q) });
  const items = itemsOf(data);
  const cur = open && open !== 'new' ? open : null;
  return (
    <>
      <PageHead title="Собственники" actions={<Button onClick={() => setOpen('new')}><Plus size={16} />Добавить</Button>} />
      <SearchBox value={q} onChange={setQ} placeholder="Имя, телефон, адрес" />
      {isLoading ? <Loading /> : error ? <ErrorBox error={error} /> : items.length === 0 ? <Empty text="Собственников не найдено" /> : (
        <div className="card table-wrap">
          <table className="table">
            <thead><tr><th>Имя</th><th>Телефон</th><th className="hide-mobile">Адрес</th><th>Метка</th><th className="hide-mobile">Добавлен</th></tr></thead>
            <tbody>
              {items.map((o) => (
                <tr key={o.id} className="clickable" onClick={() => setOpen(o)}>
                  <td><b>{o.name || '—'}</b></td>
                  <td className="num">{o.phone || <span className="muted">скрыт</span>}</td>
                  <td className="hide-mobile">{o.address || '—'}</td>
                  <td>{o.is_agent ? <Badge tone="warn">агент</Badge> : <Badge tone="ok">собственник</Badge>}</td>
                  <td className="muted hide-mobile">{fmtDate(o.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Drawer open={!!open} onClose={() => setOpen(null)} title={cur ? cur.name || 'Собственник' : 'Новый собственник'}>
        {open && (
          <div className="col gap-lg">
            <OwnerForm key={cur?.id ?? 'new'} owner={cur || undefined} onDone={() => setOpen(null)} />
            {cur && (cur.properties || []).length > 0 && (
              <Card label="Объекты" title="История съёмок">
                {(cur.properties || []).map((p) => (
                  <Link key={p.id} to={`/objects/${p.id}`} className="list-row" style={{ color: 'inherit', textDecoration: 'none' }}>
                    <span className="grow"><b>{p.address}</b><div className="faint" style={{ fontSize: 12 }}>{p.agent?.name} · {fmtDateTime(p.taken_at)}</div></span>
                    <PropertyStatusBadge status={p.status} />
                  </Link>
                ))}
              </Card>
            )}
          </div>
        )}
      </Drawer>
    </>
  );
}
