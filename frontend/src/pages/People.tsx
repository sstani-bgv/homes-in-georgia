// Клиенты and Собственники: search tables + detail drawers.
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Heart, Plus, Search, ThumbsDown } from 'lucide-react';
import { api } from '../api';
import { itemsOf } from '../api/client';
import type { Owner } from '../api/types';
import { LeadStatusBadge, PropertyStatusBadge } from '../components/domain';
import { errText, useToast } from '../components/toast';
import { Badge, Button, Card, Drawer, Empty, ErrorBox, Field, Input, KV, Loading, PageHead, Switch, Textarea } from '../components/ui';
import { ProfileView } from '../features/leads/LeadDetail';
import { ago, fmtDate, fmtDateTime, money } from '../lib/format';

function SearchBox({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  return (
    <div className="input-icon" style={{ maxWidth: 420, marginBottom: 14 }}>
      <Search size={16} />
      <input className="input" placeholder={placeholder} value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

function ClientView({ id }: { id: number }) {
  const { data: c, isLoading, error } = useQuery({ queryKey: ['client', id], queryFn: () => api.clients.get(id) });
  if (error) return <ErrorBox error={error} />;
  if (isLoading || !c) return <Loading />;
  const leads = Array.isArray(c.leads) ? c.leads : [];
  return (
    <div className="col gap-lg">
      <KV items={[['Telegram', c.username ? <a href={`https://t.me/${c.username}`} target="_blank" rel="noreferrer">@{c.username}</a> : '—'], ['Телефон', c.phone || '—'], ['Язык', (c.lang || '—').toUpperCase()], ['С нами с', fmtDate(c.created_at)]]} />
      <Card label="Анкета" title={c.profile_done ? 'Заполнена' : 'Не заполнена'}><ProfileView profile={c.profile || {}} /></Card>
      <Card label="Заявки" title={`${leads.length}`}>
        {leads.length === 0 ? <div className="muted">Заявок нет</div> : leads.map((l) => (
          <Link key={l.id} to={`/leads/${l.id}`} className="list-row" style={{ color: 'inherit', textDecoration: 'none' }}>
            <span className="label">#{l.id}</span><span className="grow ellipsis" style={{ fontWeight: 600 }}>{l.listing?.title}</span><LeadStatusBadge status={l.status} />
          </Link>
        ))}
      </Card>
      {(c.reactions || []).length > 0 && (
        <Card label="Реакции" title="Подбор">
          {(c.reactions || []).map((r, i) => (
            <div key={i} className="list-row">
              {r.reaction === 'like' ? <Heart size={16} color="var(--accent)" /> : <ThumbsDown size={16} color="var(--faint)" />}
              <span className="grow ellipsis">{r.listing?.title || r.listing_id}</span><span className="faint" style={{ fontSize: 12 }}>{r.created_at ? ago(r.created_at) : ''}</span>
            </div>
          ))}
        </Card>
      )}
    </div>
  );
}

export function ClientsPage() {
  const [q, setQ] = useState('');
  const [open, setOpen] = useState<number | null>(null);
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
                <tr key={c.id} className="clickable" onClick={() => setOpen(c.id)}>
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
      <Drawer open={!!open} onClose={() => setOpen(null)} title={items.find((c) => c.id === open)?.name || 'Клиент'}>{open && <ClientView id={open} />}</Drawer>
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
