// «Новые объявления» as a map (David 2026-11-09): the same filtered feed, a price pin per ad with a pin; a tap opens the ad's
// card in a popup (with «Беру в работу»; Stanislav 11-11, it was under the map), «Рядом со мной» centres on the agent so he sees what came out around him.
import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { addBaseLayer } from '../../lib/mapTiles';
import { LocateFixed } from 'lucide-react';
import type { Listing } from '../../api/types';
import { Button, Modal } from '../../components/ui';

const TBILISI: L.LatLngTuple = [41.7151, 44.8271];
const price = (l: Listing) => (l.price != null ? `$${Math.round(Number(l.price))}` : '?');

export function ListingsMap({ items, card }: { items: Listing[]; card: (l: Listing) => ReactNode }) {
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layer = useRef<L.LayerGroup | null>(null);
  const [sel, setSel] = useState<Listing | null>(null);
  const [me, setMe] = useState<string>('');
  const fit = useRef<() => void>(() => {});  // pins → view, until the agent moves the map himself
  const moved = useRef(false);
  // taken / marked as agent: the ad leaves the feed, so the popup closes with it
  useEffect(() => { if (sel && !items.some((l) => l.id === sel.id)) setSel(null); }, [items, sel]);
  const pinned = items.filter((l) => l.lat != null && l.lng != null);

  useEffect(() => {
    if (!box.current || map.current) return;
    map.current = L.map(box.current, { zoomControl: true }).setView(TBILISI, 12);
    addBaseLayer(map.current);
    layer.current = L.layerGroup().addTo(map.current);
    // the Telegram webview / a rotated phone resizes the box: Leaflet must know, or part of the map stays grey
    map.current.on('dragstart zoomstart', (e) => { if ((e as L.LeafletEvent & { originalEvent?: Event }).originalEvent) moved.current = true; });
    const ro = new ResizeObserver(() => { map.current?.invalidateSize(); if (!moved.current) fit.current(); });
    ro.observe(box.current);
    return () => { ro.disconnect(); map.current?.remove(); map.current = null; };
  }, []);

  useEffect(() => {
    const m = map.current, g = layer.current;
    if (!m || !g) return;
    g.clearLayers();
    for (const l of pinned) {
      const icon = L.divIcon({ className: 'price-pin-wrap', html: `<span class="price-pin${sel?.id === l.id ? ' on' : ''}">${price(l)}</span>`, iconSize: undefined });
      L.marker([l.lat!, l.lng!], { icon }).on('click', () => setSel(l)).addTo(g);
    }
    fit.current = () => {
      if (pinned.length) m.fitBounds(L.latLngBounds(pinned.map((l) => [l.lat!, l.lng!] as L.LatLngTuple)), { padding: [40, 40], maxZoom: 15 });
    };
    m.invalidateSize();
    if (!sel && !moved.current) fit.current();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, sel?.id]);

  const locate = () => {
    if (!navigator.geolocation) return setMe('Геолокация недоступна');
    setMe('Ищу вас…');
    navigator.geolocation.getCurrentPosition((p) => {
      const at: L.LatLngTuple = [p.coords.latitude, p.coords.longitude];
      moved.current = true;
      map.current?.setView(at, 15);
      if (layer.current) L.circleMarker(at, { radius: 8, color: '#fff', weight: 3, fillColor: '#2f6bff', fillOpacity: 1 }).addTo(layer.current);
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
