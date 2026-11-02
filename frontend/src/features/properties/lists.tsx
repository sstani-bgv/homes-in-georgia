// Funnel 1 lists: new listings (C1), kanban (by status), my objects. Shared by desktop and /m.
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ExternalLink, Search } from 'lucide-react';
import { api } from '../../api';
import { itemsOf, mediaUrl } from '../../api/client';
import type { Property } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { ListingCard, PropertyStatusBadge, Place } from '../../components/domain';
import { errText, useToast } from '../../components/toast';
import { Button, Empty, ErrorBox, Loading, Seg } from '../../components/ui';
import { PROPERTY_BOARD, PROPERTY_STATUS, ago, bedroomsLabel, fmtDateTime, money } from '../../lib/format';

export function NewListings({ base = '' }: { base?: string }) {
  const [q, setQ] = useState('');
  const [flag, setFlag] = useState<string>('');
  const { user } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const { data, isLoading, error } = useQuery({ queryKey: ['listings', 'new', q, flag], queryFn: () => api.listings.new({ q, flag }) });
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
          <input className="input" placeholder="Район, адрес" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <Seg value={flag} onChange={setFlag} options={[
          { value: '', label: 'Все' }, { value: 'owner', label: 'Собственник' }, { value: 'agent_suspect', label: 'Агент?' }, { value: 'duplicate', label: 'Дубль' },
        ]} />
      </div>
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
