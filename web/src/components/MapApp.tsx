// Made with Claude (Anthropic) · © Homes in Georgia · Created by Irakli Kvirkvelia
'use client';
// Total map of every listing. One switch per Telegram channel; icon = the post's kind (apartment, house, commercial,
// land, car); colour = how fresh the post is (green < 5 days, amber 5–20, red-orange > 20).
// The page address holds the state: /map?from=<service>&<catalogue filters>&q=<address>&ch=…&age=… (ch/age list what
// is switched OFF), so any filtered view is a link. `?q=` is what the CRM's channel posts use (hge.ge/map?q=<address>).
import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import Link from 'next/link';
import { usePathname, useSearchParams } from 'next/navigation';
import type { Map as LMap, Marker, MarkerCluster, MarkerClusterGroup, MarkerOptions, Popup } from 'leaflet';
import { Icon, iconHtml } from './Icon';
import FilterSheet from './FilterSheet';
import { useSite } from './Providers';
import {
  CHANNELS, CONTACT, FRESHNESS, KIND_ICONS, MAP_CENTER, MAP_CHANNELS, SITE_URL, channelOf, freshnessOf, isService, telegramHref,
  type Freshness,
} from '@/lib/config';
import { activeCount, applyFilters, filtersToParams, itemsFor, parseFilters, toRow, type Filters } from '@/lib/filters';
import { addLocate, brandMap, doubleTapZoom, hasPoint, loadLeaflet, mapTiler, type LeafletLib } from '@/lib/leaflet';
import { entryData, saveEntry } from '@/lib/scroll';
import { useDesktop } from '@/lib/hooks';
import { thumb } from '@/lib/photos';
import type { Item } from '@/lib/types';

const isDesktop = () => matchMedia('(min-width: 900px)').matches;
const csv = (v: string | null) => new Set((v || '').split(',').filter(Boolean));
interface Point { x: Item; ch: string; kind: string; fresh: Freshness }
interface Selection { list: Point[]; idx: number; node: HTMLElement | null } // node: the desktop popup's content

function PointCard({ p, peek }: { p: Point; peek: boolean }) {
  const { fmt } = useSite();
  const { t } = fmt;
  const { x, ch, kind, fresh } = p;
  const pr = fmt.priceLabel(x);
  const message = t('l.msg', x.kind, fmt.title(x), pr.main + (pr.period ? ' ' + pr.period : ''), x.post_url || `${SITE_URL}/listing/${x.id}`) + `\nID: ${x.id}`;
  const agent = x.agent?.telegram || x.agent?.name || '';
  return (
    <div className="tm-wrap" data-listing={x.id} data-service={x.service} data-agent={agent}>
      <Link className={peek ? 'tm-peek' : 'map-card tm-card'} href={`/listing/${x.id}`}>
        {x.photos?.[0] && <img src={thumb(x.photos[0])} alt="" loading="lazy" />}
        <div><em style={{ '--c': fresh.color } as React.CSSProperties}><i />{t('map.ch.' + ch)} · {t(kind === 'car' ? 'kind.car' : 'map.kind.' + kind)} · {fmt.ago(x.published_at)}</em>
          <b>{pr.main} <small>{[pr.period, fmt.sqmPrice(x)].filter(Boolean).join(' · ')}</small></b><span>{fmt.title(x)}</span><small>{fmt.facts(x).join(' · ')}</small></div>
      </Link>
      <div className="tm-actions">
        <Link className="btn btn-outline" href={`/listing/${x.id}`}>{t('map.details')}</Link>
        <a className="btn btn-primary" href={telegramHref(x.agent?.telegram || CONTACT.telegram, message)} target="_blank" rel="noopener" data-lead="telegram"><Icon name="send" /><span>Telegram</span></a>
      </div>
    </div>
  );
}

export default function MapApp({ items }: { items: Item[] }) {
  const { fmt, notify } = useSite();
  const { t } = fmt;
  const pathname = usePathname();
  const search = useSearchParams();
  const desktop = useDesktop(900);
  const qs = search.toString();
  const params = useMemo(() => new URLSearchParams(qs), [qs]);

  // ---- what is shown ----
  const from = params.get('from') || '';
  const scoped = from === 'all' || isService(from); // only what that catalogue search found (the phone "map" view of a catalogue)
  const filters = useMemo(() => parseFilters(params), [params]);
  const filtered = !scoped && (activeCount(filters) > 0 || !!params.get('q'));
  const rows = useMemo(() => items.map(toRow), [items]);
  const source = useMemo(() => applyFilters(scoped ? itemsFor(rows, from) : rows, filters), [rows, scoped, from, filters]);
  const all = useMemo<Point[]>(() => source.filter(hasPoint).map(x => ({ x, ch: channelOf(x), kind: KIND_ICONS[x.kind] ? x.kind : 'apartment', fresh: freshnessOf(x) })), [source]);
  const noPoint = source.length - all.length; // listings without coordinates stay in the catalogue
  const off = useMemo(() => ({ ch: csv(params.get('ch')), age: csv(params.get('age')) }), [params]);
  const visible = useMemo(() => all.filter(p => !off.ch.has(p.ch) && !off.age.has(p.fresh.id)), [all, off]);
  const count = useMemo(() => {
    const c = { ch: {} as Record<string, number>, age: {} as Record<string, number> };
    all.forEach(p => { c.ch[p.ch] = (c.ch[p.ch] || 0) + 1; c.age[p.fresh.id] = (c.age[p.fresh.id] || 0) + 1; });
    return c;
  }, [all]);
  const n = visible.length;

  const write = (q: URLSearchParams) => history.replaceState(null, '', pathname + (q.toString() ? '?' + q : ''));
  const commit = (next: { ch: Set<string>; age: Set<string> }) => {
    const q = new URLSearchParams(params);
    q.delete('ch'); q.delete('age');
    Object.entries(next).forEach(([k, set]) => { if (set.size) q.set(k, [...set].join(',')); });
    write(q);
  };
  const flip = (group: 'ch' | 'age', id: string) => {
    const set = new Set(off[group]);
    if (set.has(id)) set.delete(id); else set.add(id);
    commit({ ...off, [group]: set });
  };
  const setAll = (on: boolean) => commit({ ch: on ? new Set() : new Set(MAP_CHANNELS.map(c => c.id)), age: new Set() });

  // ---- the Leaflet map ----
  const box = useRef<HTMLDivElement>(null);
  const lib = useRef<{ L: LeafletLib; map: LMap; layer: MarkerClusterGroup } | null>(null);
  const [ready, setReady] = useState(false);
  const [sel, setSel] = useState<Selection | null>(null);
  const popup = useRef<Popup | null>(null);
  const state = useRef({ all, visible, scoped, filtered, notify, t });
  useEffect(() => { state.current = { all, visible, scoped, filtered, notify, t }; });

  const group = (p: Point) => {
    // Everything at this exact point can be swiped through, newest first.
    const same = state.current.visible.filter(q => q.x.lat === p.x.lat && q.x.lon === p.x.lon).sort((a, b) => String(b.x.published_at).localeCompare(String(a.x.published_at)));
    return same.length ? same : [p];
  };
  const select = (p: Point) => {
    const l = lib.current;
    if (!l) return;
    const list = group(p);
    const idx = Math.max(0, list.indexOf(p));
    if (!isDesktop()) return setSel({ list, idx, node: null });
    const node = document.createElement('div');
    const pop = l.L.popup({ className: 'pop', closeButton: false, offset: [0, -12], maxWidth: 280, autoPanPaddingTopLeft: [340, 24], autoPanPaddingBottomRight: [24, 24] })
      .setLatLng([p.x.lat!, p.x.lon!]).setContent(node);
    popup.current = pop;
    pop.openOn(l.map);
    setSel({ list, idx, node });
  };
  const selectRef = useRef(select);
  useEffect(() => { selectRef.current = select; });
  const dropPopup = () => { const pop = popup.current; popup.current = null; pop?.close(); }; // cleared first: closing fires popupclose
  const closeCard = () => { saveEntry({ card: null }); dropPopup(); setSel(null); };
  const closeRef = useRef(closeCard);
  useEffect(() => { closeRef.current = closeCard; });

  useEffect(() => {
    document.body.classList.add('map-page');
    let alive = true;
    let map: LMap | undefined;
    let watch: ResizeObserver | undefined;
    loadLeaflet().then(L => {
      const el = box.current;
      if (!alive || !el) return;
      // Coming back from a listing: same place, same zoom, same open card.
      const saved = entryData();
      map = L.map(el, { zoomControl: false, doubleClickZoom: !L.Browser.mobile }).setView(saved.center || MAP_CENTER, saved.zoom ?? (isDesktop() ? 13 : 12));
      map.on('moveend', () => { const c = map!.getCenter(); saveEntry({ center: [c.lat, c.lng], zoom: map!.getZoom() }); });
      watch = new ResizeObserver(() => map?.invalidateSize());
      watch.observe(el);
      if (L.Browser.mobile) doubleTapZoom(L, map);
      L.control.zoom({ position: 'bottomright' }).addTo(map);
      addLocate(L, map, k => state.current.t(k), text => state.current.notify(text));
      brandMap(map);
      const streets = mapTiler(L, 'streets-v2').addTo(map);
      L.control.layers({ [state.current.t('map.streets')]: streets, [state.current.t('map.satellite')]: mapTiler(L, 'hybrid', 'jpg') }, undefined, { position: isDesktop() ? 'topright' : 'bottomright' }).addTo(map);
      map.on('click', () => closeRef.current());
      map.on('popupclose', e => { if (e.popup === popup.current) closeRef.current(); });
      // Cluster ring shows the freshness mix of the listings inside it.
      const layer = L.markerClusterGroup({
        maxClusterRadius: 52, showCoverageOnHover: false, zoomToBoundsOnClick: false, spiderfyOnMaxZoom: false,
        iconCreateFunction(cl) {
          const by: Record<string, number> = {};
          cl.getAllChildMarkers().forEach(m => { const f = (m.options as { fresh?: string }).fresh || ''; by[f] = (by[f] || 0) + 1; });
          const total = cl.getChildCount();
          let acc = 0;
          const stops = FRESHNESS.filter(f => by[f.id]).map(f => { const a = acc; acc += (by[f.id] / total) * 360; return `${f.color} ${a}deg ${acc}deg`; });
          return L.divIcon({ className: 'dc', html: `<div style="--g:conic-gradient(${stops.join(',')})"><span>${total}</span></div>`, iconSize: [46, 46] });
        },
      }).addTo(map);
      // Listings at one exact point open as swipeable cards; a real cluster still zooms in.
      layer.on('clusterclick', ev => {
        const cluster = (ev as unknown as { layer: MarkerCluster }).layer;
        const kids = cluster.getAllChildMarkers();
        const first = kids[0].getLatLng();
        if (kids.every(k => k.getLatLng().equals(first))) {
          const best = kids.map(k => (k.options as { place: Point }).place).sort((a, b) => String(b.x.published_at).localeCompare(String(a.x.published_at)))[0];
          return selectRef.current(best);
        }
        if (map!.getBoundsZoom(cluster.getBounds()) > map!.getZoom()) cluster.zoomToBounds({ padding: [24, 24] });
        else cluster.spiderfy();
      });
      lib.current = { L, map, layer };
      setReady(true);
      const s = state.current;
      // ?pin=lat,lng&title=…: one place that may not be among the listings here (the bot's «📍 On the map» for an object
      // the site doesn't show yet) — its own marker above the others, the map opens on it.
      const url = new URLSearchParams(location.search);
      const at = (url.get('pin') || '').split(',').map(Number);
      const pin = at.length === 2 && at.every(Number.isFinite) && Math.abs(at[0]) <= 90 && Math.abs(at[1]) <= 180 ? at as [number, number] : null;
      if (pin) {
        const title = (url.get('title') || '').replace(/[&<>"]/g, '').slice(0, 80);
        L.marker(pin, {
          interactive: false, keyboard: false, zIndexOffset: 50000,
          icon: L.divIcon({ className: 'cm cm-active', iconSize: undefined, html: `<div style="--c:var(--brand-strong)"><i>${iconHtml('pin')}</i>${title}</div>` }),
        }).addTo(map);
        if (!saved.center) map.setView(pin, 16, { animate: false });
      }
      if (saved.card) {
        const p = s.visible.find(q => q.x.id === saved.card);
        if (p) requestAnimationFrame(() => selectRef.current(p));
      } else if (!pin && (s.scoped || s.filtered) && s.all.length && !saved.center) {
        map.fitBounds(L.latLngBounds(s.all.map(p => [p.x.lat!, p.x.lon!] as [number, number])), {
          paddingTopLeft: isDesktop() ? [340, 80] : [36, 170], paddingBottomRight: [36, 36], maxZoom: 15, animate: false,
        });
      }
    }).catch(() => {});
    return () => { alive = false; document.body.classList.remove('map-page'); watch?.disconnect(); lib.current = null; popup.current = null; map?.remove(); };
  }, []);

  const pinIcon = (L: LeafletLib, p: Point, active = false) => {
    const { x, fresh } = p;
    const period = x.period ? t('period.' + x.period).replace(/\s+/g, '') : '';
    const price = x.price_usd ? fmt.compactMoney(x.price_usd).replace(/[&<>"]/g, '') + (period ? `<small>${period.replace(/[&<>"]/g, '')}</small>` : '') : '';
    return L.divIcon({ className: active ? 'cm cm-active' : 'cm', iconSize: undefined, html: `<div style="--c:${fresh.color}"><i>${iconHtml(KIND_ICONS[p.kind])}</i>${price}</div>` });
  };

  // Pins follow the filters and switches. Changing them closes the open card.
  const [seenVisible, setSeenVisible] = useState(visible);
  if (seenVisible !== visible) { setSeenVisible(visible); setSel(null); }
  useEffect(() => { if (!sel) dropPopup(); });
  useEffect(() => {
    const l = lib.current;
    if (!ready || !l) return;
    l.layer.clearLayers();
    visible.forEach(p => {
      const m = l.L.marker([p.x.lat!, p.x.lon!], { fresh: p.fresh.id, place: p, keyboard: true, title: fmt.title(p.x), icon: pinIcon(l.L, p) } as MarkerOptions);
      m.on('click', () => selectRef.current(p));
      l.layer.addLayer(m);
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, visible, fmt]);

  // The chosen listing's pin sits above everything else on the map.
  const active = sel ? sel.list[sel.idx] : null;
  useEffect(() => {
    const l = lib.current;
    if (!ready || !l || !active) return;
    saveEntry({ card: active.x.id });
    const m: Marker = l.L.marker([active.x.lat!, active.x.lon!], { icon: pinIcon(l.L, active, true), interactive: false, keyboard: false, zIndexOffset: 100000 }).addTo(l.map);
    return () => { m.remove(); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, active, fmt]);

  // Phones: the strip of cards at one point is swiped; the card in the middle is the chosen one.
  const strip = (el: HTMLDivElement | null) => {
    if (!el || !sel || sel.list.length < 2) return;
    const cards = [...el.children] as HTMLElement[];
    const cur = cards[sel.idx];
    if (cur && !el.dataset.placed) { el.dataset.placed = '1'; el.scrollLeft = cur.offsetLeft - (el.clientWidth - cur.offsetWidth) / 2; }
  };
  const onStripScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    const middle = el.scrollLeft + el.clientWidth / 2;
    let best = 0, gap = Infinity;
    [...el.children].forEach((c, i) => { const d = Math.abs((c as HTMLElement).offsetLeft + (c as HTMLElement).offsetWidth / 2 - middle); if (d < gap) { gap = d; best = i; } });
    setSel(s => (s && s.idx !== best ? { ...s, idx: best } : s));
  };

  // ---- markup ----
  const [sheet, setSheet] = useState(false);
  const row = (g: 'ch', id: string, ic: string) => {
    const c = count.ch[id] || 0;
    if ((scoped || filtered) && !c) return null;
    const on = !off.ch.has(id);
    return (
      <button key={id} type="button" className={`lrow${on && c ? ' on' : ''}${c ? '' : ' empty'}`} data-group={g} data-id={id} disabled={!c} aria-pressed={on} title={'@' + (isService(id) ? CHANNELS[id] : '')} onClick={() => flip(g, id)}>
        <i><Icon name={ic} /></i><b>{t(`map.${g}.${id}`)}</b><small>{c || t('svc.soon')}</small><span className="sw" />
      </button>
    );
  };
  const ageButton = (f: Freshness) => (
    <button key={f.id} type="button" className={`age${off.age.has(f.id) ? '' : ' on'}`} data-group="age" data-id={f.id} style={{ '--c': f.color } as React.CSSProperties} aria-pressed={!off.age.has(f.id)} onClick={() => flip('age', f.id)}>
      <i /><span>{t('map.age.' + f.id)}</span><small>{count.age[f.id] || 0}</small>
    </button>
  );
  const list = new URLSearchParams(params);
  ['from', 'ch', 'age'].forEach(k => list.delete(k));
  const foundCount = (
    <b>{t('map.found', n)}{noPoint > 0 && <small>{fmt.num(noPoint)} {t('cat.no.coords')}</small>}</b>
  );
  const peek = !desktop && sel && !sel.node;

  return (
    <>
      <style>{'.footer{display:none}'}</style>
      <section className={`mapapp${peek ? ' peeking' : ''}`}>
        <h1 className="sr-only">{t('map.title')}</h1>
        <div className="tmap" id="tmap" ref={box} />
        <aside className="mlayers" aria-label={t('map.title')}>
          <h2>{t('map.title')}</h2><p>{t('map.count', n)}</p>
          {noPoint > 0 && <p className="map-nocoords">{t('map.nocoords', noPoint)} <Link href={'/' + (scoped ? from : 'all')}>{t('map.nocoords.link')}</Link></p>}
          <button type="button" className="btn btn-primary btn-block mlayers-filter" onClick={() => setSheet(true)}><Icon name="sliders" /><span>{t('f.title')}</span></button>
          <h3>{t('map.channels')}</h3><div className="lgroup">{MAP_CHANNELS.map(c => row('ch', c.id, c.icon))}</div>
          <div className="kinds-legend">{['apartment', 'house', 'commercial', 'land'].map(k => <span key={k}><i><Icon name={KIND_ICONS[k]} /></i>{t('map.kind.' + k)}</span>)}</div>
          <h3>{t('map.freshness')}</h3><div className="ages">{FRESHNESS.map(ageButton)}</div>
          <div className="lfoot"><button type="button" className="btn btn-outline" onClick={() => setAll(true)}>{t('map.show.all')}</button><button type="button" className="btn btn-outline" onClick={() => setAll(false)}>{t('map.hide.all')}</button></div>
        </aside>
        <div className="mchips" role="group" aria-label={t('map.channels')}>
          <button type="button" className="mchip all" onClick={() => setAll(true)}>{t('map.all')} · {n}</button>
          {MAP_CHANNELS.filter(c => count.ch[c.id]).map(c => (
            <button key={c.id} type="button" className={`mchip${off.ch.has(c.id) ? ' off' : ''}`} data-group="ch" data-id={c.id} aria-pressed={!off.ch.has(c.id)} onClick={() => flip('ch', c.id)}><i><Icon name={c.icon} /></i>{t('map.ch.' + c.id)}</button>
          ))}
        </div>
        <div className="mages" role="group" aria-label={t('map.freshness')}>{FRESHNESS.map(ageButton)}</div>
        <div className="mfound" hidden={!scoped && !filtered}>
          {filtered && <>
            <Link className="mfound-list" href={`/all${list.toString() ? '?' + list : ''}`}><Icon name="list" /><span>{t('cat.view.list')}</span></Link>
            {foundCount}
            <button type="button" className="mfound-x" aria-label={t('cat.reset')} title={t('cat.reset')} onClick={() => {
              const keep = new URLSearchParams();
              ['ch', 'age'].forEach(k => { const v = params.get(k); if (v) keep.set(k, v); });
              write(keep);
            }}><Icon name="x" /></button>
          </>}
          {scoped && <>
            <Link className="mfound-list" href={`/${from}${list.toString() ? '?' + list : ''}`}><Icon name="list" /><span>{t('cat.view.list')}</span></Link>
            {foundCount}
            <Link className="mfound-x" href="/map" aria-label={t('map.found.clear')} title={t('map.found.clear')}><Icon name="x" /></Link>
          </>}
        </div>
        <div className="mpeek" hidden={!peek}>
          {peek && sel && <>
            <div className={`mstrip${sel.list.length > 1 ? ' many' : ''}`} ref={strip} onScroll={sel.list.length > 1 ? onStripScroll : undefined}>
              {sel.list.map((p, i) => <div key={p.x.id} className="mstrip-card" data-i={i}><PointCard p={p} peek /></div>)}
            </div>
            {sel.list.length > 1 && <div className="mstrip-dots" aria-hidden="true">{sel.list.map((p, i) => <i key={p.x.id} className={i === sel.idx ? 'on' : undefined} />)}</div>}
          </>}
        </div>
      </section>
      {sel?.node && createPortal(sel.list.length > 1 ? <div className="tm-list">{sel.list.map(p => <PointCard key={p.x.id} p={p} peek={false} />)}</div> : <PointCard p={sel.list[0]} peek={false} />, sel.node)}
      {sheet && <FilterSheet serviceId="all" base={items} filters={filters} onClose={() => setSheet(false)}
        onApply={(next: Filters) => write(filtersToParams(next, params))} />}
    </>
  );
}
