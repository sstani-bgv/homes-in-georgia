// Funnel 1 lists: new listings (C1), kanban (by status), my objects. Shared by desktop and /m.
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ExternalLink, Search } from 'lucide-react';
import { api, type ListingFilter } from '../../api';
import { itemsOf, mediaUrl } from '../../api/client';
import type { Property } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { ListingCard, PropertyStatusBadge, Place } from '../../components/domain';
import { errText, useToast } from '../../components/toast';
import { Button, Empty, ErrorBox, Loading, Select, Seg } from '../../components/ui';
import { PROPERTY_BOARD, PROPERTY_STATUS, ago, bedroomsLabel, fmtDateTime, money } from '../../lib/format';

/** "fb:Some group" -> "Facebook · Some group"; site sources stay as they are. */
const PREFIX_LABEL: Record<string, string> = { fb: 'Facebook', ig: 'Instagram', tg: 'Telegram' };
const sourceName = (s: string) => { const [k, ...rest] = s.split(':'); return PREFIX_LABEL[k] && rest.length ? `${PREFIX_LABEL[k]} · ${rest.join(':')}` : s; };
const BEDS = [{ value: '0', label: 'Студия' }, { value: '1', label: '1' }, { value: '2', label: '2' }, { value: '3', label: '3' }, { value: '4', label: '4+' }];

const PAGE = 50;

export function NewListings({ base = '' }: { base?: string }) {
  const [f, setF0] = useState<ListingFilter>({ q: '', flag: '' });
  const [limit, setLimit] = useState(PAGE);
  const setF = (v: ListingFilter | ((o: ListingFilter) => ListingFilter)) => { setF0(v); setLimit(PAGE); };
  const set = (k: keyof ListingFilter) => (v: string) => setF((o) => ({ ...o, [k]: v }));
  const beds = new Set((f.bedrooms || '').split(',').filter(Boolean));
  const toggleBed = (b: string) => { beds.has(b) ? beds.delete(b) : beds.add(b); set('bedrooms')([...beds].sort().join(',')); };
  const { user } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const { data, isLoading, isFetching, error } = useQuery({ queryKey: ['listings', 'new', f, limit], queryFn: () => api.listings.new(f, limit), placeholderData: (p) => p });
  const { data: facets } = useQuery({ queryKey: ['listings', 'facets'], queryFn: () => api.listings.facets(), staleTime: 60_000 });
  const opts = (xs?: { value: string; count: number }[], label = (v: string) => v) => (xs || []).map((x) => ({ value: x.value, label: `${label(x.value)} (${x.count})` }));
  const filtered = Object.entries(f).some(([k, v]) => v && k !== 'q');
  const take = useMutation({
    mutationFn: (id: string) => api.properties.take(id),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ['listings'] });
      qc.invalidateQueries({ queryKey: ['properties'] });
      toast('Объект закреплён за вами');
      navigate(base === '/m' ? `/m/properties/${p.id}` : `/objects/${p.id}`);
    },
    onError: (e) => { toast(errText(e), true); qc.invalidateQueries({ queryKey: ['listings'] }); },
  });
  const items = itemsOf(data);

  return (
    <>
      <div className="row row-wrap" style={{ marginBottom: 14 }}>
        <div className="input-icon grow" style={{ minWidth: 200, maxWidth: 380 }}>
          <Search size={16} />
          <input className="input" placeholder="Район, адрес" value={f.q || ''} onChange={(e) => set('q')(e.target.value)} />
        </div>
        <Seg value={f.flag || ''} onChange={set('flag')} options={[
          { value: '', label: 'Все' }, { value: 'owner', label: 'Собственник' }, { value: 'agent_suspect', label: 'Агент?' }, { value: 'duplicate', label: 'Дубль' },
        ]} />
      </div>
      <div className="row row-wrap gap-sm" style={{ marginBottom: 14 }}>
        <Select aria-label="Источник" value={f.source || ''} onChange={(e) => set('source')(e.target.value)} placeholder="Все источники" options={opts(facets?.sources, sourceName)} style={{ maxWidth: 260 }} />
        <Select aria-label="Район" value={f.district || ''} onChange={(e) => set('district')(e.target.value)} placeholder="Все районы" options={opts(facets?.districts)} style={{ maxWidth: 200 }} />
        <Select aria-label="Метро" value={f.metro || ''} onChange={(e) => set('metro')(e.target.value)} placeholder="Любое метро" options={opts(facets?.metros)} style={{ maxWidth: 200 }} />
        <input className="input" type="number" inputMode="numeric" min={0} placeholder="Цена от $" value={f.price_min || ''} onChange={(e) => set('price_min')(e.target.value)} style={{ width: 110 }} />
        <input className="input" type="number" inputMode="numeric" min={0} placeholder="до $" value={f.price_max || ''} onChange={(e) => set('price_max')(e.target.value)} style={{ width: 90 }} />
        <div className="row gap-sm" role="group" aria-label="Спальни">
          {BEDS.map((b) => <Button key={b.value} size="sm" variant={beds.has(b.value) ? 'primary' : 'secondary'} aria-pressed={beds.has(b.value)} onClick={() => toggleBed(b.value)}>{b.label}</Button>)}
        </div>
        {filtered && <Button size="sm" variant="ghost" onClick={() => setF({ q: f.q, flag: '' })}>Сбросить</Button>}
      </div>
      {data && <div className="faint" style={{ fontSize: 12, marginBottom: 8 }}>Найдено: {data.total ?? itemsOf(data).length}</div>}
      {isLoading ? <Loading /> : error ? <ErrorBox error={error} /> : items.length === 0 ? <Empty text="Новых объявлений нет" /> : (
        <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))' }}>
          {items.map((l) => {
            const lockedByOther = !!l.taken_by && l.taken_by.id !== user?.id;
            return (
              <ListingCard key={l.id} listing={l} locked={lockedByOther ? `Взял ${l.taken_by?.name}` : undefined}
                footer={
                  <div className="row">
                    <Button block disabled={lockedByOther} loading={take.isPending && take.variables === l.id} onClick={() => take.mutate(l.id)}>Беру в работу</Button>
                    {l.url && <a className="btn btn-secondary icon-btn" href={l.url} target="_blank" rel="noreferrer" title="Открыть источник"><ExternalLink size={16} /></a>}
                  </div>
                } />
            );
          })}
        </div>
      )}
      {(data?.total ?? 0) > items.length && (
        <div className="row" style={{ justifyContent: 'center', marginTop: 16 }}>
          <Button variant="secondary" loading={isFetching} onClick={() => setLimit((n) => n + PAGE)}>Показать ещё ({(data?.total ?? 0) - items.length})</Button>
        </div>
      )}
    </>
  );
}

export function PropertyMiniCard({ p, to }: { p: Property; to: string }) {
  const ph = p.media?.find((m) => m.kind === 'photo')?.path || p.photos?.[0];
  return (
    <Link to={to} className="kcard">
      <div className="row" style={{ alignItems: 'flex-start' }}>
        {ph && <img src={mediaUrl(ph)} alt="" style={{ width: 52, height: 52, borderRadius: 8, objectFit: 'cover', flex: 'none' }} />}
        <div className="grow">
          <div className="kcard-title ellipsis">{p.address || `Объект #${p.id}`}</div>
          <div className="kcard-meta">{[p.district, bedroomsLabel(p.bedrooms), money(p.price, p.currency || 'USD')].filter(Boolean).join(' · ')}</div>
        </div>
      </div>
      <div className="row row-between mt" style={{ marginTop: 8 }}>
        <span className="faint" style={{ fontSize: 11, fontWeight: 600 }}>{p.agent?.name || '—'}</span>
        {p.status === 'call_failed' && p.next_call_at ? <span className="badge badge-redsoft">звонок {fmtDateTime(p.next_call_at)}</span>
          : p.shoot_at && p.status === 'shoot_scheduled' ? <span className="badge">съёмка {fmtDateTime(p.shoot_at)}</span>
          : <span className="faint" style={{ fontSize: 11 }}>{ago(p.updated_at || p.created_at)}</span>}
      </div>
    </Link>
  );
}

export function PropertyBoard() {
  const { data, isLoading, error } = useQuery({ queryKey: ['properties', 'board'], queryFn: api.properties.board, refetchInterval: 30_000 });
  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  return (
    <div className="kanban">
      {PROPERTY_BOARD.map((s) => {
        const items = data?.[s] || [];
        return (
          <div className="kanban-col" key={s}>
            <div className="kanban-col-head"><span className="label">{PROPERTY_STATUS[s]}</span><span className="tab-count">{items.length}</span></div>
            <div className="kanban-col-body">
              {items.length === 0 ? <div className="faint" style={{ fontSize: 12, padding: 6 }}>Пусто</div> : items.map((p) => <PropertyMiniCard key={p.id} p={p} to={`/objects/${p.id}`} />)}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function PropertyList({ mine, base = '' }: { mine?: boolean; base?: string }) {
  const [q, setQ] = useState('');
  const [status, setStatus] = useState('');
  const { data, isLoading, error } = useQuery({
    queryKey: ['properties', 'list', mine ? 'mine' : 'all', q, status],
    queryFn: () => api.properties.list({ mine: mine ? 1 : undefined, q, status }),
    placeholderData: (p, prev) => (prev?.queryKey[2] === (mine ? 'mine' : 'all') ? p : undefined), // keep the list while a filter reloads, never across tabs
  });
  const items = itemsOf(data);
  const navigate = useNavigate();
  const link = (id: number) => (base === '/m' ? `/m/properties/${id}` : `/objects/${id}`);
  return (
    <>
      <div className="row row-wrap" style={{ marginBottom: 14 }}>
        <div className="input-icon grow" style={{ minWidth: 180, maxWidth: 380 }}>
          <Search size={16} />
          <input className="input" placeholder="Адрес, район, номер" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <select className="select" style={{ width: 'auto' }} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Все статусы</option>
          {Object.entries(PROPERTY_STATUS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </div>
      {isLoading ? <Loading /> : error ? <ErrorBox error={error} /> : items.length === 0 ? <Empty text="Объектов нет" /> : (
        <>
          <div className="card hide-mobile">
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>#</th><th>Адрес</th><th>Цена</th><th>Агент</th><th>Статус</th><th>Обновлён</th></tr></thead>
                <tbody>
                  {items.map((p) => (
                    <tr key={p.id} className="clickable" onClick={() => navigate(link(p.id))}>
                      <td className="muted num">{p.id}</td>
                      <td><Link to={link(p.id)} style={{ fontWeight: 700, color: 'inherit' }}>{p.address || '—'}</Link><div><Place district={p.district} /></div></td>
                      <td className="num" style={{ fontWeight: 700 }}>{money(p.price, p.currency || 'USD')}</td>
                      <td>{p.agent?.name || '—'}</td>
                      <td><PropertyStatusBadge status={p.status} /></td>
                      <td className="muted">{ago(p.updated_at || p.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <div className="col only-mobile">
            {items.map((p) => (
              <div key={p.id}>
                <PropertyMiniCard p={p} to={link(p.id)} />
              </div>
            ))}
          </div>
        </>
      )}
    </>
  );
}
