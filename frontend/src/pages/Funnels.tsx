import { useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Briefcase, Database, Inbox, Plus } from 'lucide-react';
import { BackLink } from '../layout/back';
import { api } from '../api';
import { itemsOf } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { AddressField, type Point, type PointFacts } from '../components/AddressField';
import { errText, useToast } from '../components/toast';
import { Button, Field, Input, Modal, PageHead, Tabs, DistrictSelect } from '../components/ui';
import type { ReactNode } from 'react';
import { ArchivedLeads, LeadStages } from '../features/leads/LeadBoard';
import { LeadDetail } from '../features/leads/LeadDetail';
import { MyProperties, NewListings, PropertyList, WorkProperties } from '../features/properties/lists';
import { metroLabel, PropertyDetail } from '../features/properties/PropertyDetail';
import { ChangeRequestQueue } from '../features/properties/requests';
import { distLabel, phoneInput } from '../lib/format';
import { useNavigate } from 'react-router-dom';

type ObjTab = 'new' | 'board' | 'review' | 'requests' | 'mine' | 'all' | 'archive';

/** «Объект вручную»: the owner came to the office himself. His contact makes the object «Собственник» at once (no call
 * step); the card then opens the post generator, where the agent fills everything (address, price, photos...). */
export function ManualProperty({ open, onClose, base = '' }: { open: boolean; onClose: () => void; base?: string }) {
  const empty = { owner_name: '', owner_phone: '', address: '', district: '', point: null as Point | null, facts: null as PointFacts | null };
  const [f, setF] = useState(empty);
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const m = useMutation({
    mutationFn: () => api.properties.create({ address: f.address || null, district: f.district || null, lat: f.point?.lat ?? null, lng: f.point?.lng ?? null, ...(f.facts?.metro ? { post_fields: { metro: f.facts.metro } } : {}), source_type: 'direct', owner_name: f.owner_name, owner_phone: f.owner_phone }),
    onSuccess: (p) => { qc.invalidateQueries({ queryKey: ['properties'] }); onClose(); setF(empty); navigate(`${base}/objects/${p.id}`); },
    onError: (e) => toast(errText(e), true),
  });
  return (
    <Modal open={open} onClose={onClose} title="Объект вручную">
      <div className="muted" style={{ fontSize: 13, marginBottom: 12 }}>Собственник пришёл сам: объект сразу в статусе «Собственник», дальше откроется пост-генератор — там цена, параметры и фото.</div>
      <div className="form-grid">
        <Field label="Имя собственника"><Input value={f.owner_name} onChange={(e) => setF({ ...f, owner_name: e.target.value })} /></Field>
        <Field label="Телефон собственника"><Input type="tel" inputMode="tel" placeholder="+995 5XX XXX XXX" value={f.owner_phone} onChange={(e) => setF({ ...f, owner_phone: phoneInput(e) })} /></Field>
        <div className="field span2"><span className="label">Адрес (можно позже)</span>
          <AddressField value={f.address} onChange={(address) => setF((x) => ({ ...x, address }))} point={f.point} onPoint={(point) => setF((x) => ({ ...x, point }))}
            onFacts={(facts) => setF((x) => ({ ...x, facts, district: facts.district || x.district }))} />
          {f.facts && <span className="faint" style={{ fontSize: 13 }}>🚇 {f.facts.metro ? `${metroLabel(f.facts.metro)} · ≈ ${distLabel(f.facts.metro_m ?? 0)}` : 'метро не найдено (точка за городом)'}</span>}</div>
        <Field label="Район"><DistrictSelect value={f.district} onChange={(district) => setF({ ...f, district })} /></Field>
      </div>
      <Button className="mt-lg" disabled={!f.owner_name.trim() || !f.owner_phone.trim()} loading={m.isPending} onClick={() => m.mutate()}>Создать и заполнить пост</Button>
    </Modal>
  );
}

/** «Объекты» tabs in three groups (Stanislav 11-13 mock-up): Входящие (new ads, operators' requests), Работа (the board,
 * the publication queue), База (mine, the whole base, the archive). */
const OBJ_GROUPS: { key: string; label: string; icon: ReactNode; tabs: ObjTab[] }[] = [
  { key: 'in', label: 'Входящие', icon: <Inbox size={20} />, tabs: ['new', 'requests'] },
  { key: 'work', label: 'Работа', icon: <Briefcase size={20} />, tabs: ['board', 'review'] },
  { key: 'base', label: 'База', icon: <Database size={20} />, tabs: ['mine', 'all', 'archive'] },
];
const OBJ_TAB: Record<ObjTab, string> = { new: 'Новые объявления', requests: 'Запросы', board: 'В работе', review: 'На публикацию', mine: 'Мои объекты', all: 'База объектов', archive: 'Архив' };

export function ObjectsPage() {
  const { isStaff } = useAuth();
  const [params, setParams] = useSearchParams();
  const asked = params.get('tab') as ObjTab | null;
  // «На публикацию» / «Запросы» are the operators' queues: an agent's ?tab=requests falls back to his default tab
  const allowed = (t: ObjTab) => isStaff || !['review', 'requests'].includes(t);
  const tab = asked && OBJ_TAB[asked] && allowed(asked) ? asked : isStaff ? 'board' : 'new';
  const setTab = (t: ObjTab) => setParams({ tab: t }, { replace: true });
  const [manual, setManual] = useState(params.get('new') === '1');
  // «На публикацию»: objects the agents sent («Готово — отправить оператору»); same query as the dashboard's tasks
  const queue = useQuery({ queryKey: ['properties', 'list', 'review'], queryFn: () => api.properties.list({ status: 'review' }), enabled: isStaff });
  const requests = useQuery({ queryKey: ['change-requests', 'pending'], queryFn: () => api.changeRequests.list('pending'), enabled: isStaff });
  const fresh = useQuery({ queryKey: ['listings', 'new', 'count'], queryFn: () => api.listings.new({}, 1), staleTime: 60_000 });
  const count: Partial<Record<ObjTab, number | undefined>> = {
    new: fresh.data?.total, review: queue.data ? itemsOf(queue.data).length : undefined, requests: requests.data?.total,
  };
  const groups = OBJ_GROUPS.map((g) => ({ ...g, tabs: g.tabs.filter(allowed) }));
  const group = groups.find((g) => g.tabs.includes(tab))!;
  const badge = (t: ObjTab) => count[t] !== undefined && <span className={`tab-count${t === 'new' && count[t] ? ' hot' : ''}`}>{count[t]}</span>;
  return (
    <>
      <div className="obj-head">
        <div className="row row-between">
          <h1>Объекты</h1>
          <button type="button" className="btn obj-head-btn" onClick={() => setManual(true)}><Plus size={16} />Объект вручную</button>
        </div>
        <div className="obj-groups" role="tablist">
          {groups.map((g) => (
            <button key={g.key} type="button" role="tab" aria-selected={g === group} className={g === group ? 'active' : ''} onClick={() => setTab(g.tabs[0])}>
              {g.icon}<span>{g.label}</span>
            </button>
          ))}
        </div>
      </div>
      <div className="obj-sub">
        <div className="subtabs" role="tablist">
          {group.tabs.map((t) => (
            <button key={t} type="button" role="tab" aria-selected={t === tab} className={t === tab ? 'active' : ''} onClick={() => setTab(t)}>{OBJ_TAB[t]}{badge(t)}</button>
          ))}
        </div>
      </div>
      {tab === 'new' && <NewListings />}
      {tab === 'board' && <WorkProperties />}
      {tab === 'review' && <PropertyList key="review" status="review" />}
      {tab === 'requests' && <ChangeRequestQueue />}
      {tab === 'mine' && (isStaff ? <PropertyList mine /> : <MyProperties />)}
      {tab === 'all' && <PropertyList key="all" />}
      {tab === 'archive' && <PropertyList key="archive" archived />}
      <ManualProperty open={manual} onClose={() => setManual(false)} />
    </>
  );
}

export function PropertyPage() {
  const { id } = useParams();
  return (
    <>
      <BackLink to="/objects">Объекты</BackLink>
      <PropertyDetail id={Number(id)} />
    </>
  );
}

export function LeadsPage() {
  const { isStaff } = useAuth();
  const [tab, setTab] = useState<'board' | 'mine' | 'archive'>(isStaff ? 'board' : 'mine');
  return (
    <>
      <PageHead title="Заявки" />
      <Tabs value={tab} onChange={setTab} tabs={[{ value: 'board', label: 'Доска' }, { value: 'mine', label: isStaff ? 'Списком' : 'Мои заявки' }, { value: 'archive', label: 'Архив' }]} />
      {tab === 'archive' ? <ArchivedLeads /> : <LeadStages key={tab} list={tab === 'mine'} />}
    </>
  );
}

export function LeadPage() {
  const { id } = useParams();
  return (
    <>
      <BackLink to="/leads">Заявки</BackLink>
      <LeadDetail id={Number(id)} />
    </>
  );
}
