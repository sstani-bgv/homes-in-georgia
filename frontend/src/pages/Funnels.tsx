import { useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Plus } from 'lucide-react';
import { api } from '../api';
import { itemsOf } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { errText, useToast } from '../components/toast';
import { Button, Field, Input, Modal, PageHead, Select, Tabs } from '../components/ui';
import { LeadBoard, MyLeadsList } from '../features/leads/LeadBoard';
import { LeadDetail } from '../features/leads/LeadDetail';
import { NewListings, PropertyBoard, PropertyList } from '../features/properties/lists';
import { PropertyDetail } from '../features/properties/PropertyDetail';
import { DISTRICTS } from '../lib/format';
import { useNavigate } from 'react-router-dom';

type ObjTab = 'new' | 'board' | 'review' | 'mine' | 'all' | 'archive';

function ManualProperty({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [f, setF] = useState({ address: '', district: '', price: '', bedrooms: '', source_url: '' });
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const m = useMutation({
    mutationFn: () => api.properties.create({ address: f.address, district: f.district, price: Number(f.price) || null, bedrooms: Number(f.bedrooms) || null, source_url: f.source_url || null, source_type: 'direct' }),
    onSuccess: (p) => { qc.invalidateQueries({ queryKey: ['properties'] }); onClose(); navigate(`/objects/${p.id}`); },
    onError: (e) => toast(errText(e), true),
  });
  return (
    <Modal open={open} onClose={onClose} title="Объект вручную">
      <div className="form-grid">
        <Field label="Адрес" className="span2"><Input value={f.address} onChange={(e) => setF({ ...f, address: e.target.value })} /></Field>
        <Field label="Район"><Select value={f.district} onChange={(e) => setF({ ...f, district: e.target.value })} placeholder="—" options={DISTRICTS.map((d) => ({ value: d, label: d }))} /></Field>
        <Field label="Цена, $"><Input type="number" value={f.price} onChange={(e) => setF({ ...f, price: e.target.value })} /></Field>
        <Field label="Спальни"><Input type="number" value={f.bedrooms} onChange={(e) => setF({ ...f, bedrooms: e.target.value })} /></Field>
        <Field label="Ссылка"><Input value={f.source_url} onChange={(e) => setF({ ...f, source_url: e.target.value })} /></Field>
      </div>
      <Button className="mt-lg" disabled={!f.address} loading={m.isPending} onClick={() => m.mutate()}>Создать и взять в работу</Button>
    </Modal>
  );
}

export function ObjectsPage() {
  const { isStaff } = useAuth();
  const [params, setParams] = useSearchParams();
  const tab = (params.get('tab') as ObjTab) || (isStaff ? 'board' : 'new');
  const setTab = (t: ObjTab) => setParams({ tab: t }, { replace: true });
  const [manual, setManual] = useState(params.get('new') === '1');
  // «На публикацию»: objects the agents sent («Готово — отправить оператору»); same query as the dashboard's tasks
  const queue = useQuery({ queryKey: ['properties', 'list', 'review'], queryFn: () => api.properties.list({ status: 'review' }), enabled: isStaff });
  return (
    <>
      <PageHead title="Объекты" actions={<Button variant="secondary" onClick={() => setManual(true)}><Plus size={16} />Объект вручную</Button>} />
      <Tabs value={tab} onChange={setTab} tabs={[
        { value: 'new', label: 'Новые объявления' }, { value: 'board', label: 'В работе' },
        ...(isStaff ? [{ value: 'review' as const, label: 'На публикацию', count: queue.data ? itemsOf(queue.data).length : undefined }] : []),
        { value: 'mine', label: 'Мои объекты' }, { value: 'all', label: 'База объектов' }, { value: 'archive', label: 'Архив' },
      ]} />
      {tab === 'new' && <NewListings />}
      {tab === 'board' && <PropertyBoard />}
      {tab === 'review' && <PropertyList key="review" status="review" />}
      {tab === 'mine' && <PropertyList mine />}
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
  const [tab, setTab] = useState<'board' | 'mine'>(isStaff ? 'board' : 'mine');
  return (
    <>
      <PageHead title="Заявки" />
      <Tabs value={tab} onChange={setTab} tabs={[{ value: 'board', label: 'Доска' }, { value: 'mine', label: isStaff ? 'Списком' : 'Мои заявки' }]} />
      {tab === 'board' ? <LeadBoard /> : <div style={{ maxWidth: 640 }}><MyLeadsList /></div>}
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
