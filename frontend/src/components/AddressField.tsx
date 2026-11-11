// The object's address with an OSM check (Stanislav 11-11): typed or pasted (often from Google Maps) → suggestions from
// GET /geo/search, a pick puts the point on a small map; the agent drags the marker (or taps the map) to correct it.
// A pasted Google Maps link / «lat, lng» is the point itself; OSM only names the address there.
import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { MapPin } from 'lucide-react';
import { api } from '../api';
import type { GeoHit } from '../api/types';
import { Input } from './ui';

export type Point = { lat: number; lng: number };
/** GET /geo/point: what a point fills in — the CRM district and the nearest metro tag within 1.5 km (+ metres) */
export type PointFacts = { district: string | null; metro: string | null; metro_m: number | null };
const TBILISI: L.LatLngTuple = [41.7151, 44.8271];
/** Brand drop pin, tip anchored on the point (the old 📍 emoji sat off the point and read as clutter, Stanislav 11-11). */
const PIN = L.divIcon({
  className: 'addr-pin', iconSize: [34, 44], iconAnchor: [17, 43],
  html: '<svg viewBox="0 0 34 44" width="34" height="44"><path d="M17 43C17 43 32 27.5 32 16.5A15 15 0 0 0 2 16.5C2 27.5 17 43 17 43Z" fill="#E3172D" stroke="#fff" stroke-width="2"/><circle cx="17" cy="16.5" r="5.5" fill="#fff"/></svg>',
});
const isLinkOrPoint = (s: string) => /^\s*(https?:\/\/|-?\d{1,2}\.\d+\s*,)/.test(s);

function PointMap({ point, onPoint, disabled }: { point: Point | null; onPoint: (p: Point) => void; disabled?: boolean }) {
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const marker = useRef<L.Marker | null>(null);
  const cb = useRef(onPoint);
  cb.current = onPoint;
  useEffect(() => {
    if (!box.current) return;
    const m = L.map(box.current).setView(point ? [point.lat, point.lng] : TBILISI, point ? 17 : 12);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© OpenStreetMap' }).addTo(m);
    if (!disabled) m.on('click', (e: L.LeafletMouseEvent) => cb.current({ lat: e.latlng.lat, lng: e.latlng.lng }));
    const ro = new ResizeObserver(() => m.invalidateSize());
    ro.observe(box.current);
    map.current = m;
    return () => { ro.disconnect(); m.remove(); map.current = marker.current = null; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    if (!point) { marker.current?.remove(); marker.current = null; return; }
    const at: L.LatLngTuple = [point.lat, point.lng];
    if (!marker.current) {
      marker.current = L.marker(at, { icon: PIN, draggable: !disabled }).addTo(m);
      const lift = (on: boolean) => () => marker.current?.getElement()?.classList.toggle('lift', on);
      marker.current.on('dragstart', lift(true));
      marker.current.on('dragend', lift(false));
      marker.current.on('dragend', () => { const ll = marker.current!.getLatLng(); cb.current({ lat: ll.lat, lng: ll.lng }); });
    } else marker.current.setLatLng(at);
    if (!m.getBounds().pad(-0.2).contains(at)) m.setView(at, Math.max(m.getZoom(), 16));
  }, [point?.lat, point?.lng]); // eslint-disable-line react-hooks/exhaustive-deps
  return <div ref={box} className="addr-map" />;
}

export function AddressField({ value, onChange, point, onPoint, onFacts, disabled }: {
  value: string; onChange: (v: string) => void; point: Point | null; onPoint: (p: Point | null) => void;
  /** after the agent picks / pastes / moves the point: fill district and metro (he can still change them) */
  onFacts?: (f: PointFacts) => void; disabled?: boolean;
}) {
  const [q, setQ] = useState('');
  const [open, setOpen] = useState(false);
  const [manual, setManual] = useState(false);  // «не тот»: the agent puts the point himself
  const picked = useRef(value);  // the text a pick wrote: no new search for it
  useEffect(() => {
    const t = setTimeout(() => { const s = value.trim(); if (s !== picked.current.trim()) { setQ(s); setOpen(true); } }, 500);
    return () => clearTimeout(t);
  }, [value]);
  const { data, isFetching } = useQuery({ queryKey: ['geo', q], queryFn: () => api.geo.search(q), enabled: q.length >= 3 && !disabled, staleTime: Infinity });
  const items = data?.items || [];
  const asked = useRef(0);  // only the latest point's answer counts (a marker dragged twice)
  const setPoint = (p: Point) => {
    onPoint(p);
    const n = ++asked.current;
    if (onFacts) api.geo.point(p).then((f) => { if (n === asked.current) onFacts(f); }).catch(() => undefined);
  };
  const pick = (h: GeoHit, keepText = false) => {
    setPoint({ lat: h.lat, lng: h.lng });
    if (!keepText) { picked.current = h.label; onChange(h.label); }
    setOpen(false);
    setManual(false);
  };
  // a pasted link / coordinates: that's the point; the link itself is no address, so OSM's name replaces it
  useEffect(() => { if (data?.point && items[0]) pick(items[0], !isLinkOrPoint(value)); }, [data]); // eslint-disable-line react-hooks/exhaustive-deps
  const searched = q.length >= 3 && !isFetching && !!data && q === value.trim();
  const notFound = searched && !items.length;
  const showMap = !!point || manual || notFound;
  return (
    <div className="col" style={{ gap: 6 }}>
      <Input value={value} disabled={disabled} placeholder="Улица и дом, или ссылка Google Maps"
        onChange={(e) => onChange(e.target.value)} onFocus={() => setOpen(true)} />
      {open && !data?.point && items.length > 0 && (
        <div className="card col" style={{ gap: 0, padding: 4 }}>
          <span className="faint" style={{ fontSize: 11, padding: '2px 8px' }}>Это тот адрес? Выберите:</span>
          {items.map((h) => (
            <div key={`${h.lat},${h.lng}`} className="list-row row gap-sm" style={{ padding: '6px 8px' }}>
              <button type="button" className="grow" style={{ textAlign: 'left', background: 'none', border: 0, cursor: 'pointer', padding: 0, color: 'inherit' }}
                onClick={() => pick(h)}>{h.label}</button>
              <button type="button" className="chip" title="Точка с карты, адрес оставить как написали" onClick={() => pick(h, true)}>точка</button>
            </div>
          ))}
          <button type="button" className="faint" style={{ textAlign: 'left', background: 'none', border: 0, cursor: 'pointer', padding: '6px 8px', fontSize: 13 }}
            onClick={() => { setOpen(false); setManual(true); }}>Не тот — поставлю точку на карте сам</button>
        </div>
      )}
      {point ? <span className="addr-state ok"><MapPin size={14} />Точка найдена — проверьте на карте</span>
        : notFound ? <span className="addr-state warn">{data?.error ? 'Поиск адресов сейчас недоступен' : 'Не нашли'} — уточните адрес или поставьте точку на карте</span>
          : manual ? <span className="addr-state">Нажмите на карте, где объект</span> : null}
      {showMap && <PointMap point={point} onPoint={setPoint} disabled={disabled} />}
      {point && !disabled && <span className="faint" style={{ fontSize: 12 }}>Перетащите точку, если она стоит не там</span>}
    </div>
  );
}
