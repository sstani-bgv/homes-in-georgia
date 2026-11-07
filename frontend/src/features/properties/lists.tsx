// Funnel 1 lists: new listings (C1), kanban (by status), my objects. Shared by desktop and /m.
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ExternalLink, Search, Users } from 'lucide-react';
import { api, type ListingFilter } from '../../api';
import { itemsOf, mediaUrl } from '../../api/client';
import type { Property } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { ChipButton, ExclusiveConflictBadge, ListingCard, PropertyStatusBadge, Place } from '../../components/domain';
import { errText, useToast } from '../../components/toast';
import { Badge, Button, Empty, ErrorBox, Field, KV, Loading, Modal, Select, Seg } from '../../components/ui';
import { ARCHIVE_REASON, PROPERTY_BOARD, PROPERTY_STATUS, ago, bedroomsLabel, demandLabel, fmtDateTime, money } from '../../lib/format';

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
  const [peek, setPeek] = useState<{ id: number; match?: { listing: string; reasons: string[] } } | null>(null);
  const { data, isLoading, isFetching, error } = useQuery({ queryKey: ['listings', 'new', f, limit], queryFn: () => api.listings.new(f, limit), placeholderData: (p) => p });
  const { data: facets } = useQuery({ queryKey: ['listings', 'facets'], queryFn: () => api.listings.facets(), staleTime: 60_000 });
  const opts = (xs?: { value: string; count: number }[], label = (v: string) => v) => (xs || []).map((x) => ({ value: x.value, label: `${label(x.value)} (${x.count})` }));
  const filtered = Object.entries(f).some(([k, v]) => v && k !== 'q' && k !== 'known');
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
  const [agentFor, setAgentFor] = useState<string | null>(null);
  const [agentC, setAgentC] = useState({ owner_name: '', owner_phone: '' });
  const markAgent = useMutation({
    mutationFn: () => api.listings.agent(agentFor!, agentC),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['listings'] });
      qc.invalidateQueries({ queryKey: ['properties'] });
      setAgentFor(null); setAgentC({ owner_name: '', owner_phone: '' });
      toast('Отмечено как агент, контакт сохранён в базе');
    },
    onError: (e) => toast(errText(e), true),
  });
  const dismiss = useMutation({
    mutationFn: (id: string) => api.listings.dismiss(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['listings'] }); toast('Убрано из ленты как неактуальное'); },
    onError: (e) => toast(errText(e), true),
  });
  const items = itemsOf(data);

  return (
    <>
      {peek && <PropertyModal id={peek.id} match={peek.match} base={base} onClose={() => setPeek(null)} />}
      <Modal open={!!agentFor} onClose={() => setAgentFor(null)} title="Это агент">
        <form className="col gap-lg" onSubmit={(e) => { e.preventDefault(); markAgent.mutate(); }}>
          <Field label="Имя"><input className="input" value={agentC.owner_name} onChange={(e) => setAgentC((c) => ({ ...c, owner_name: e.target.value }))} autoFocus /></Field>
          <Field label="Телефон" hint="Номер попадёт в базу агентов: его объявления дальше будут помечаться автоматически">
            <input className="input" type="tel" value={agentC.owner_phone} onChange={(e) => setAgentC((c) => ({ ...c, owner_phone: e.target.value }))} />
          </Field>
          <Button type="submit" block loading={markAgent.isPending} disabled={!agentC.owner_name.trim() && !agentC.owner_phone.trim()}>Сохранить</Button>
        </form>
      </Modal>
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
        {filtered && <Button size="sm" variant="ghost" onClick={() => setF({ q: f.q, flag: '', known: f.known })}>Сбросить</Button>}
        {(!!data?.known_total || f.known) && (
          <Button size="sm" variant={f.known ? 'primary' : 'secondary'} aria-pressed={!!f.known} onClick={() => set('known')(f.known ? '' : '1')} title={KNOWN_HINT}>
            Уже в базе ({data?.known_total ?? 0})
          </Button>
        )}
      </div>
      {f.known && <div className="muted" style={{ fontSize: 12, marginBottom: 8 }}>{KNOWN_HINT}</div>}
      {data && <div className="faint" style={{ fontSize: 12, marginBottom: 8 }}>Найдено: {data.total ?? itemsOf(data).length}</div>}
      {isLoading ? <Loading /> : error ? <ErrorBox error={error} /> : items.length === 0 ? <Empty text="Новых объявлений нет" /> : (
        <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))' }}>
          {items.map((l) => {
            const lockedByOther = !!l.taken_by && l.taken_by.id !== user?.id;
            const chips = (l.known_object || !!l.matches?.length) && (
              <>
                {l.known_object && (
                  <ChipButton tone="ok" title={`Система узнала объявление как объект #${l.known_object.id}${l.known_object.address ? ` · ${l.known_object.address}` : ''}: оно прикреплено к нему автоматически`}
                    onClick={() => setPeek({ id: l.known_object!.id })}>Уже в базе · #{l.known_object.id}</ChipButton>
                )}
                {(l.matches || []).map((m) => (
                  <ChipButton key={m.property_id} tone="redsoft" title={`Похож на объект #${m.property_id}${m.address ? ` · ${m.address}` : ''}: ${m.reasons.join(', ')}`}
                    onClick={() => setPeek({ id: m.property_id, match: { listing: l.id, reasons: m.reasons } })}>Похож на #{m.property_id}</ChipButton>
                ))}
              </>
            );
            return (
              <ListingCard key={l.id} listing={l} locked={lockedByOther ? `Взял ${l.taken_by?.name}` : undefined} chips={chips || undefined}
                footer={
                  <div className="col gap-sm">
                    {!!l.demand && <div className="alert alert-info row gap-sm" style={{ fontSize: 12, fontWeight: 700 }}><Users size={14} />{demandLabel(l.demand)}</div>}
                    <div className="row">
                      <Button block disabled={lockedByOther} loading={take.isPending && take.variables === l.id} onClick={() => take.mutate(l.id)}>Беру в работу</Button>
                      {l.url && <a className="btn btn-secondary icon-btn" href={l.url} target="_blank" rel="noreferrer" title="Открыть источник"><ExternalLink size={16} /></a>}
                    </div>
                    <div className="row">
                      <Button size="sm" variant="secondary" block disabled={lockedByOther} onClick={() => setAgentFor(l.id)}>Агент</Button>
                      <Button size="sm" variant="secondary" block disabled={lockedByOther} loading={dismiss.isPending && dismiss.variables === l.id}
                        onClick={() => { if (window.confirm('Объявление удалено или неактуально? Оно уйдёт из ленты.')) dismiss.mutate(l.id); }}>Неактуально</Button>
                    </div>
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

const KNOWN_HINT = 'Объявления, которые система узнала как уже существующий объект: они прикреплены к нему автоматически';
const propLink = (base: string, id: number) => (base === '/m' ? `/m/properties/${id}` : `/objects/${id}`);

/** The object in a modal (no page change): photos, parameters, owner if visible, «Открыть полностью».
 * match: opened from a «Похож на #N» chip — the operator decides «Тот же объект» / «Другой» right here. */
export function PropertyModal({ id, match, base = '', onClose }: { id: number; match?: { listing: string; reasons: string[] }; base?: string; onClose: () => void }) {
  const { isStaff } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const { data: p, isLoading, error } = useQuery({ queryKey: ['property', id], queryFn: () => api.properties.get(id) });
  const decide = useMutation({
    mutationFn: (same: boolean) => api.listings.decideMatch(match!.listing, id, same),
    onSuccess: (_, same) => { qc.invalidateQueries({ queryKey: ['listings'] }); toast(same ? `Объявление привязано к объекту #${id}` : 'Отмечено: другой объект'); onClose(); },
    onError: (e) => toast(errText(e), true),
  });
  const own = (p?.media || []).filter((m) => m.kind === 'photo').map((m) => m.url || m.path);
  const shots = own.length ? own : p?.photos || [];
  const phone = p?.owner?.phone;
  return (
    <Modal open onClose={onClose} title={p?.address || `Объект #${id}`}>
      {isLoading ? <Loading /> : error ? <ErrorBox error={error} /> : p && (
        <div className="col gap-lg">
          {!!shots.length && (
            <div className="row" style={{ overflowX: 'auto', gap: 8 }}>
              {shots.map((src) => <img key={src} src={mediaUrl(src)} alt="" loading="lazy" style={{ height: 160, borderRadius: 10, flex: 'none' }} />)}
            </div>
          )}
          <div className="row row-wrap gap-sm">
            <Badge tone="grey">#{p.id}</Badge><PropertyStatusBadge status={p.status} />
            {p.is_exclusive && <Badge tone="solid">эксклюзив</Badge>}<ExclusiveConflictBadge p={p} />
          </div>
          <KV items={[
            ['Цена', money(p.price, p.currency || 'USD')], ['Район', p.district || '—'], ['Спальни', bedroomsLabel(p.bedrooms) || '—'],
            ['Площадь', p.area ? `${p.area} м²` : '—'], ['Этаж', p.floor ? `${p.floor}${p.total_floors ? ` из ${p.total_floors}` : ''}` : '—'],
            ['Агент', p.agent?.name || '—'],
            ...(p.owner ? [['Собственник', <>{p.owner.name || '—'}{phone && <> · <a href={`tel:${phone.replace(/[^+\d]/g, '')}`}>{phone}</a></>}</>] as [string, React.ReactNode]] : []),
          ]} />
          {match && (
            <div className="alert alert-warn col gap-sm" style={{ fontSize: 13 }}>
              <span>Похож на объявление: {match.reasons.join(', ') || 'параметры совпадают'}</span>
              {isStaff && (
                <div className="row">
                  <Button size="sm" block loading={decide.isPending && decide.variables} onClick={() => decide.mutate(true)}>Тот же объект</Button>
                  <Button size="sm" variant="secondary" block loading={decide.isPending && !decide.variables} onClick={() => decide.mutate(false)}>Другой</Button>
                </div>
              )}
            </div>
          )}
          <Link to={propLink(base, id)} className="btn btn-secondary">Открыть полностью</Link>
        </div>
      )}
    </Modal>
  );
}

/** «База объектов»: the same card as «Новые объявления»; click opens PropertyModal. */
function PropertyCard({ p, onOpen }: { p: Property; onOpen: () => void }) {
  return (
    <article className="card lcard" role="button" tabIndex={0} onClick={onOpen} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen(); } }} style={{ cursor: 'pointer' }}>
      <div className="lcard-photo">
        {p.photo ? <img src={mediaUrl(p.photo)} alt="" loading="lazy" /> : null}
        <div className="badges"><PropertyStatusBadge status={p.status} />{p.is_exclusive && <Badge tone="solid">эксклюзив</Badge>}<ExclusiveConflictBadge p={p} /></div>
        {p.address && <span className="plaque">{p.address}</span>}
      </div>
      <div className="lcard-body">
        <span className="label">{[p.district, bedroomsLabel(p.bedrooms), p.area ? `${p.area} м²` : null].filter(Boolean).join(' · ') || '—'}</span>
        <div className="price">{money(p.price, p.currency || 'USD')}<span className="muted" style={{ fontSize: 12, fontWeight: 600 }}>/мес</span></div>
        <div className="row row-between gap-sm faint" style={{ fontSize: 12, marginTop: 'auto' }}>
          <span className="ellipsis">#{p.id} · {p.agent?.name || 'без агента'}</span>
          <span style={{ flex: 'none' }}>{ago(p.updated_at || p.created_at)}</span>
        </div>
      </div>
    </article>
  );
}

export function PropertyMiniCard({ p, to }: { p: Property; to: string }) {
  const ph = p.photo || p.media?.find((m) => m.kind === 'photo')?.path || p.photos?.[0];
  return (
    <Link to={to} className="kcard">
      <div className="row" style={{ alignItems: 'flex-start' }}>
        {ph && <img src={mediaUrl(ph)} alt="" style={{ width: 52, height: 52, borderRadius: 8, objectFit: 'cover', flex: 'none' }} />}
        <div className="grow">
          <div className="kcard-title ellipsis">{p.address || `Объект #${p.id}`}</div>
          <div className="kcard-meta">{[p.district, bedroomsLabel(p.bedrooms), money(p.price, p.currency || 'USD')].filter(Boolean).join(' · ')}</div>
          <ExclusiveConflictBadge p={p} />
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

const LIVE = (Object.keys(PROPERTY_STATUS) as (keyof typeof PROPERTY_STATUS)[]).filter((s) => s !== 'archived');

/** mine: the agent's own objects; archived: «Архив»; only: one fixed status (operator queue «На публикацию» = review);
 * otherwise «База объектов» — every live object, for everyone (owner contacts stay hidden by the API for other agents' objects). */
export function PropertyList({ mine, archived, status: only, base = '' }: { mine?: boolean; archived?: boolean; status?: string; base?: string }) {
  const [q, setQ] = useState('');
  const [status, setStatus] = useState('');
  const scope = archived ? 'archive' : only ? `only:${only}` : mine ? 'mine' : 'all';
  const { data, isLoading, error } = useQuery({
    queryKey: ['properties', 'list', scope, q, status],
    queryFn: () => api.properties.list({ mine: mine ? 1 : undefined, q, status: archived ? 'archived' : only || status || (mine ? '' : LIVE.join(',')) }),
    placeholderData: (p, prev) => (prev?.queryKey[2] === scope ? p : undefined), // keep the list while a filter reloads, never across tabs
  });
  const items = itemsOf(data);
  const navigate = useNavigate();
  const link = (id: number) => propLink(base, id);
  const [peek, setPeek] = useState<number | null>(null);
  return (
    <>
      <div className="row row-wrap" style={{ marginBottom: 14 }}>
        <div className="input-icon grow" style={{ minWidth: 180, maxWidth: 380 }}>
          <Search size={16} />
          <input className="input" placeholder="Адрес, район, номер" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        {!archived && !only && (
          <select className="select" style={{ width: 'auto' }} value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Все статусы</option>
            {(mine ? Object.keys(PROPERTY_STATUS) as typeof LIVE : LIVE).map((k) => <option key={k} value={k}>{PROPERTY_STATUS[k]}</option>)}
          </select>
        )}
      </div>
      {peek !== null && <PropertyModal id={peek} base={base} onClose={() => setPeek(null)} />}
      {isLoading ? <Loading /> : error ? <ErrorBox error={error} /> : items.length === 0 ? <Empty text={archived ? 'Архив пуст' : only === 'review' ? 'Очередь пуста: всё опубликовано' : 'Объектов нет'} /> : scope === 'all' ? (
        <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))' }}>
          {items.map((p) => <PropertyCard key={p.id} p={p} onOpen={() => setPeek(p.id)} />)}
        </div>
      ) : (
        <>
          <div className="card hide-mobile">
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>#</th><th>Адрес</th><th>Цена</th><th>Агент</th><th>Статус</th>{archived && <th>Причина</th>}<th>Обновлён</th></tr></thead>
                <tbody>
                  {items.map((p) => (
                    <tr key={p.id} className="clickable" onClick={() => navigate(link(p.id))}>
                      <td className="muted num">{p.id}</td>
                      <td><Link to={link(p.id)} style={{ fontWeight: 700, color: 'inherit' }}>{p.address || '—'}</Link><div><Place district={p.district} /></div></td>
                      <td className="num" style={{ fontWeight: 700 }}>{money(p.price, p.currency || 'USD')}</td>
                      <td>{p.agent?.name || '—'}</td>
                      <td><div className="row row-wrap gap-sm"><PropertyStatusBadge status={p.status} /><ExclusiveConflictBadge p={p} /></div></td>
                      {archived && <td>{ARCHIVE_REASON[p.archive_reason || ''] || '—'}{p.archive_comment && <div className="muted" style={{ fontSize: 12 }}>{p.archive_comment}</div>}</td>}
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
                {archived && p.archive_reason && <div className="muted" style={{ fontSize: 12, padding: '4px 6px' }}>Причина: {ARCHIVE_REASON[p.archive_reason] || p.archive_reason}{p.archive_comment ? ` · ${p.archive_comment}` : ''}</div>}
              </div>
            ))}
          </div>
        </>
      )}
    </>
  );
}
