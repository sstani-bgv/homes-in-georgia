// Old CRM tools that had no React screen yet:
//  · «Property Check» (reports → openCheckProperty) — owner/property duplicate search via POST /api/properties/check-duplicate.
import { useState } from 'react';
import { Search } from 'lucide-react';
import { api } from '../api';
import { errText, useToast } from '../components/toast';
import { Badge, Button, Card, Field, Input } from '../components/ui';
import { fmtDate, phoneInput } from '../lib/format';

type DupRow = { source?: string; owner?: string | null; phone?: string | null; address?: string | null; link?: string | null; status?: string; rent_to?: string | null;
  phone_match?: boolean; addr_match?: boolean; url_match?: boolean; cadastral_match?: boolean; property_id?: number; deal_id?: number; is_agent?: boolean };

const LEVEL: Record<string, { tone: 'ok' | 'warn' | 'red'; title: string; sub: string }> = {
  none: { tone: 'ok', title: 'Не найдено в базе', sub: 'Совпадений по собственнику или объекту нет, можно продолжать' },
  warn: { tone: 'warn', title: 'Возможное совпадение', sub: 'Совпало одно поле, проверьте внимательно' },
  danger: { tone: 'red', title: 'Уже есть в базе', sub: 'Совпали телефон и адрес, ссылка или кадастровый код' },
};

export function PropertyCheck() {
  const toast = useToast();
  const [f, setF] = useState({ phone: '', address: '', url: '', cadastral: '' });
  const [res, setRes] = useState<{ level: string; results: DupRow[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const empty = !Object.values(f).some((v) => v.trim());
  const run = async () => {
    setBusy(true);
    try {
      const r = await api.properties.checkDuplicate(Object.fromEntries(Object.entries(f).filter(([, v]) => v.trim())));
      setRes({ level: String(r.level || 'none'), results: (r.results as DupRow[]) || [] });
    } catch (e) { toast(errText(e), true); } finally { setBusy(false); }
  };
  const L = res ? LEVEL[res.level] || LEVEL.none : null;
  return (
    <div className="split">
      <Card label="База собственников" title="Проверка объекта">
        <div className="form-grid">
          <Field label="Телефон собственника" hint="Любой формат, с кодом страны или без"><Input value={f.phone} onChange={(e) => setF({ ...f, phone: phoneInput(e) })} inputMode="tel" /></Field>
          <Field label="Адрес объекта" hint="Улица и номер, частичное совпадение работает"><Input value={f.address} onChange={(e) => setF({ ...f, address: e.target.value })} /></Field>
          <Field label="Ссылка на объявление"><Input value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} /></Field>
          <Field label="Кадастровый код"><Input value={f.cadastral} onChange={(e) => setF({ ...f, cadastral: e.target.value })} /></Field>
        </div>
        <div className="row mt">
          <Button disabled={empty} loading={busy} onClick={run}><Search size={16} />Искать в базе</Button>
          {res && <Button variant="ghost" onClick={() => { setRes(null); setF({ phone: '', address: '', url: '', cadastral: '' }); }}>Очистить</Button>}
        </div>
        {L && (
          <div className="mt-lg col">
            <div className={`alert ${L.tone === 'ok' ? 'alert-ok' : L.tone === 'red' ? 'alert-warn' : 'alert-info'}`}><div><b>{L.title}</b><div style={{ fontWeight: 500 }}>{L.sub}</div></div></div>
            {res!.results.map((r, i) => (
              <div key={i} className="outcome-card col gap-sm">
                <div className="row row-wrap gap-sm">
                  <Badge tone="grey">{r.source === 'owners' ? 'Собственник' : r.source === 'deals' ? 'Сделка' : 'Объект'}</Badge>
                  {r.phone_match && <Badge tone="redsoft">телефон</Badge>}{r.addr_match && <Badge tone="redsoft">адрес</Badge>}
                  {r.url_match && <Badge tone="redsoft">ссылка</Badge>}{r.cadastral_match && <Badge tone="redsoft">кадастр</Badge>}
                  {r.is_agent && <Badge tone="warn">агент</Badge>}
                </div>
                {r.owner && <b>{r.owner}</b>}
                <div className="muted" style={{ fontSize: 13 }}>{[r.phone, r.address].filter(Boolean).join(' · ') || '—'}</div>
                {r.rent_to && <div className="muted" style={{ fontSize: 12 }}>Сдан до {fmtDate(r.rent_to)}</div>}
                <div className="row gap-sm">
                  {r.property_id && <a href={`/objects/${r.property_id}`}>Объект #{r.property_id}</a>}
                  {r.deal_id && <a href={`/deals?open=${r.deal_id}`}>Сделка #{r.deal_id}</a>}
                  {r.link && <a href={r.link} target="_blank" rel="noopener noreferrer">Объявление</a>}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
      <Card label="Как это работает" title="Уровни совпадения">
        <div className="col" style={{ fontSize: 13 }}>
          <div>Введите телефон или адрес, для точного результата — оба поля.</div>
          <div><b>Возможное совпадение</b> — совпало одно поле, проверьте внимательно.</div>
          <div><b>Уже есть в базе</b> — совпали телефон и адрес, ссылка или кадастровый код.</div>
          <div><b>Не найдено</b> — объект можно брать в работу.</div>
        </div>
      </Card>
    </div>
  );
}
