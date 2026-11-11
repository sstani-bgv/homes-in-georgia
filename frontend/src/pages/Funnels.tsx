import { useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Plus } from 'lucide-react';
import { api } from '../api';
import { itemsOf } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { AddressField, type Point } from '../components/AddressField';
import { errText, useToast } from '../components/toast';
import { Button, Field, Input, Modal, PageHead, Select, Tabs } from '../components/ui';
import { ArchivedLeads, LeadBoard, MyLeadsList } from '../features/leads/LeadBoard';
import { LeadDetail } from '../features/leads/LeadDetail';
import { MyProperties, NewListings, PropertyList, WorkProperties } from '../features/properties/lists';
import { PropertyDetail } from '../features/properties/PropertyDetail';
import { ChangeRequestQueue } from '../features/properties/requests';
import { DISTRICTS, phoneInput } from '../lib/format';
import { useNavigate } from 'react-router-dom';

type ObjTab = 'new' | 'board' | 'review' | 'requests' | 'mine' | 'all' | 'archive';

/** «Объект вручную»: the owner came to the office himself. His contact makes the object «Собственник» at once (no call
 * step); the card then opens the post generator, where the agent fills everything (address, price, photos...). */
function ManualProperty({ open, onClose }: { open: boolean; onClose: () => void }) {
  const empty = { owner_name: '', owner_phone: '', address: '', district: '', point: null as Point | null };
  const [f, setF] = useState(empty);
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const m = useMutation({
    mutationFn: () => api.properties.create({ address: f.address || null, district: f.district || null, lat: f.point?.lat ?? null, lng: f.point?.lng ?? null, source_type: 'direct', owner_name: f.owner_name, owner_phone: f.owner_phone }),
    onSuccess: (p) => { qc.invalidateQueries({ queryKey: ['properties'] }); onClose(); setF(empty); navigate(`/objects/${p.id}`); },
    onError: (e) => toast(errText(e), true),
  });
  return (
    <Modal open={open} onClose={onClose} title="Объект вручную">
      <div className="muted" style={{ fontSize: 13, marginBottom: 12 }}>Собственник пришёл сам: объект сразу в статусе «Собственник», дальше откроется пост-генератор — там цена, параметры и фото.</div>
      <div className="form-grid">
        <Field label="Имя собственника"><Input value={f.owner_name} onChange={(e) => setF({ ...f, owner_name: e.target.value })} /></Field>
        <Field label="Телефон собственника"><Input type="tel" inputMode="tel" placeholder="+995 5XX XX XX XX" value={f.owner_phone} onChange={(e) => setF({ ...f, owner_phone: phoneInput(e) })} /></Field>
        <div className="field span2"><span className="label">Адрес (можно позже)</span>
          <AddressField value={f.address} onChange={(address) => setF((x) => ({ ...x, address }))} point={f.point} onPoint={(point) => setF((x) => ({ ...x, point }))}
            onDistrict={(d) => setF((x) => (x.district ? x : { ...x, district: d }))} /></div>
        <Field label="Район"><Select value={f.district} onChange={(e) => setF({ ...f, district: e.target.value })} placeholder="—" options={DISTRICTS.map((d) => ({ value: d, label: d }))} /></Field>
      </div>
      <Button className="mt-lg" disabled={!f.owner_name.trim() || !f.owner_phone.trim()} loading={m.isPending} onClick={() => m.mutate()}>Создать и заполнить пост</Button>
    </Modal>
  );
}

export function ObjectsPage() {
  const { isStaff } = useAuth();
  const [params, setParams] = useSearchParams();
  const asked = params.get('tab') as ObjTab | null;
  // «На публикацию» / «Запросы» are the operators' queues: an agent's ?tab=requests falls back to his default tab
  const tab = asked && (isStaff || !['review', 'requests'].includes(asked)) ? asked : isStaff ? 'board' : 'new';
  const setTab = (t: ObjTab) => setParams({ tab: t }, { replace: true });
  const [manual, setManual] = useState(params.get('new') === '1');
  // «На публикацию»: objects the agents sent («Готово — отправить оператору»); same query as the dashboard's tasks
  const queue = useQuery({ queryKey: ['properties', 'list', 'review'], queryFn: () => api.properties.list({ status: 'review' }), enabled: isStaff });
  const requests = useQuery({ queryKey: ['change-requests', 'pending'], queryFn: () => api.changeRequests.list('pending'), enabled: isStaff });
  return (
    <>
      <PageHead title="Объекты" actions={<Button variant="secondary" onClick={() => setManual(true)}><Plus size={16} />Объект вручную</Button>} />
      <Tabs value={tab} onChange={setTab} tabs={[
        { value: 'new', label: 'Новые объявления' }, { value: 'board', label: 'В работе' },
        ...(isStaff ? [{ value: 'review' as const, label: 'На публикацию', count: queue.data ? itemsOf(queue.data).length : undefined },
          { value: 'requests' as const, label: 'Запросы', count: requests.data?.total }] : []),
        { value: 'mine', label: 'Мои объекты' }, { value: 'all', label: 'База объектов' }, { value: 'archive', label: 'Архив' },
      ]} />
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
      <Link to="/objects" className="row gap-sm label" style={{ marginBottom: 12, display: 'inline-flex' }}><ArrowLeft size={14} />Объекты</Link>
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
      {tab === 'board' ? <LeadBoard /> : tab === 'archive' ? <ArchivedLeads /> : <div style={{ maxWidth: 640 }}><MyLeadsList /></div>}
    </>
  );
}

export function LeadPage() {
  const { id } = useParams();
  return (
    <>
      <Link to="/leads" className="row gap-sm label" style={{ marginBottom: 12, display: 'inline-flex' }}><ArrowLeft size={14} />Заявки</Link>
      <LeadDetail id={Number(id)} />
    </>
  );
}
