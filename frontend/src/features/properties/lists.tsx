// Funnel 1 lists: new listings (C1), kanban (by status), my objects. Shared by desktop and /m.
import { useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Archive, ArrowRightLeft, DollarSign, ExternalLink, List, Map as MapIcon, Search, Users } from 'lucide-react';
import { api, type ListingFilter, type PropertyScope } from '../../api';
import { itemsOf, thumbUrl } from '../../api/client';
import type { Listing, Property, PropertyStatus } from '../../api/types';
import { FIELD_FILTER } from '../../api/types';
import { StatusBar, dragBoardProps } from '../leads/LeadBoard';
import { pageScroller, useAuth } from '../../auth/AuthContext';
import { ChipButton, CompareSide, CopyPhone, ExclusiveConflictBadge, ListingCard, ListingSide, PropertyStatusBadge, Place } from '../../components/domain';
import { errText, useToast } from '../../components/toast';
import { Badge, Button, Empty, ErrorBox, Field, Loading, Modal, MultiSelect, Select, Seg } from '../../components/ui';
import { ListingsMap } from './ListingsMap';
import { metroLabel } from './PropertyDetail';
import { usePageState } from '../../layout/back';
import { ARCHIVE_REASON, PLATFORM, PROPERTY_BOARD, PROPERTY_STATUS, SOURCE_LABEL, ago, bedroomsLabel, demandLabel, districtLabel, fmtDateTime, formatPhone, money, phoneBlur, phoneFocus, phoneInput } from '../../lib/format';

/** "fb:Some group" -> "Facebook · Some group"; site sources stay as they are. */
const PREFIX_LABEL: Record<string, string> = { fb: 'Facebook', ig: 'Instagram', th: 'Threads', tg: 'Telegram' };
/** a Facebook group is keyed by its numeric id: its name from «Источники» (facet label), else «группа …1234» */
const sourceName = (s: string, label?: string) => {
  const [k, ...rest] = s.split(':'), key = rest.join(':');
  if (!PREFIX_LABEL[k] || !rest.length) return SOURCE_LABEL[s] || s;
  return `${PREFIX_LABEL[k]} · ${label || (/^\d{8,}$/.test(key) ? `группа …${key.slice(-4)}` : key)}`;
};
const BEDS = [{ value: '0', label: 'Студия' }, { value: '1', label: '1' }, { value: '2', label: '2' }, { value: '3', label: '3' }, { value: '4', label: '4+' }];

type Facet = { value: string; count: number; label?: string }[];
/** the filter fields the feed and the base share (ListingFilter, PropertyList's URL) */
type CommonFilter = { q?: string; source?: string[]; district?: string[]; metro?: string; bedrooms?: string; price_min?: string; price_max?: string };

/** The mock-up of 11-13: search + list/map, a full-width segment, a 2-column grid of labelled filters (+ `boxes`),
 * bedrooms, «Найдено · Сбросить». «Новые объявления» and the base tabs (Stanislav 11-13); children go under it. */
function FeedFilters({ f, patch, view, onView, seg, facets, sourceLabel, metroLabel = (v) => v, boxes, found, filtered, onReset, actions, placeholder = 'Район, адрес', children }: {
  f: CommonFilter; patch: (p: CommonFilter) => void; view?: 'list' | 'map'; onView?: (v: 'list' | 'map') => void;
  seg?: { value: string; onChange: (v: string) => void; options: { value: string; label: string }[] };
  facets?: { sources?: Facet; districts?: Facet; metros?: Facet }; sourceLabel: (v: string, label?: string) => string; metroLabel?: (v: string) => string;
  boxes?: React.ReactNode; found: React.ReactNode; filtered: boolean; onReset: () => void; actions?: React.ReactNode; placeholder?: string; children?: React.ReactNode;
}) {
  const beds = new Set((f.bedrooms || '').split(',').filter(Boolean));
  const toggleBed = (b: string) => { beds.has(b) ? beds.delete(b) : beds.add(b); patch({ bedrooms: [...beds].sort().join(',') }); };
  return (
    <div className="feed-filters">
      <div className="row" style={{ gap: 10 }}>
        <div className="input-icon grow">
          <Search size={16} />
          <input className="input" placeholder={placeholder} value={f.q || ''} onChange={(e) => patch({ q: e.target.value })} />
        </div>
        {view && onView && <Seg value={view} onChange={(v) => onView(v as 'list' | 'map')} options={[
          { value: 'list', label: <List size={18} aria-label="Список" /> }, { value: 'map', label: <MapIcon size={18} aria-label="Карта" /> }]} />}
      </div>
      {seg && <div className="seg-full"><Seg value={seg.value} onChange={seg.onChange} options={seg.options} /></div>}
      <div className="filter-grid">
        <div className="fbox"><span className="flabel">Источник</span>
          <MultiSelect label="Источник" value={f.source || []} onChange={(v) => patch({ source: v })} placeholder="Все"
            options={(facets?.sources || []).map((x) => ({ value: x.value, label: sourceLabel(x.value, x.label), count: x.count }))} /></div>
        <div className="fbox"><span className="flabel">Район</span>
          <MultiSelect label="Район" value={f.district || []} onChange={(v) => patch({ district: v })} placeholder="Все" many={['район', 'района', 'районов']}
            options={(facets?.districts || []).map((x) => ({ value: x.value, label: districtLabel(x.value), count: x.count }))} /></div>
        <div className="fbox"><span className="flabel">Метро</span>
          <Select aria-label="Метро" value={f.metro || ''} onChange={(e) => patch({ metro: e.target.value })} placeholder="Любое"
            options={(facets?.metros || []).map((x) => ({ value: x.value, label: `${metroLabel(x.value)} (${x.count})` }))} /></div>
        <div className="fbox fbox-price"><span className="flabel">Цена, $</span>
          <input className="input" type="number" inputMode="numeric" min={0} placeholder="от" aria-label="Цена от" value={f.price_min || ''} onChange={(e) => patch({ price_min: e.target.value })} />
          <span className="faint">—</span>
          <input className="input" type="number" inputMode="numeric" min={0} placeholder="до" aria-label="Цена до" value={f.price_max || ''} onChange={(e) => patch({ price_max: e.target.value })} />
          <DollarSign size={16} className="faint" /></div>
        {boxes}
      </div>
      <div className="beds-row" role="group" aria-label="Спальни">
        {BEDS.map((b) => <button key={b.value} type="button" className={beds.has(b.value) ? 'active' : ''} aria-pressed={beds.has(b.value)} onClick={() => toggleBed(b.value)}>{b.label}</button>)}
      </div>
      <div className="row row-between row-wrap" style={{ gap: 8 }}>
        <span className="faint" style={{ fontSize: 13 }}>{found}</span>
        <div className="row gap-sm">
          {actions}
          {filtered && <button type="button" className="link-btn link-red" onClick={onReset}>Сбросить фильтры</button>}
        </div>
      </div>
      {children}
    </div>
  );
}

const PAGE = 50;
const MAP_LIMIT = 500;  // ponytail: the map loads the first 500 filtered ads; a bbox query when the feed grows past it

export function NewListings({ base = '' }: { base?: string }) {
  const [f, setF0] = usePageState<ListingFilter>('feed-filter', { q: '', flag: '' });
  const [limit, setLimit] = usePageState('feed-limit', PAGE);
  const [view, setView] = usePageState<'list' | 'map'>('feed-view', 'list');
  const setF = (v: ListingFilter | ((o: ListingFilter) => ListingFilter)) => { setF0(v); setLimit(PAGE); };
  const set = (k: keyof ListingFilter) => (v: string) => setF((o) => ({ ...o, [k]: v }));
  const { user } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const [peek, setPeek] = useState<{ id: number; listing: Listing; reasons?: string[] } | null>(null);
  const { data, isLoading, isFetching, error } = useQuery({ queryKey: ['listings', 'new', f, view === 'map' ? MAP_LIMIT : limit], queryFn: () => api.listings.new(f, view === 'map' ? MAP_LIMIT : limit), placeholderData: (p) => p });
  const { data: facets } = useQuery({ queryKey: ['listings', 'facets'], queryFn: () => api.listings.facets(), staleTime: 60_000 });
  const filtered = Object.entries(f).some(([k, v]) => (Array.isArray(v) ? v.length : v) && k !== 'q' && k !== 'known');
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
  const card = (l: Listing) => {
            const lockedByOther = !!l.taken_by && l.taken_by.id !== user?.id;
            const chips = (l.known_object || !!l.matches?.length) && (
              <>
                {l.known_object && (
                  <ChipButton tone="ok" onClick={() => setPeek({ id: l.known_object!.id, listing: l })}>
                    Уже в базе · #{l.known_object.id}{l.known_object.address ? ` · ${l.known_object.address}` : ''}
                  </ChipButton>
                )}
                {(l.matches || []).map((m) => (
                  <ChipButton key={m.property_id} tone="redsoft" onClick={() => setPeek({ id: m.property_id, listing: l, reasons: m.reasons })}>
                    Похож на #{m.property_id}{m.address ? ` · ${m.address}` : ''}
                  </ChipButton>
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
  };

  return (
    <>
      {peek && <PropertyModal id={peek.id} listing={peek.listing} reasons={peek.reasons} base={base} onClose={() => setPeek(null)} />}
      <Modal open={!!agentFor} onClose={() => setAgentFor(null)} title="Это агент">
        <form className="col gap-lg" onSubmit={(e) => { e.preventDefault(); markAgent.mutate(); }}>
          <Field label="Имя"><input className="input" value={agentC.owner_name} onChange={(e) => setAgentC((c) => ({ ...c, owner_name: e.target.value }))} autoFocus /></Field>
          <Field label="Телефон" hint="Номер попадёт в базу агентов: его объявления дальше будут помечаться автоматически">
            <input className="input" type="tel" inputMode="tel" onFocus={phoneFocus} onBlur={phoneBlur} value={agentC.owner_phone} onChange={(e) => { const v = phoneInput(e); setAgentC((c) => ({ ...c, owner_phone: v })); }} />
          </Field>
          <Button type="submit" block loading={markAgent.isPending} disabled={!agentC.owner_name.trim() && !agentC.owner_phone.trim()}>Сохранить</Button>
        </form>
      </Modal>
      <FeedFilters f={f} patch={(p) => setF((o) => ({ ...o, ...p }))} view={view} onView={setView} facets={facets} sourceLabel={sourceName}
        seg={{ value: f.flag || '', onChange: set('flag'), options: [
          { value: '', label: 'Все' }, { value: 'owner', label: 'Собственник' }, { value: 'agent_suspect', label: 'Агент?' }, { value: 'duplicate', label: 'Дубль' }] }}
        found={data ? `Найдено: ${data.total ?? itemsOf(data).length}` : ''} filtered={filtered} onReset={() => setF({ q: f.q, flag: '', known: f.known })}
        actions={(!!data?.known_total || f.known) && (
          <Button size="sm" variant={f.known ? 'primary' : 'secondary'} aria-pressed={!!f.known} onClick={() => set('known')(f.known ? '' : '1')} title={KNOWN_HINT}>
            Уже в базе ({data?.known_total ?? 0})
          </Button>
        )}>
        {f.known && <div className="muted" style={{ fontSize: 12 }}>{KNOWN_HINT}</div>}
      </FeedFilters>
      {isLoading ? <Loading /> : error ? <ErrorBox error={error} /> : items.length === 0 ? <Empty text="Новых объявлений нет" /> : view === 'map' ? (
        <ListingsMap items={items} card={card} />
      ) : (
        <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))' }}>
          {items.map(card)}
        </div>
      )}
      {view === 'list' && (data?.total ?? 0) > items.length && (
        <div className="row" style={{ justifyContent: 'center', marginTop: 16 }}>
          <Button variant="secondary" loading={isFetching} onClick={() => setLimit((n) => n + PAGE)}>Показать ещё ({(data?.total ?? 0) - items.length})</Button>
        </div>
      )}
    </>
  );
}

const KNOWN_HINT = 'Объявления, которые система узнала как уже существующий объект: они прикреплены к нему автоматически';
const propLink = (base: string, id: number) => (base === '/m' ? `/m/properties/${id}` : `/objects/${id}`);

/** The object in a modal (no page change): photos, parameters, owner if visible, «Открыть объект полностью».
 * listing: opened from a chip of «Новые объявления» — the ad and the object side by side (stacked on a phone);
 * reasons: a «Похож на #N» match, decided right here («Тот же объект» / «Другой»); without reasons: «Уже в базе». */
export function PropertyModal({ id, listing, reasons, base = '', onClose }: { id: number; listing?: Listing; reasons?: string[]; base?: string; onClose: () => void }) {
  const { isStaff } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const { data: p, isLoading, error } = useQuery({ queryKey: ['property', id], queryFn: () => api.properties.get(id) });
  const decide = useMutation({
    mutationFn: (same: boolean) => api.listings.decideMatch(listing!.id, id, same),
    onSuccess: (_, same) => { qc.invalidateQueries({ queryKey: ['listings'] }); toast(same ? `Объявление привязано к объекту #${id}` : 'Отмечено: другой объект'); onClose(); },
    onError: (e) => toast(errText(e), true),
  });
  const own = (p?.media || []).filter((m) => m.kind === 'photo').map((m) => m.url || m.path);
  const phone = p?.owner?.phone;
  const side = p && (
    <CompareSide label={`Объект #${p.id} в базе`} photos={own.length ? own : p.photos || []} address={p.address} rows={[
      ['Цена', money(p.price, p.currency || 'USD')], ['Район', districtLabel(p.district) || '—'], ['Спальни', bedroomsLabel(p.bedrooms) || '—'],
      ['Площадь', p.area ? `${p.area} м²` : '—'], ['Этаж', p.floor ? `${p.floor}${p.total_floors ? ` из ${p.total_floors}` : ''}` : '—'],
      ['Агент собственника', (p.owner?.agent || p.agent)?.name || PLATFORM],
      ...(p.owner ? [['Собственник', <>{p.owner.name || '—'}{phone && <> · <a href={`tel:${phone.replace(/[^+\d]/g, '')}`}>{formatPhone(phone)}</a> <CopyPhone phone={phone} /></>}</>] as [string, React.ReactNode]] : []),
    ]}>
      <div className="row row-wrap gap-sm">
        <PropertyStatusBadge status={p.status} />
        {p.is_exclusive && <Badge tone="solid">эксклюзив</Badge>}<ExclusiveConflictBadge p={p} />
      </div>
    </CompareSide>
  );
  return (
    <Modal open wide={!!listing} onClose={onClose} title={listing ? (reasons ? `Похож на объект #${id}?` : `Уже в базе · объект #${id}`) : p?.address || `Объект #${id}`}>
      {isLoading ? <Loading /> : error ? <ErrorBox error={error} /> : p && (
        <div className="col gap-lg">
          {listing && (
            <div className={`alert ${reasons ? 'alert-warn' : 'alert-ok'}`} style={{ fontSize: 13 }}>
              {reasons ? `Почему похож: ${reasons.join('; ') || 'параметры совпадают'}. Это та же квартира?` : 'Система узнала это объявление как объект из базы: оно прикреплено к нему автоматически.'}
            </div>
          )}
          {listing ? <div className="cmp"><ListingSide l={listing} />{side}</div> : side}
          {listing && reasons && isStaff && (
            <div className="row">
              <Button block loading={decide.isPending && decide.variables} onClick={() => decide.mutate(true)}>Тот же объект</Button>
              <Button variant="secondary" block loading={decide.isPending && !decide.variables} onClick={() => decide.mutate(false)}>Другой</Button>
            </div>
          )}
          <Link to={propLink(base, id)} className="btn btn-secondary">Открыть объект полностью</Link>
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
        {p.photo ? <img src={thumbUrl(p.photo)} alt="" loading="lazy" /> : null}
        <div className="badges"><PropertyStatusBadge status={p.status} />{p.is_exclusive && <Badge tone="solid">эксклюзив</Badge>}<ExclusiveConflictBadge p={p} /></div>
        {p.address && <span className="plaque">{p.address}</span>}
      </div>
      <div className="lcard-body">
        <span className="label">{[districtLabel(p.district), bedroomsLabel(p.bedrooms), p.area ? `${p.area} м²` : null].filter(Boolean).join(' · ') || '—'}</span>
        <div className="price">{money(p.price, p.currency || 'USD')}<span className="muted" style={{ fontSize: 12, fontWeight: 600 }}>/мес</span></div>
        <div className="row row-between gap-sm faint" style={{ fontSize: 12, marginTop: 'auto' }}>
          <span className="ellipsis">#{p.id} · {p.agent?.name || PLATFORM}</span>
          <span style={{ flex: 'none' }}>{ago(p.updated_at || p.created_at)}</span>
        </div>
      </div>
    </article>
  );
}

export function PropertyMiniCard({ p, to, onMove }: { p: Property; to: string; onMove?: () => void }) {
  const ph = p.photo || p.media?.find((m) => m.kind === 'photo')?.path || p.photos?.[0];
  return (
    <Link to={to} className="kcard" draggable onDragStart={(e) => e.dataTransfer.setData('text/property', String(p.id))}>
      <div className="row" style={{ alignItems: 'flex-start' }}>
        {ph && <img src={thumbUrl(ph)} alt="" loading="lazy" draggable={false} style={{ width: 52, height: 52, borderRadius: 8, objectFit: 'cover', flex: 'none' }} />}
        <div className="grow">
          <div className="kcard-title ellipsis">{p.address || `Объект #${p.id}`}</div>
          <div className="kcard-meta">{[districtLabel(p.district), bedroomsLabel(p.bedrooms), money(p.price, p.currency || 'USD')].filter(Boolean).join(' · ')}</div>
          <ExclusiveConflictBadge p={p} />
        </div>
      </div>
      <div className="row row-between mt" style={{ marginTop: 8 }}>
        <span className="faint" style={{ fontSize: 11, fontWeight: 600 }}>{p.agent?.name || PLATFORM}</span>
        {p.status === 'call_failed' && p.next_call_at ? <span className="badge badge-redsoft">звонок {fmtDateTime(p.next_call_at)}</span>
          : p.shoot_at && p.status === 'shoot_scheduled' ? <span className="badge">съёмка {fmtDateTime(p.shoot_at)}</span>
          : <span className="faint" style={{ fontSize: 11 }}>{ago(p.updated_at || p.created_at)}</span>}
      </div>
      {onMove && (
        <button type="button" className="btn btn-secondary btn-sm mt" style={{ marginTop: 8 }}
          onClick={(e) => { e.preventDefault(); e.stopPropagation(); onMove(); }}><ArrowRightLeft size={14} />Перенести</button>
      )}
    </Link>
  );
}

const CALL: PropertyStatus[] = ['taken', 'call_failed'];
const WHO: PropertyStatus[] = ['owner_confirmed', 'agent_marked'];

/** «В работе» (staff: every object, agent: his own); base="/m" hides empty columns like the Mini App lead board.
 * Drag a card to a column = the backend step for that move (services/properties.py); steps that need input (contact,
 * shoot date, photos, operator's review) open the object card; any other move is refused with a toast.
 * Agents: «Опубликован» is collapsed until «Показать» (it only grows and clutters the working columns). */
export function PropertyBoard({ base = '', statusBar }: { base?: string; statusBar?: boolean }) {
  const { data, isLoading, error } = useQuery({ queryKey: ['properties', 'board'], queryFn: api.properties.board, refetchInterval: 30_000 });
  const { isStaff } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const boardRef = useRef<HTMLDivElement>(null);
  const [over, setOver] = useState<PropertyStatus | null>(null);
  const [moving, setMoving] = useState<Property | null>(null);
  const [showPublished, setShowPublished] = useState(isStaff);
  const act = useMutation({
    mutationFn: async ({ p, to }: { p: Property; to: PropertyStatus }) => {
      if (to === 'call_failed') return api.properties.call(p.id, false);
      if (to === 'review') return api.properties.submit(p.id);
      if (CALL.includes(p.status)) await api.properties.call(p.id, true); // «Дозвонился», as CallStep does
      if (to === 'taken') return;
      return api.properties.owner(p.id, { is_owner: to === 'owner_confirmed', owner_name: p.owner?.name || undefined, owner_phone: p.owner?.phone || undefined });
    },
    onSuccess: (_, { to }) => { qc.invalidateQueries({ queryKey: ['properties'] }); qc.invalidateQueries({ queryKey: ['property'] }); toast(`Объект перенесён: ${PROPERTY_STATUS[to]}`); },
    onError: (e) => { toast(errText(e), true); qc.invalidateQueries({ queryKey: ['properties'] }); },
  });
  const hide = useMutation({
    mutationFn: (s: 'published' | 'rented') => api.properties.boardHide(s),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ['properties', 'board'] }); toast(`Убрано с доски: ${r.hidden}`); },
    onError: (e) => toast(errText(e), true),
  });
  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  const all = Object.values(data || {}).flat();
  const cols = PROPERTY_BOARD.filter((s) => !base || (data?.[s] || []).length);
  const drop = (to: PropertyStatus, id: number) => {
    setOver(null);
    const p = all.find((x) => x.id === id);
    if (!p || p.status === to) return;
    const from = p.status;
    const open = (msg: string) => { toast(msg); navigate(propLink(base, p.id)); };
    const contact = !!(p.owner?.name || p.owner?.phone);
    if (CALL.includes(from) && CALL.includes(to)) act.mutate({ p, to });
    else if (WHO.includes(to) && [...CALL, ...WHO].includes(from)) {
      if (contact) act.mutate({ p, to });
      else open('Укажите, кто ответил, и его контакт');
    } else if (to === 'shoot_scheduled' && from === 'owner_confirmed') open('Выберите дату съёмки');
    else if (to === 'media_uploaded' && ['owner_confirmed', 'shoot_scheduled', 'returned'].includes(from)) open('Загрузите фото и видео');
    else if (to === 'review' && ['shoot_scheduled', 'media_uploaded', 'returned'].includes(from)) {
      if (p.media_count) act.mutate({ p, to });
      else open('Сначала загрузите фото и видео');
    } else if (from === 'review' && (to === 'returned' || to === 'published')) {
      if (isStaff) open(to === 'published' ? 'Проверьте пост и опубликуйте' : 'Напишите агенту, что поправить');
      else toast('Объект на проверке: решение за оператором', true);
    } else if (to === 'rented') toast('«Сдан» ставится подтверждённой сделкой', true);
    else toast(`Из «${PROPERTY_STATUS[from]}» в «${PROPERTY_STATUS[to]}» перенести нельзя`, true);
  };
  return (
    <>
      <div className="kanban" ref={boardRef} {...dragBoardProps}>
        {cols.map((s) => {
          const items = data?.[s] || [];
          const collapsed = s === 'published' && !showPublished && items.length > 0;
          return (
            <div className={`kanban-col ${over === s ? 'drop-over' : ''}`} key={s} id={`col-${s}`} data-stage={s}
              onDragOver={(e) => { e.preventDefault(); setOver(s); }} onDragLeave={() => setOver(null)}
              onDrop={(e) => { e.preventDefault(); drop(s, Number(e.dataTransfer.getData('text/property'))); }}>
              <div className="kanban-col-head"><span className="label">{PROPERTY_STATUS[s]}</span><span className="tab-count">{items.length}</span></div>
              {(s === 'published' || s === 'rented') && items.length > 0 && (!collapsed) && (
                <Button size="sm" variant="ghost" block disabled={hide.isPending}
                  title="Объекты остаются в канале, боте и списках; карточка вернётся на доску при смене статуса"
                  onClick={() => { if (confirm(`Убрать с доски все карточки «${PROPERTY_STATUS[s]}» (${items.length})? Объекты останутся в канале, боте и списках.`)) hide.mutate(s); }}>
                  <Archive size={14} />Убрать с доски
                </Button>
              )}
              <div className="kanban-col-body">
                {collapsed ? <Button size="sm" variant="secondary" block onClick={() => setShowPublished(true)}>Показать</Button>
                  : items.length === 0 ? <div className="faint" style={{ fontSize: 12, padding: 6 }}>Пусто</div>
                  : items.map((p) => <PropertyMiniCard key={p.id} p={p} to={propLink(base, p.id)} onMove={base ? () => setMoving(p) : undefined} />)}
                {s === 'published' && showPublished && !isStaff && items.length > 0 && <Button size="sm" variant="ghost" block onClick={() => setShowPublished(false)}>Свернуть</Button>}
              </div>
            </div>
          );
        })}
      </div>
      {statusBar && <StatusBar board={boardRef} stages={cols} labels={PROPERTY_STATUS} count={(s) => data?.[s]?.length ?? 0} />}
      {/* touch (Mini App) has no HTML drag & drop: the same moves from a menu, like the lead board */}
      <Modal open={!!moving} onClose={() => setMoving(null)} title={`Перенести объект #${moving?.id}`}>
        <div className="col">
          {PROPERTY_BOARD.filter((st) => st !== moving?.status).map((st) => (
            <Button key={st} variant="secondary" block onClick={() => { const p = moving!; setMoving(null); drop(st, p.id); }}>{PROPERTY_STATUS[st]}</Button>
          ))}
        </div>
      </Modal>
    </>
  );
}

/** an agent's «Мои объекты»: status columns by default (statuses read at a glance), the table as the alternative */
export function MyProperties({ base = '' }: { base?: string }) {
  const [view, setView] = useState<'board' | 'list'>('board');
  return (
    <div className="col">
      <Seg value={view} onChange={setView} options={[{ value: 'board', label: 'Доска' }, { value: 'list', label: 'Списком' }]} />
      {view === 'board' ? <PropertyBoard base={base} /> : <PropertyList mine base={base} />}
    </div>
  );
}

const WORK = PROPERTY_BOARD.filter((s) => s !== 'published' && s !== 'rented');

/** «В работе»: the board, or the same objects as a table (Stanislav 11-11); published / rented live in «База объектов».
 * Board: the «СТАТУСЫ» panel (tap = scroll to that column); list: stage chips with counts filter it (again = all). */
export function WorkProperties() {
  const [view, setView] = useState<'board' | 'list'>('board');
  const [stage, setStage] = useState<PropertyStatus | ''>('');
  const { data } = useQuery({ queryKey: ['properties', 'board'], queryFn: api.properties.board, refetchInterval: 30_000 });
  return (
    <div className="col">
      <div className="row row-wrap">
        <Seg value={view} onChange={setView} options={[{ value: 'board', label: 'Доска' }, { value: 'list', label: 'Списком' }]} />
        {/* the board has the «СТАТУСЫ» panel instead of the chips (Stanislav 11-13) */}
        {view === 'list' && <Seg value={stage} onChange={(s) => setStage(stage === s ? '' : s)}
          options={WORK.map((s) => ({ value: s, label: <>{PROPERTY_STATUS[s]} <span className="tab-count">{data?.[s]?.length ?? 0}</span></> }))} />}
      </div>
      {view === 'board' ? <PropertyBoard statusBar /> : <PropertyList key="work" status={stage || WORK.join(',')} />}
    </div>
  );
}

type PS = keyof typeof PROPERTY_STATUS;
/** «База объектов»: shot and verified by the operator (backend properties.BASE_STATUSES + published_at, ?base=1) */
const BASE: PS[] = ['published', 'rented'];
/** PropertyList's URL filter keys (`status` is kept apart: a dashboard link may bring it) */
const FILTER_KEYS = ['q', 'agent', 'page', 'source', 'district', 'metro', 'bedrooms', 'price_min', 'price_max', 'reason', 'view'];

/** mine: the agent's own objects; archived: «Архив»; only: one fixed status (operator queue «На публикацию» = review);
 * otherwise «База объектов» — published / rented objects: staff see all, an agent only his own (backend properties.query). */
export function PropertyList({ mine, archived, status: only, agentId, base = '' }: { mine?: boolean; archived?: boolean; status?: string; agentId?: number; base?: string }) {
  const { isStaff } = useAuth();
  const scope = archived ? 'archive' : only ? `only:${only}` : mine ? 'mine' : 'all';
  // search / filters / page live in the URL: «назад» from an object lands on the same search and page (David 11-12).
  // `ls` says which list they belong to, so a sibling list (cabinet groups, work stages) starts clean.
  const [params, setParams] = useSearchParams();
  const own = params.get('ls') === scope;
  const asked = params.get('status') as PS | null;  // «База объектов» opened from a dashboard number: ?tab=all&status=rented
  const get = (k: string) => (own ? params.get(k) || '' : '');
  const f = { q: get('q'), source: own ? params.getAll('source') : [], district: own ? params.getAll('district') : [], metro: get('metro'),
    bedrooms: get('bedrooms'), price_min: get('price_min'), price_max: get('price_max') };
  const status = !archived && !only && asked && (own || (scope === 'all' && BASE.includes(asked))) ? asked as string : '';
  const agent = get('agent'), reason = get('reason'), page = Number(get('page')) || 0;
  const view = get('view') === 'map' && !only ? 'map' : 'list';
  const set = (kv: Record<string, string | number | string[] | undefined>) => setParams((cur) => {
    const n = new URLSearchParams(cur);
    if (!own) [...FILTER_KEYS, 'status'].forEach((k) => n.delete(k));
    n.set('ls', scope);
    Object.entries(kv).forEach(([k, v]) => { n.delete(k); (Array.isArray(v) ? v : v ? [String(v)] : []).forEach((x) => n.append(k, x)); });
    return n;
  }, { replace: true });
  const setPage = (n: number) => set({ page: n });
  const fixedAgent = agentId !== undefined;  // the cabinet: one agent's objects, no agent select
  // staff pick an agent instead of typing his #tag; agents only ever get their own objects (backend properties.query)
  const agents = useQuery({ queryKey: ['users', 'agents-all'], queryFn: () => api.users.list({ role: FIELD_FILTER }), enabled: isStaff && !mine && !fixedAgent });
  // the tab's scope (what the segment narrows, what the facets count over); «В работе» in the segment = every work stage
  const scoped: PropertyScope = { mine: mine ? 1 : undefined, base: scope === 'all' ? 1 : undefined, agent_id: agentId ?? (agent ? Number(agent) : undefined),
    status: archived ? 'archived' : only || (mine ? '' : BASE.join(',')) };
  const statusQ = status === 'work' ? WORK.join(',') : status || scoped.status;
  const { data, isLoading, isFetching, error } = useQuery({
    queryKey: ['properties', 'list', scope, f, statusQ, scoped.agent_id, reason, view === 'map' ? -1 : page],
    queryFn: () => api.properties.list({ ...scoped, ...f, status: statusQ, archive_reason: reason || undefined,
      limit: view === 'map' ? MAP_LIMIT : PAGE, offset: view === 'map' ? 0 : page * PAGE }),
    placeholderData: (p, prev) => (prev?.queryKey[2] === scope ? p : undefined), // keep the list while a filter reloads, never across tabs
  });
  const facets = useQuery({ queryKey: ['properties', 'facets', scope, scoped], queryFn: () => api.properties.facets(scoped), staleTime: 60_000 });
  const counts = useQuery({ queryKey: ['properties', 'counts'], queryFn: api.properties.counts, enabled: isStaff && (scope === 'all' || scope === 'archive') }); // whole-base totals: staff only
  const total = data?.total ?? 0;
  const pages = view === 'map' ? 1 : Math.max(1, Math.ceil(total / PAGE));
  const items = itemsOf(data);
  const navigate = useNavigate();
  const link = (id: number) => propLink(base, id);
  const [peek, setPeek] = useState<number | null>(null);
  const filtered = !!(f.source.length || f.district.length || f.metro || f.bedrooms || f.price_min || f.price_max || status || (agent && !fixedAgent) || reason);
  const segOpts = scope === 'all' ? ['', ...BASE] : scope === 'mine' ? ['', 'work', 'published', 'rented'] : null;
  return (
    <>
      <FeedFilters f={f} patch={(p) => set({ ...p, page: 0 })} view={only ? undefined : view} onView={(v) => set({ view: v === 'map' ? v : '', page: 0 })}
        facets={facets.data} sourceLabel={(v) => SOURCE_LABEL[v] || v} metroLabel={metroLabel} placeholder="Адрес, агент, #тег"
        seg={segOpts ? { value: status, onChange: (v) => set({ status: v, page: 0 }), options: segOpts.map((v) => ({ value: v, label: v === 'work' ? 'В работе' : v ? PROPERTY_STATUS[v as PS] : 'Все' })) } : undefined}
        filtered={filtered} onReset={() => set({ ...Object.fromEntries(FILTER_KEYS.filter((k) => k !== 'q' && k !== 'view' && !(fixedAgent && k === 'agent')).map((k) => [k, ''])), status: '' })}
        found={data ? `Найдено: ${total.toLocaleString('ru')}${view === 'list' && total > PAGE ? ` · показаны ${(page * PAGE + 1).toLocaleString('ru')}–${Math.min(total, (page + 1) * PAGE).toLocaleString('ru')}` : ''}` : ''}
        boxes={<>
          {isStaff && !mine && !fixedAgent && (
            <div className="fbox"><span className="flabel">Агент</span>
              <select className="select" value={agent} aria-label="Агент" onChange={(e) => set({ agent: e.target.value, page: 0 })}>
                <option value="">Все</option>
                <option value="0">{PLATFORM} (без агента)</option>
                {itemsOf(agents.data).slice().sort((a, b) => Number(b.is_active !== false) - Number(a.is_active !== false) || (a.name || '').localeCompare(b.name || '')).map((u) => (
                  <option key={u.id} value={u.id}>{u.name}{u.agent_tag ? ` · #${u.agent_tag}` : ''}{u.is_active === false ? ' (не работает)' : ''}</option>
                ))}
              </select></div>
          )}
          {archived && (
            <div className="fbox"><span className="flabel">Причина</span>
              <Select aria-label="Причина" value={reason} onChange={(e) => set({ reason: e.target.value, page: 0 })} placeholder="Любая"
                options={Object.entries(ARCHIVE_REASON).map(([value, label]) => ({ value, label }))} /></div>
          )}
        </>} />
      {counts.data && (
        <div className="muted" style={{ fontSize: 13, marginBottom: 6 }}>
          Всего объектов: <b>{Object.values(counts.data).reduce((a, b) => a + b, 0).toLocaleString('ru')}</b>
          {' · '}{(['published', 'rented', 'archived'] as PS[]).map((k) => `${PROPERTY_STATUS[k]}: ${(counts.data[k] || 0).toLocaleString('ru')}`).join(' · ')}
        </div>
      )}
      {peek !== null && <PropertyModal id={peek} base={base} onClose={() => setPeek(null)} />}
      {isLoading ? <Loading /> : error ? <ErrorBox error={error} /> : items.length === 0 ? <Empty text={filtered ? 'Ничего не найдено' : archived ? 'Архив пуст' : only === 'review' ? 'Очередь пуста: всё опубликовано' : 'Объектов нет'} /> : view === 'map' ? (
        <ListingsMap items={items} card={(p) => <PropertyCard key={p.id} p={p} onOpen={() => setPeek(p.id)} />} />
      ) : scope === 'all' ? (
        <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))' }}>
          {items.map((p) => <PropertyCard key={p.id} p={p} onOpen={() => setPeek(p.id)} />)}
        </div>
      ) : (
        <>
          <div className="card hide-mobile">
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>#</th><th>Адрес</th><th>Цена</th><th>Агент</th><th>Статус</th>{(archived || only === 'archived') && <th>Причина</th>}<th>Обновлён</th></tr></thead>
                <tbody>
                  {items.map((p) => (
                    <tr key={p.id} className="clickable" onClick={() => navigate(link(p.id))}>
                      <td className="muted num">{p.id}</td>
                      <td><Link to={link(p.id)} style={{ fontWeight: 700, color: 'inherit' }}>{p.address || '—'}</Link><div><Place district={p.district} /></div></td>
                      <td className="num" style={{ fontWeight: 700 }}>{money(p.price, p.currency || 'USD')}</td>
                      <td>{p.agent?.name || PLATFORM}</td>
                      <td><div className="row row-wrap gap-sm"><PropertyStatusBadge status={p.status} /><ExclusiveConflictBadge p={p} /></div></td>
                      {(archived || only === 'archived') && <td>{ARCHIVE_REASON[p.archive_reason || ''] || '—'}{p.archive_comment && <div className="muted" style={{ fontSize: 12 }}>{p.archive_comment}</div>}</td>}
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
      {pages > 1 && (
        <div className="row gap-sm" style={{ justifyContent: 'center', marginTop: 16 }}>
          <Button variant="secondary" disabled={page === 0} onClick={() => { setPage(0); pageScroller().scrollTo(0, 0); }}>« Первая</Button>
          <Button variant="secondary" disabled={page === 0} onClick={() => { setPage(page - 1); pageScroller().scrollTo(0, 0); }}>‹ Назад</Button>
          <span className="muted" style={{ alignSelf: 'center', padding: '0 8px' }}>Стр. {page + 1} из {pages}{isFetching ? ' …' : ''}</span>
          <Button variant="secondary" disabled={page + 1 >= pages} onClick={() => { setPage(page + 1); pageScroller().scrollTo(0, 0); }}>Вперёд ›</Button>
          <Button variant="secondary" disabled={page + 1 >= pages} onClick={() => { setPage(pages - 1); pageScroller().scrollTo(0, 0); }}>Последняя »</Button>
        </div>
      )}
    </>
  );
}
