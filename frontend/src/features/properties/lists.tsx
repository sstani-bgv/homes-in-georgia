// Funnel 1 lists: new listings (C1), kanban (by status), my objects. Shared by desktop and /m.
import { useConfirm } from '../../components/ActionDialog';
import { createContext, useContext, useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Archive, ArrowRightLeft, DollarSign, ExternalLink, List, Map as MapIcon, Search, Users } from 'lucide-react';
import { api, type ListingFilter, type PropertyScope } from '../../api';
import { ApiError, itemsOf, thumbUrl } from '../../api/client';
import type { Listing, Property, PropertyStatus, SimilarItem } from '../../api/types';
import { FIELD_FILTER } from '../../api/types';
import { StatusBar, dragBoardProps } from '../leads/LeadBoard';
import { pageScroller, useAuth } from '../../auth/AuthContext';
import { ChipButton, CompareSide, CopyPhone, ExclusiveConflictBadge, ListingCard, ListingSide, PropertyStatusBadge, Place } from '../../components/domain';
import { errText, useToast } from '../../components/toast';
import { TakeCheck } from '../../components/Similar';
import { Badge, Button, Empty, ErrorBox, Field, Loading, Modal, MultiSelect, Select, Seg } from '../../components/ui';
import { ListingsMap } from './ListingsMap';
import { Resume, metroLabel } from './PropertyDetail';
import { usePageState } from '../../layout/back';
import { ARCHIVE_REASON, PLATFORM, PROPERTY_BOARD, PROPERTY_STATUS, SOURCE_LABEL, ago, bedroomsLabel, demandLabel, districtLabel, fmtDate, fmtDateTime, formatPhone, money, phoneBlur, phoneFocus, phoneInput } from '../../lib/format';
import { MorePhones } from '../../components/PhonesField';
import { LOCALE, lx, t } from '../../lib/i18n';

/** "fb:Some group" -> "Facebook · Some group"; site sources stay as they are. */
const PREFIX_LABEL: Record<string, string> = { fb: 'Facebook', ig: 'Instagram', th: 'Threads', tg: 'Telegram' };
/** a Facebook group is keyed by its numeric id: its name from «Источники» (facet label), else «группа …1234» */
const sourceName = (s: string, label?: string) => {
  const [k, ...rest] = s.split(':'), key = rest.join(':');
  if (!PREFIX_LABEL[k] || !rest.length) return SOURCE_LABEL[s] || s;
  return `${PREFIX_LABEL[k]} · ${label || (/^\d{8,}$/.test(key) ? t('группа …{id}', { id: key.slice(-4) }) : key)}`;
};
// «Тип» of the feed (Irakli 11-17): the parsers' details.property (backend new_listings prop_type)
const PROP_TYPES = [{ value: 'apartment', label: t('Квартира') }, { value: 'house', label: t('Частный дом') }, { value: 'commercial', label: t('Коммерция') }];
const BEDS = [{ value: '0', label: t('Студия') }, { value: '1', label: '1' }, { value: '2', label: '2' }, { value: '3', label: '3' }, { value: '4', label: '4+' }];

type Facet = { value: string; count: number; label?: string }[];
/** the filter fields the feed and the base share (ListingFilter, PropertyList's URL) */
type CommonFilter = { q?: string; source?: string[]; district?: string[]; metro?: string; bedrooms?: string; price_min?: string; price_max?: string };

/** The mock-up of 11-13: search + list/map, a full-width segment, a 2-column grid of labelled filters (+ `boxes`),
 * bedrooms, «Найдено · Сбросить». «Новые объявления» and the base tabs (Stanislav 11-13); children go under it. */
function FeedFilters({ f, patch, view, onView, seg, facets, sourceLabel, metroLabel = (v) => v, boxes, found, filtered, onReset, actions, placeholder = t('Район, адрес'), children }: {
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
          { value: 'list', label: <List size={18} aria-label={t('Список')} /> }, { value: 'map', label: <MapIcon size={18} aria-label={t('Карта')} /> }]} />}
      </div>
      {seg && <div className="seg-full"><Seg value={seg.value} onChange={seg.onChange} options={seg.options} /></div>}
      <div className="filter-grid">
        <div className="fbox"><span className="flabel">{t('Источник')}</span>
          <MultiSelect label={t('Источник')} value={f.source || []} onChange={(v) => patch({ source: v })} placeholder={t('Все')}
            options={(facets?.sources || []).map((x) => ({ value: x.value, label: sourceLabel(x.value, x.label), count: x.count }))} /></div>
        <div className="fbox"><span className="flabel">{t('Район')}</span>
          <MultiSelect label={t('Район')} value={f.district || []} onChange={(v) => patch({ district: v })} placeholder={t('Все')} many={lx<[string, string, string]>(['район', 'района', 'районов'], ['უბანი', 'უბანი', 'უბანი'], ['district', 'districts', 'districts'])}
            options={(facets?.districts || []).map((x) => ({ value: x.value, label: districtLabel(x.value), count: x.count }))} /></div>
        <div className="fbox"><span className="flabel">{t('Метро')}</span>
          <Select aria-label={t('Метро')} value={f.metro || ''} onChange={(e) => patch({ metro: e.target.value })} placeholder={t('Любое')}
            options={(facets?.metros || []).map((x) => ({ value: x.value, label: `${metroLabel(x.value)} (${x.count})` }))} /></div>
        <div className="fbox fbox-price"><span className="flabel">{t('Цена, $')}</span>
          <input className="input" type="number" inputMode="numeric" min={0} placeholder={t('от')} aria-label={t('Цена от')} value={f.price_min || ''} onChange={(e) => patch({ price_min: e.target.value })} />
          <span className="faint">—</span>
          <input className="input" type="number" inputMode="numeric" min={0} placeholder={t('до')} aria-label={t('Цена до')} value={f.price_max || ''} onChange={(e) => patch({ price_max: e.target.value })} />
          <DollarSign size={16} className="faint" /></div>
        {boxes}
      </div>
      <div className="beds-row" role="group" aria-label={t('Спальни')}>
        {BEDS.map((b) => <button key={b.value} type="button" className={beds.has(b.value) ? 'active' : ''} aria-pressed={beds.has(b.value)} onClick={() => toggleBed(b.value)}>{b.label}</button>)}
      </div>
      <div className="row row-between row-wrap" style={{ gap: 8 }}>
        <span className="faint" style={{ fontSize: 13 }}>{found}</span>
        <div className="row gap-sm">
          {actions}
          {filtered && <button type="button" className="link-btn link-red" onClick={onReset}>{t('Сбросить фильтры')}</button>}
        </div>
      </div>
      {children}
    </div>
  );
}

const PAGE = 50;
const FEED_PAGE = 15;  // David 11-25: the feed in pages of 10–15 — easier to find your place than one long scroll
const MAP_LIMIT = 500;  // ponytail: the map loads the first 500 filtered ads; a bbox query when the feed grows past it

/** «Объекты» → Аренда / Продажа (Stanislav 11-15): every list, board and count of the page shows one deal. Outside
 * «Объекты» (cabinet, dashboard) there is no provider: both deals. */
export type Deal = 'rent' | 'sale';
export const DealContext = createContext<Deal | undefined>(undefined);
export const useDeal = () => useContext(DealContext);
const isSale = (p: Property) => p.post_fields?.deal === 'Sale';
const priceOf = (p: Property) => (isSale(p) ? money(p.post_fields?.sale_price ?? p.price, p.currency || 'USD') : money(p.price, p.currency || 'USD'));

export function NewListings({ base = '' }: { base?: string }) {
  if (useDeal() === 'sale') return <Empty text={t('Объявления о продаже пока не собираем: объект продажи добавляется кнопкой «Объект вручную»')} />;
  return <RentListings base={base} />;
}

/** «Объекты → База → Конкуренты»: every ad of the sources marked «Конкурент» (Управление → Источники), read only:
 * not our inventory, so no «Беру в работу» (never in the work board, never published, not on the site). */
export function CompetitorListings({ base = '' }: { base?: string }) {
  return <RentListings base={base} competitor />;
}

function RentListings({ base = '', competitor = false }: { base?: string; competitor?: boolean }) {
  const confirm = useConfirm();
  const pk = competitor ? 'rival' : 'feed';
  const [f, setF0] = usePageState<ListingFilter>(`${pk}-filter`, { q: '', flag: '' });
  const [page, setPage] = usePageState(`${pk}-page`, 0);
  const [view, setView] = usePageState<'list' | 'map'>(`${pk}-view`, 'list');
  // the map's pin opens the full ad window (?ad=<id>, as a card does), not a small preview (11-13)
  const [params, setParams] = useSearchParams();
  const openAd = (id: string) => setParams((p) => { const n = new URLSearchParams(p); n.set('ad', id); return n; }, { replace: true });
  const setF = (v: ListingFilter | ((o: ListingFilter) => ListingFilter)) => { setF0(v); setPage(0); };
  const set = (k: keyof ListingFilter) => (v: string) => setF((o) => ({ ...o, [k]: v }));
  const { user } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const [peek, setPeek] = useState<{ id: number; listing: Listing; reasons?: string[] } | null>(null);
  const q = competitor ? { ...f, flag: f.flag === 'ours' || f.flag === 'not_ours' ? f.flag : '', known: '', competitor: '1' } : f;
  const { data, isLoading, isFetching, error } = useQuery({ queryKey: ['listings', 'new', q, view === 'map' ? -1 : page], queryFn: () => (view === 'map' ? api.listings.new(q, MAP_LIMIT) : api.listings.new(q, FEED_PAGE, page * FEED_PAGE)), placeholderData: (p) => p });
  // ads taken / marked from the last page can empty it: step back to the last one that has cards
  useEffect(() => { const last = Math.max(0, Math.ceil((data?.total ?? 0) / FEED_PAGE) - 1); if (data && view === 'list' && page > last) setPage(last); }, [data?.total]); // eslint-disable-line react-hooks/exhaustive-deps
  const { data: facets } = useQuery({ queryKey: ['listings', 'facets', competitor], queryFn: () => api.listings.facets(competitor), staleTime: 60_000 });
  const filtered = Object.entries(f).some(([k, v]) => (Array.isArray(v) ? v.length : v) && k !== 'q' && k !== 'known');
  // possible_duplicate (Stanislav 11-17): the ad may already be our object — the agent looks first (TakeCheck)
  const [dupCheck, setDupCheck] = useState<{ id: string; items: SimilarItem[] } | null>(null);
  const take = useMutation({
    mutationFn: (v: string | { id: string; force: true }) => (typeof v === 'string' ? api.properties.take(v) : api.properties.take(v.id, true)),
    onSuccess: (p) => {
      setDupCheck(null);
      qc.invalidateQueries({ queryKey: ['listings'] });
      qc.invalidateQueries({ queryKey: ['properties'] });
      toast(t('Объект закреплён за вами'));
      navigate(base === '/m' ? `/m/properties/${p.id}` : `/objects/${p.id}`);
    },
    onError: (e, v) => {
      if (e instanceof ApiError && e.code === 'possible_duplicate' && typeof v === 'string') {
        setDupCheck({ id: v, items: (e.data?.items as SimilarItem[]) || [] });
        return;
      }
      toast(errText(e), true); qc.invalidateQueries({ queryKey: ['listings'] });
    },
  });
  const [agentFor, setAgentFor] = useState<string | null>(null);
  const [agentC, setAgentC] = useState({ owner_name: '', owner_phone: '' });
  const markAgent = useMutation({
    mutationFn: () => api.listings.agent(agentFor!, agentC),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['listings'] });
      qc.invalidateQueries({ queryKey: ['properties'] });
      setAgentFor(null); setAgentC({ owner_name: '', owner_phone: '' });
      toast(t('Отмечено как агент: объявление убрано из ленты, в работу не взято'));
    },
    onError: (e) => toast(errText(e), true),
  });
  const dismiss = useMutation({
    mutationFn: (id: string) => api.listings.dismiss(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['listings'] }); toast(t('Убрано из ленты как неактуальное')); },
    onError: (e) => toast(errText(e), true),
  });
  const items = itemsOf(data);
  const card = (l: Listing) => {
            const lockedByOther = !!l.taken_by && l.taken_by.id !== user?.id;
            const chips = (l.known_object || !!l.matches?.length || !!l.at_competitor?.length) && (
              <>
                {(l.at_competitor || []).map((s) => <Badge key={s} tone="warn" title={t('Это объявление уже у конкурента')}>{t('У конкурента · {name}', { name: sourceName(s) })}</Badge>)}
                {l.known_object && (
                  <ChipButton tone="ok" onClick={() => setPeek({ id: l.known_object!.id, listing: l })}>
                    {t('Уже в базе · #{id}', { id: l.known_object.id })}{l.known_object.address ? ` · ${l.known_object.address}` : ''}
                  </ChipButton>
                )}
                {(l.matches || []).map((m) => (
                  <ChipButton key={m.property_id} tone="redsoft" onClick={() => setPeek({ id: m.property_id, listing: l, reasons: m.reasons })}>
                    {t('Похож на #{id}', { id: m.property_id })}{m.address ? ` · ${m.address}` : ''}
                  </ChipButton>
                ))}
              </>
            );
            return (
              <ListingCard key={l.id} listing={competitor ? { ...l, ai_flags: null } : l} locked={lockedByOther ? t('Взял {name}', { name: l.taken_by?.name ?? '' }) : undefined} chips={chips || undefined}
                footer={competitor ? (l.url ? (
                  <a className="btn btn-secondary btn-block" href={l.url} target="_blank" rel="noreferrer"><ExternalLink size={16} />{t('Открыть источник')}</a>
                ) : undefined) :
                  <div className="col gap-sm">
                    {!!l.demand && <div className="alert alert-info row gap-sm" style={{ fontSize: 12, fontWeight: 700 }}><Users size={14} />{demandLabel(l.demand)}</div>}
                    <div className="row">
                      <Button block disabled={lockedByOther} loading={take.isPending && take.variables === l.id} onClick={() => take.mutate(l.id)}>{t('Беру в работу')}</Button>
                      {l.url && <a className="btn btn-secondary icon-btn" href={l.url} target="_blank" rel="noreferrer" title={t('Открыть источник')}><ExternalLink size={16} /></a>}
                    </div>
                    <div className="row">
                      <Button size="sm" variant="secondary" block disabled={lockedByOther} onClick={() => setAgentFor(l.id)}>{t('Агент')}</Button>
                      <Button size="sm" variant="secondary" block disabled={lockedByOther} loading={dismiss.isPending && dismiss.variables === l.id}
                        onClick={async () => { if (await confirm(t('Объявление удалено или неактуально? Оно уйдёт из ленты.'))) dismiss.mutate(l.id); }}>{t('Неактуально')}</Button>
                    </div>
                  </div>
                } />
            );
  };

  return (
    <>
      {peek && <PropertyModal id={peek.id} listing={peek.listing} reasons={peek.reasons} base={base} onClose={() => setPeek(null)} />}
      <Modal open={!!dupCheck} wide onClose={() => setDupCheck(null)} title={t('Проверка на дубль')}>
        {dupCheck && <TakeCheck listingId={dupCheck.id} items={dupCheck.items} taking={take.isPending}
          onTake={() => take.mutate({ id: dupCheck.id, force: true })}
          onClose={() => { setDupCheck(null); qc.invalidateQueries({ queryKey: ['listings'] }); }} />}
      </Modal>
      <Modal open={!!agentFor} onClose={() => setAgentFor(null)} title={t('Это агент')}>
        <form className="col gap-lg" onSubmit={(e) => { e.preventDefault(); markAgent.mutate(); }}>
          <div className="alert alert-info" style={{ fontSize: 13 }}>{t('Объявление уйдёт из ленты, в работу вы его не берёте. Номер из объявления попадёт в базу агентов.')}</div>
          <Field label={t('Имя')}><input className="input" value={agentC.owner_name} onChange={(e) => setAgentC((c) => ({ ...c, owner_name: e.target.value }))} autoFocus /></Field>
          <Field label={t('Телефон')} hint={t('Необязательно, если номер есть в объявлении. Его объявления дальше будут помечаться автоматически')}>
            <input className="input" type="tel" inputMode="tel" onFocus={phoneFocus} onBlur={phoneBlur} value={agentC.owner_phone} onChange={(e) => { const v = phoneInput(e); setAgentC((c) => ({ ...c, owner_phone: v })); }} />
          </Field>
          <Button type="submit" block loading={markAgent.isPending}>{t('Это агент, не беру')}</Button>
        </form>
      </Modal>
      <FeedFilters f={f} patch={(p) => setF((o) => ({ ...o, ...p }))} view={view} onView={setView} facets={facets} sourceLabel={sourceName}
        seg={competitor ? { value: q.flag || '', onChange: set('flag'), options: [
          { value: '', label: t('Все') }, { value: 'ours', label: t('Есть у нас') }, { value: 'not_ours', label: t('Нет у нас') }] } : { value: f.flag || '', onChange: set('flag'), options: [
          { value: '', label: t('Все') }, { value: 'owner', label: t('Собственник') }, { value: 'agent_suspect', label: t('Агент?') },
          // the card's red label «В базе / В архиве по этому адресу» (11-13; the old «Дубль» = agency channel matched nothing)
          { value: 'in_base', label: t('В базе') }, { value: 'in_archive', label: t('В архиве') }] }}
        boxes={<div className="fbox"><span className="flabel">{t('Тип')}</span>
          <Select aria-label={t('Тип')} value={f.prop_type || ''} onChange={(e) => set('prop_type')(e.target.value)} placeholder={t('Любой')} options={PROP_TYPES} /></div>}
        found={data ? t('Найдено: {n}', { n: data.total ?? itemsOf(data).length }) : ''} filtered={filtered} onReset={() => setF({ q: f.q, flag: '', known: f.known })}
        actions={!competitor && (!!data?.known_total || f.known) && (
          <Button size="sm" variant={f.known ? 'primary' : 'secondary'} aria-pressed={!!f.known} onClick={() => set('known')(f.known ? '' : '1')} title={KNOWN_HINT}>
            {t('Уже в базе ({n})', { n: data?.known_total ?? 0 })}
          </Button>
        )}>
        {competitor ? <div className="muted" style={{ fontSize: 12 }}>{t('Объявления источников, отмеченных «Конкурент» в Управление → Источники. Это не наша база: в работу их не берут.')}</div>
          : f.known && <div className="muted" style={{ fontSize: 12 }}>{KNOWN_HINT}</div>}
      </FeedFilters>
      {isLoading ? <Loading /> : error ? <ErrorBox error={error} /> : items.length === 0 ? <Empty text={competitor ? t('Объявлений конкурентов нет: отметьте источник «Конкурент» в Управление → Источники') : t('Новых объявлений нет')} /> : view === 'map' ? (
        <>
          <ListingsMap items={items} card={card} onPick={(l) => openAd(l.id)} />
          {/* the picked ad's card stays hidden: it renders its full window (with its buttons) from ?ad= */}
          {items.filter((l) => l.id === params.get('ad')).map((l) => <div key={l.id} hidden>{card(l)}</div>)}
        </>
      ) : (
        <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))' }}>
          {items.map(card)}
        </div>
      )}
      {view === 'list' && (data?.total ?? 0) > FEED_PAGE && (
        <Pager page={page} pages={Math.ceil((data?.total ?? 0) / FEED_PAGE)} busy={isFetching} go={(n) => { setPage(n); pageScroller().scrollTo(0, 0); }} />
      )}
    </>
  );
}

const KNOWN_HINT = t('Объявления, которые система узнала как уже существующий объект: они прикреплены к нему автоматически');
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
    onSuccess: (_, same) => { qc.invalidateQueries({ queryKey: ['listings'] }); toast(same ? t('Объявление привязано к объекту #{id}', { id }) : t('Отмечено: другой объект')); onClose(); },
    onError: (e) => toast(errText(e), true),
  });
  const own = (p?.media || []).filter((m) => m.kind === 'photo').map((m) => m.url || m.path);
  const phone = p?.owner?.phone;
  const side = p && (
    <CompareSide label={t('Объект #{id} в базе', { id: p.id })} photos={own.length ? own : p.photos || []} address={p.address} rows={[
      [t('Цена'), money(p.price, p.currency || 'USD')], [t('Район'), districtLabel(p.district) || '—'], [t('Спальни'), bedroomsLabel(p.bedrooms) || '—'],
      [t('Площадь'), p.area ? `${p.area} ${t('м²')}` : '—'], [t('Этаж'), p.floor ? `${p.floor}${p.total_floors ? t(' из {n}', { n: p.total_floors }) : ''}` : '—'],
      [t('Агент собственника'), (p.owner?.agent || p.agent)?.name || PLATFORM],
      ...(p.owner ? [[t('Собственник'), <>{p.owner.name || '—'}{phone && <> · <a href={`tel:${phone.replace(/[^+\d]/g, '')}`}>{formatPhone(phone)}</a> <CopyPhone phone={phone} /></>}<MorePhones phones={p.owner.phones} /></>] as [string, React.ReactNode]] : []),
    ]}>
      <div className="row row-wrap gap-sm">
        <PropertyStatusBadge status={p.status} />
        {p.is_exclusive && <Badge tone="solid">{t('эксклюзив')}</Badge>}<ExclusiveConflictBadge p={p} />
      </div>
    </CompareSide>
  );
  return (
    <Modal open wide={!!listing} onClose={onClose} title={listing ? (reasons ? t('Похож на объект #{id}?', { id }) : t('Уже в базе · объект #{id}', { id })) : p?.address || t('Объект #{id}', { id })}>
      {isLoading ? <Loading /> : error ? <ErrorBox error={error} /> : p && (
        <div className="col gap-lg">
          {listing && (
            <div className={`alert ${reasons ? 'alert-warn' : 'alert-ok'}`} style={{ fontSize: 13 }}>
              {reasons ? t('Почему похож: {why}. Это та же квартира?', { why: reasons.join('; ') || t('параметры совпадают') }) : t('Система узнала это объявление как объект из базы: оно прикреплено к нему автоматически.')}
            </div>
          )}
          {listing ? <div className="cmp"><ListingSide l={listing} />{side}</div> : side}
          {listing && reasons && isStaff && (
            <div className="row">
              <Button block loading={decide.isPending && decide.variables} onClick={() => decide.mutate(true)}>{t('Тот же объект')}</Button>
              <Button variant="secondary" block loading={decide.isPending && !decide.variables} onClick={() => decide.mutate(false)}>{t('Другой')}</Button>
            </div>
          )}
          {p.post_url && <a className="btn btn-secondary" href={p.post_url} target="_blank" rel="noreferrer"><ExternalLink size={16} />{t('Пост в нашем канале')}</a>}
          <Link to={propLink(base, id)} className="btn btn-secondary">{t('Открыть объект полностью')}</Link>
        </div>
      )}
    </Modal>
  );
}

/** «База объектов»: the same card as «Новые объявления»; a tap opens the full object (Stanislav 11-13: no modal step). */
function PropertyCard({ p, to, note }: { p: Property; to: string; note?: React.ReactNode }) {
  return (
    <Link to={to} className="card lcard" style={{ color: 'inherit', textDecoration: 'none' }}>
      <div className="lcard-photo">
        {p.photo ? <img src={thumbUrl(p.photo)} alt="" loading="lazy" /> : null}
        <div className="badges"><PropertyStatusBadge status={p.status} />{p.is_exclusive && <Badge tone="solid">{t('эксклюзив')}</Badge>}<ExclusiveConflictBadge p={p} /></div>
        {p.address && <span className="plaque">{p.address}</span>}
      </div>
      <div className="lcard-body">
        <span className="label">{[districtLabel(p.district), bedroomsLabel(p.bedrooms), p.area ? `${p.area} ${t('м²')}` : null].filter(Boolean).join(' · ') || '—'}</span>
        <div className="price">{priceOf(p)}{!isSale(p) && <span className="muted" style={{ fontSize: 12, fontWeight: 600 }}>{t('/мес')}</span>}</div>
        {note}
        <div className="row row-between gap-sm faint" style={{ fontSize: 12, marginTop: 'auto' }}>
          <span className="ellipsis">#{p.id} · {p.agent?.name || PLATFORM}</span>
          <span style={{ flex: 'none' }}>{ago(p.updated_at || p.created_at)}</span>
        </div>
      </div>
    </Link>
  );
}

export function PropertyMiniCard({ p, to, onMove }: { p: Property; to: string; onMove?: () => void }) {
  const ph = p.photo || p.media?.find((m) => m.kind === 'photo')?.path || p.photos?.[0];
  return (
    <Link to={to} className="kcard" draggable onDragStart={(e) => e.dataTransfer.setData('text/property', String(p.id))}>
      <div className="row" style={{ alignItems: 'flex-start' }}>
        {ph && <img src={thumbUrl(ph)} alt="" loading="lazy" draggable={false} style={{ width: 52, height: 52, borderRadius: 8, objectFit: 'cover', flex: 'none' }} />}
        <div className="grow">
          <div className="kcard-title ellipsis">{p.address || t('Объект #{id}', { id: p.id })}</div>
          <div className="kcard-meta">{[districtLabel(p.district), bedroomsLabel(p.bedrooms), money(p.price, p.currency || 'USD')].filter(Boolean).join(' · ')}</div>
          <ExclusiveConflictBadge p={p} />
        </div>
      </div>
      <div className="row row-between mt" style={{ marginTop: 8 }}>
        <span className="faint" style={{ fontSize: 11, fontWeight: 600 }}>{p.agent?.name || PLATFORM}</span>
        {p.status === 'call_failed' && p.next_call_at ? <span className="badge badge-redsoft">{t('звонок')} {fmtDateTime(p.next_call_at)}</span>
          : p.shoot_at && p.status === 'shoot_scheduled' ? <span className="badge">{t('съёмка')} {fmtDateTime(p.shoot_at)}</span>
          : <span className="faint" style={{ fontSize: 11 }}>{ago(p.updated_at || p.created_at)}</span>}
      </div>
      {onMove && (
        <button type="button" className="btn btn-secondary btn-sm mt" style={{ marginTop: 8 }}
          onClick={(e) => { e.preventDefault(); e.stopPropagation(); onMove(); }}><ArrowRightLeft size={14} />{t('Перенести')}</button>
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
  const confirm = useConfirm();
  const deal = useDeal();
  const { data, isLoading, error } = useQuery({ queryKey: ['properties', 'board', deal], queryFn: () => api.properties.board(deal), refetchInterval: 30_000 });
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
    onSuccess: (_, { to }) => { qc.invalidateQueries({ queryKey: ['properties'] }); qc.invalidateQueries({ queryKey: ['property'] }); toast(t('Объект перенесён: {s}', { s: PROPERTY_STATUS[to] })); },
    onError: (e) => { toast(errText(e), true); qc.invalidateQueries({ queryKey: ['properties'] }); },
  });
  const hide = useMutation({
    mutationFn: (s: 'published' | 'rented') => api.properties.boardHide(s),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ['properties', 'board'] }); toast(t('Убрано с доски: {n}', { n: r.hidden })); },
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
      else open(t('Укажите, кто ответил, и его контакт'));
    } else if (to === 'shoot_scheduled' && from === 'owner_confirmed') open(t('Выберите дату съёмки'));
    else if (to === 'media_uploaded' && ['owner_confirmed', 'shoot_scheduled', 'returned'].includes(from)) open(t('Загрузите фото и видео'));
    else if (to === 'review' && ['shoot_scheduled', 'media_uploaded', 'returned'].includes(from)) {
      if (p.media_count) act.mutate({ p, to });
      else open(t('Сначала загрузите фото и видео'));
    } else if (from === 'review' && (to === 'returned' || to === 'published')) {
      if (isStaff) open(to === 'published' ? t('Проверьте пост и опубликуйте') : t('Напишите агенту, что поправить'));
      else toast(t('Объект на проверке: решение за оператором'), true);
    } else if (to === 'rented') toast(t('«Сдан» ставится подтверждённой сделкой'), true);
    else toast(t('Из «{from}» в «{to}» перенести нельзя', { from: PROPERTY_STATUS[from], to: PROPERTY_STATUS[to] }), true);
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
                  title={t('Объекты остаются в канале, боте и списках; карточка вернётся на доску при смене статуса')}
                  onClick={async () => { if (await confirm(t('Убрать с доски все карточки «{s}» ({n})? Объекты останутся в канале, боте и списках.', { s: PROPERTY_STATUS[s], n: items.length }))) hide.mutate(s); }}>
                  <Archive size={14} />{t('Убрать с доски')}
                </Button>
              )}
              <div className="kanban-col-body">
                {collapsed ? <Button size="sm" variant="secondary" block onClick={() => setShowPublished(true)}>{t('Показать')}</Button>
                  : items.length === 0 ? <div className="faint" style={{ fontSize: 12, padding: 6 }}>{t('Пусто')}</div>
                  : items.map((p) => <PropertyMiniCard key={p.id} p={p} to={propLink(base, p.id)} onMove={base ? () => setMoving(p) : undefined} />)}
                {s === 'published' && showPublished && !isStaff && items.length > 0 && <Button size="sm" variant="ghost" block onClick={() => setShowPublished(false)}>{t('Свернуть')}</Button>}
              </div>
            </div>
          );
        })}
      </div>
      {statusBar && <StatusBar board={boardRef} stages={cols} labels={PROPERTY_STATUS} count={(s) => data?.[s]?.length ?? 0} />}
      {/* touch (Mini App) has no HTML drag & drop: the same moves from a menu, like the lead board */}
      <Modal open={!!moving} onClose={() => setMoving(null)} title={t('Перенести объект #{id}', { id: moving?.id ?? '' })}>
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
      <Seg value={view} onChange={setView} options={[{ value: 'board', label: t('Доска') }, { value: 'list', label: t('Списком') }]} />
      <FreeingList base={base} />
      {view === 'board' ? <PropertyBoard base={base} /> : <PropertyList mine base={base} />}
    </div>
  );
}

const WORK = PROPERTY_BOARD.filter((s) => s !== 'published' && s !== 'rented');

/** «Освобождаются»: «Собственник сдал» objects whose lease has ended (backend ?freeing=1): call the owner, one click
 * «Снова в работу» brings the object back to «Взят» (Stanislav 11-16). Agents see their own, staff all. */
function FreeingList({ base = '' }: { base?: string }) {
  const { data } = useQuery({ queryKey: ['properties', 'freeing'], queryFn: () => api.properties.list({ freeing: 1, limit: 100 }) });
  const items = itemsOf(data);
  if (!items.length) return null;
  return (
    <div className="card col gap-sm" style={{ padding: 14 }}>
      <div className="label">{t('Освобождаются')} <span className="tab-count">{items.length}</span></div>
      {items.map((p) => (
        <div key={p.id} className="row row-wrap gap-sm" style={{ justifyContent: 'space-between' }}>
          <Link to={propLink(base, p.id)}><b>{p.address || `#${p.id}`}</b> <span className="muted">· {t('свободна с {d}', { d: fmtDate(p.rent_to) })}{p.agent?.name ? ` · ${p.agent.name}` : ''}</span></Link>
          <Resume p={p} />
        </div>
      ))}
    </div>
  );
}

/** «В работе»: the board, or the same objects as a table (Stanislav 11-11); published / rented live in «База объектов».
 * Board: the «СТАТУСЫ» panel (tap = scroll to that column); list: stage chips with counts filter it (again = all). */
export function WorkProperties() {
  const [view, setView] = useState<'board' | 'list'>('board');
  const [stage, setStage] = useState<PropertyStatus | ''>('');
  const deal = useDeal();
  const { data } = useQuery({ queryKey: ['properties', 'board', deal], queryFn: () => api.properties.board(deal), refetchInterval: 30_000 });
  return (
    <div className="col">
      <div className="row row-wrap">
        <Seg value={view} onChange={setView} options={[{ value: 'board', label: t('Доска') }, { value: 'list', label: t('Списком') }]} />
        {/* the board has the «СТАТУСЫ» panel instead of the chips (Stanislav 11-13) */}
        {view === 'list' && <Seg value={stage} onChange={(s) => setStage(stage === s ? '' : s)}
          options={WORK.map((s) => ({ value: s, label: <>{PROPERTY_STATUS[s]} <span className="tab-count">{data?.[s]?.length ?? 0}</span></> }))} />}
      </div>
      <FreeingList />
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
  const deal = useDeal();
  const scoped: PropertyScope = { deal, mine: mine ? 1 : undefined, base: scope === 'all' ? 1 : undefined, agent_id: agentId ?? (agent ? Number(agent) : undefined),
    status: archived ? 'archived' : only || (mine ? '' : BASE.join(',')) };
  const statusQ = status === 'work' ? WORK.join(',') : status || scoped.status;
  const { data, isLoading, isFetching, error } = useQuery({
    queryKey: ['properties', 'list', scope, f, statusQ, scoped.agent_id, reason, deal, view === 'map' ? -1 : page],
    queryFn: () => api.properties.list({ ...scoped, ...f, status: statusQ, archive_reason: reason || undefined,
      limit: view === 'map' ? MAP_LIMIT : PAGE, offset: view === 'map' ? 0 : page * PAGE }),
    placeholderData: (p, prev) => (prev?.queryKey[2] === scope ? p : undefined), // keep the list while a filter reloads, never across tabs
  });
  const facets = useQuery({ queryKey: ['properties', 'facets', scope, scoped], queryFn: () => api.properties.facets(scoped), staleTime: 60_000 });
  const counts = useQuery({ queryKey: ['properties', 'counts', deal], queryFn: () => api.properties.counts(deal), enabled: isStaff && (scope === 'all' || scope === 'archive') }); // whole-base totals: staff only
  const total = data?.total ?? 0;
  const pages = view === 'map' ? 1 : Math.max(1, Math.ceil(total / PAGE));
  const items = itemsOf(data);
  const navigate = useNavigate();
  const link = (id: number) => propLink(base, id);
  const filtered = !!(f.source.length || f.district.length || f.metro || f.bedrooms || f.price_min || f.price_max || status || (agent && !fixedAgent) || reason);
  const segOpts = scope === 'all' ? ['', ...BASE] : scope === 'mine' ? ['', 'work', 'published', 'rented'] : null;
  return (
    <>
      <FeedFilters f={f} patch={(p) => set({ ...p, page: 0 })} view={only ? undefined : view} onView={(v) => set({ view: v === 'map' ? v : '', page: 0 })}
        facets={facets.data} sourceLabel={(v) => SOURCE_LABEL[v] || v} metroLabel={metroLabel} placeholder={t('Адрес, агент, #тег')}
        seg={segOpts ? { value: status, onChange: (v) => set({ status: v, page: 0 }), options: segOpts.map((v) => ({ value: v, label: v === 'work' ? t('В работе') : v ? PROPERTY_STATUS[v as PS] : t('Все') })) } : undefined}
        filtered={filtered} onReset={() => set({ ...Object.fromEntries(FILTER_KEYS.filter((k) => k !== 'q' && k !== 'view' && !(fixedAgent && k === 'agent')).map((k) => [k, ''])), status: '' })}
        found={data ? `${t('Найдено: {n}', { n: total.toLocaleString(LOCALE) })}${view === 'list' && total > PAGE ? t(' · показаны {a}–{b}', { a: (page * PAGE + 1).toLocaleString(LOCALE), b: Math.min(total, (page + 1) * PAGE).toLocaleString(LOCALE) }) : ''}` : ''}
        boxes={<>
          {isStaff && !mine && !fixedAgent && (
            <div className="fbox"><span className="flabel">{t('Агент')}</span>
              <select className="select" value={agent} aria-label={t('Агент')} onChange={(e) => set({ agent: e.target.value, page: 0 })}>
                <option value="">{t('Все')}</option>
                <option value="0">{PLATFORM} {t('(без агента)')}</option>
                {itemsOf(agents.data).slice().sort((a, b) => Number(b.is_active !== false) - Number(a.is_active !== false) || (a.name || '').localeCompare(b.name || '')).map((u) => (
                  <option key={u.id} value={u.id}>{u.name}{u.agent_tag ? ` · #${u.agent_tag}` : ''}{u.is_active === false ? t(' (не работает)') : ''}</option>
                ))}
              </select></div>
          )}
          {archived && (
            <div className="fbox"><span className="flabel">{t('Причина')}</span>
              <Select aria-label={t('Причина')} value={reason} onChange={(e) => set({ reason: e.target.value, page: 0 })} placeholder={t('Любая')}
                options={Object.entries(ARCHIVE_REASON).map(([value, label]) => ({ value, label }))} /></div>
          )}
        </>} />
      {counts.data && (
        <div className="muted" style={{ fontSize: 13, marginBottom: 6 }}>
          {t('Всего объектов:')} <b>{Object.values(counts.data).reduce((a, b) => a + b, 0).toLocaleString(LOCALE)}</b>
          {' · '}{(['published', 'rented', 'archived'] as PS[]).map((k) => `${PROPERTY_STATUS[k]}: ${(counts.data[k] || 0).toLocaleString(LOCALE)}`).join(' · ')}
        </div>
      )}
      {isLoading ? <Loading /> : error ? <ErrorBox error={error} /> : items.length === 0 ? <Empty text={filtered ? t('Ничего не найдено') : archived ? t('Архив пуст') : only === 'review' ? t('Очередь пуста: всё опубликовано') : t('Объектов нет')} /> : view === 'map' ? (
        <ListingsMap items={items} card={(p) => <PropertyCard key={p.id} p={p} to={link(p.id)} />} onPick={(p) => navigate(link(p.id))} />
      ) : scope === 'all' || archived ? (
        // «Архив» as «База объектов»: cards with the photo (Stanislav 11-15), the reason under the price
        <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))' }}>
          {items.map((p) => <PropertyCard key={p.id} p={p} to={link(p.id)} note={archived && p.archive_reason ? (
            <div className="muted" style={{ fontSize: 12 }}>{ARCHIVE_REASON[p.archive_reason] || p.archive_reason}{p.archive_comment ? ` · ${p.archive_comment}` : ''}</div>) : undefined} />)}
        </div>
      ) : (
        <>
          <div className="card hide-mobile">
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>#</th><th>{t('Адрес')}</th><th>{t('Цена')}</th><th>{t('Агент')}</th><th>{t('Статус')}</th>{(archived || only === 'archived') && <th>{t('Причина')}</th>}<th>{t('Обновлён')}</th></tr></thead>
                <tbody>
                  {items.map((p) => (
                    <tr key={p.id} className="clickable" onClick={() => navigate(link(p.id))}>
                      <td className="muted num">{p.id}</td>
                      <td><Link to={link(p.id)} style={{ fontWeight: 700, color: 'inherit' }}>{p.address || '—'}</Link><div><Place district={p.district} /></div></td>
                      <td className="num" style={{ fontWeight: 700 }}>{priceOf(p)}</td>
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
                {archived && p.archive_reason && <div className="muted" style={{ fontSize: 12, padding: '4px 6px' }}>{t('Причина:')} {ARCHIVE_REASON[p.archive_reason] || p.archive_reason}{p.archive_comment ? ` · ${p.archive_comment}` : ''}</div>}
              </div>
            ))}
          </div>
        </>
      )}
      {pages > 1 && (
        <div className="row gap-sm" style={{ justifyContent: 'center', marginTop: 16 }}>
          <Button variant="secondary" disabled={page === 0} onClick={() => { setPage(0); pageScroller().scrollTo(0, 0); }}>{t('« Первая')}</Button>
          <Button variant="secondary" disabled={page === 0} onClick={() => { setPage(page - 1); pageScroller().scrollTo(0, 0); }}>{t('‹ Назад')}</Button>
          <span className="muted" style={{ alignSelf: 'center', padding: '0 8px' }}>{t('Стр. {p} из {n}', { p: page + 1, n: pages })}{isFetching ? ' …' : ''}</span>
          <Button variant="secondary" disabled={page + 1 >= pages} onClick={() => { setPage(page + 1); pageScroller().scrollTo(0, 0); }}>{t('Вперёд ›')}</Button>
          <Button variant="secondary" disabled={page + 1 >= pages} onClick={() => { setPage(pages - 1); pageScroller().scrollTo(0, 0); }}>{t('Последняя »')}</Button>
        </div>
      )}
    </>
  );
}

/** Page numbers 1 2 3 … under a feed (David 11-25): the current one, two around it, the first and the last. */
function Pager({ page, pages, busy, go }: { page: number; pages: number; busy?: boolean; go: (n: number) => void }) {
  const nums = [...new Set([0, page - 2, page - 1, page, page + 1, page + 2, pages - 1])].filter((n) => n >= 0 && n < pages).sort((a, b) => a - b);
  return (
    <div className="row row-wrap gap-sm" style={{ justifyContent: 'center', marginTop: 16 }}>
      <Button variant="secondary" disabled={page === 0} onClick={() => go(page - 1)}>‹</Button>
      {nums.map((n, i) => <span key={n} className="row gap-sm">{i > 0 && n - nums[i - 1] > 1 && <span className="muted">…</span>}
        <Button variant={n === page ? 'primary' : 'secondary'} loading={busy && n === page} onClick={() => go(n)}>{n + 1}</Button></span>)}
      <Button variant="secondary" disabled={page + 1 >= pages} onClick={() => go(page + 1)}>›</Button>
    </div>
  );
}
