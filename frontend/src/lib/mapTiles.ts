import L from 'leaflet';
import { api } from '../api';

// MapTiler key comes from MAPTILER_KEY in .env via /api/config (one request per page load); empty = free OSM tiles.
let key: Promise<string> | null = null;

export function addBaseLayer(map: L.Map) {
  key ??= api.config().then((c) => c.maptiler_key || '').catch(() => '');
  key.then((k) => {
    if (!map.getContainer().isConnected) return; // map unmounted meanwhile
    (k
      ? L.tileLayer(`https://api.maptiler.com/maps/streets-v2/256/{z}/{x}/{y}{r}.png?key=${k}`, {
          maxZoom: 20,
          attribution: '<a href="https://www.maptiler.com/copyright/" target="_blank">© MapTiler</a> <a href="https://www.openstreetmap.org/copyright" target="_blank">© OpenStreetMap</a>',
        })
      : L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© OpenStreetMap' })
    ).addTo(map);
  });
}
