// «Новые объявления» as a map (David 2026-11-09): the same filtered feed, a price pin per ad with a pin; a tap opens the ad's
// card in a popup (with «Беру в работу»; Stanislav 11-11, it was under the map), «Рядом со мной» centres on the agent so he sees what came out around him.
// Clusters like hge.ge/#/map (Stanislav 11-13): hundreds of overlapping pins lagged on a phone, so leaflet.markercluster groups them into
// count bubbles with a freshness ring (green ≤5 days, amber ≤20, red older), a tap zooms in / spiderfies at max zoom.
import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import 'leaflet.markercluster';
import 'leaflet.markercluster/dist/MarkerCluster.css';
import { addBaseLayer } from '../../lib/mapTiles';
import { LocateFixed } from 'lucide-react';
import { Button, Modal } from '../../components/ui';

const TBILISI: L.LatLngTuple = [41.7151, 44.8271];
/** an ad or an object (the base tabs' map, Stanislav 11-13) */
type Pin = { id: string | number; lat?: number | null; lng?: number | null; price?: number | null; district?: string | null; address?: string | null;
  created_at?: string | null; published_at?: string | null };
const price = (l: Pin) => (l.price != null ? `$${Math.round(Number(l.price))}` : '?');
// hge.ge «How fresh» colours; no date = brand blue
const FRESH = ['#2DB87A', '#F0A92E', '#E5532E'];
const DAY = 864e5;
const fresh = (l: Pin) => {
  const t = Date.parse(l.published_at || l.created_at || '');
  if (Number.isNaN(t)) return -1;
  const d = (Date.now() - t) / DAY;
  return d <= 5 ? 0 : d <= 20 ? 1 : 2;
};
const HOME = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z"/></svg>';
const pinIcon = (l: Pin, on: boolean) => {
  const f = fresh(l);
  return L.divIcon({ className: `cm${on ? ' cm-active' : ''}`, iconSize: undefined,
    html: `<div style="--c:${f < 0 ? 'var(--brand)' : FRESH[f]}"><i>${HOME}</i>${price(l)}</div>` });
};
type M = L.Marker & { fresh?: number };
// the ring is a conic gradient of the cluster's freshness mix, like hge.ge
const clusterIcon = (c: L.MarkerCluster) => {
  const ms = c.getAllChildMarkers() as M[];
  const n = [0, 0, 0, 0];
  for (const m of ms) n[(m.fresh ?? -1) + 1]++;
  let a = 0;
  const seg = ['#032D6A', ...FRESH].map((col, i) => { const s = a; a += (n[i] / ms.length) * 360; return n[i] ? `${col} ${s}deg ${a}deg` : ''; }).filter(Boolean);
  return L.divIcon({ className: 'dc', iconSize: [46, 46], html: `<div style="--g:conic-gradient(${seg.join(',')})"><span>${ms.length}</span></div>` });
};

export function ListingsMap<T extends Pin>({ items, card }: { items: T[]; card: (l: T) => ReactNode }) {
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layer = useRef<L.MarkerClusterGroup | null>(null);
  const markers = useRef(new Map<T['id'], M>());
  const meDot = useRef<L.CircleMarker | null>(null);
  const [sel, setSel] = useState<T | null>(null);
  const [me, setMe] = useState<string>('');
  const fit = useRef<() => void>(() => {});  // pins → view, until the agent moves the map himself
  const moved = useRef(false);
  // taken / marked as agent: the ad leaves the feed, so the popup closes with it
  useEffect(() => { if (sel && !items.some((l) => l.id === sel.id)) setSel(null); }, [items, sel]);
  const pinned = items.filter((l) => l.lat != null && l.lng != null);

  useEffect(() => {
    if (!box.current || map.current) return;
    // maxZoom: the tile layer comes async, markercluster needs it up front; preferCanvas: the «me» dot; markers stay DOM but only the visible clusters exist, so 500+ ads pan smoothly
    map.current = L.map(box.current, { zoomControl: true, preferCanvas: true, maxZoom: 19 }).setView(TBILISI, 12);
    addBaseLayer(map.current);
    layer.current = L.markerClusterGroup({ showCoverageOnHover: false, chunkedLoading: true, iconCreateFunction: clusterIcon })
      .on('clusterclick', () => { moved.current = true; })  // its zoom has no originalEvent; a later resize must not refit back out
      .addTo(map.current);
    // the Telegram webview / a rotated phone resizes the box: Leaflet must know, or part of the map stays grey
    map.current.on('dragstart zoomstart', (e) => { if ((e as L.LeafletEvent & { originalEvent?: Event }).originalEvent) moved.current = true; });
    const ro = new ResizeObserver(() => { map.current?.invalidateSize(); if (!moved.current) fit.current(); });
    ro.observe(box.current);
    return () => { ro.disconnect(); map.current?.remove(); map.current = null; };
  }, []);

  // markers are built once per data change; the selection only swaps two icons (below)
  useEffect(() => {
    const m = map.current, g = layer.current;
    if (!m || !g) return;
    g.clearLayers();
    markers.current.clear();
    const ms = pinned.map((l) => {
      const mk: M = L.marker([l.lat!, l.lng!], { icon: pinIcon(l, false) }).on('click', () => setSel(l));
      mk.fresh = fresh(l);
      markers.current.set(l.id, mk);
      return mk;
    });
    g.addLayers(ms);
    fit.current = () => {
      if (pinned.length) m.fitBounds(L.latLngBounds(pinned.map((l) => [l.lat!, l.lng!] as L.LatLngTuple)), { padding: [40, 40], maxZoom: 15 });
    };
    m.invalidateSize();
    if (!moved.current) fit.current();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items]);

  useEffect(() => {
    if (!sel) return;
    const mk = markers.current.get(sel.id);
    mk?.setIcon(pinIcon(sel, true)).setZIndexOffset(1000);
    return () => { mk?.setIcon(pinIcon(sel, false)).setZIndexOffset(0); };
  }, [sel, items]);

  const locate = () => {
    if (!navigator.geolocation) return setMe('Геолокация недоступна');
    setMe('Ищу вас…');
    navigator.geolocation.getCurrentPosition((p) => {
      const at: L.LatLngTuple = [p.coords.latitude, p.coords.longitude];
      moved.current = true;
      map.current?.setView(at, 15);
      meDot.current?.remove();
      if (map.current) meDot.current = L.circleMarker(at, { radius: 8, color: '#fff', weight: 3, fillColor: '#2f6bff', fillOpacity: 1 }).addTo(map.current);
      setMe('');
    }, () => setMe('Нет доступа к геолокации'), { enableHighAccuracy: true, timeout: 10000 });
  };

  return (
    <div className="col gap-sm">
      <div className="row row-between">
        <span className="faint" style={{ fontSize: 12 }}>На карте {pinned.length} из {items.length}{items.length > pinned.length ? ' (у остальных нет точки)' : ''}</span>
        <Button size="sm" variant="secondary" onClick={locate}><LocateFixed size={14} />Рядом со мной</Button>
      </div>
      {me && <div className="muted" style={{ fontSize: 12 }}>{me}</div>}
      <div ref={box} style={{ height: '60vh', minHeight: 320, borderRadius: 12, overflow: 'hidden', zIndex: 0 }} />
      <Modal open={!!sel} onClose={() => setSel(null)} title={sel ? `${price(sel)} · ${sel.district || sel.address || 'Объявление'}` : ''}>
        {sel && card(sel)}
      </Modal>
    </div>
  );
}
