import { useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Briefcase, Database, Inbox, Plus } from 'lucide-react';
import { BackLink } from '../layout/back';
import { api } from '../api';
import { ApiError, itemsOf } from '../api/client';
import { useConfirm } from '../components/ActionDialog';
import { useAuth } from '../auth/AuthContext';
import { AddressField, type Point, type PointFacts } from '../components/AddressField';
import { errText, useToast } from '../components/toast';
import { Button, Field, Input, Modal, PageHead, Tabs, DistrictSelect } from '../components/ui';
import type { ReactNode } from 'react';
import { ArchivedLeads, LeadStages } from '../features/leads/LeadBoard';
import { LeadDetail } from '../features/leads/LeadDetail';
import { CompetitorListings, DealContext, MyProperties, NewListings, PropertyList, WorkProperties, type Deal } from '../features/properties/lists';
import { metroLabel, PropertyDetail, takenObjects } from '../features/properties/PropertyDetail';
import { ChangeRequestQueue } from '../features/properties/requests';
import { distLabel } from '../lib/format';
import { cleanPhones, PhonesField, phoneRows } from '../components/PhonesField';
import { useNavigate } from 'react-router-dom';
import { t } from '../lib/i18n';

type ObjTab = 'new' | 'board' | 'review' | 'requests' | 'mine' | 'all' | 'archive' | 'competitors';

/** «Объект вручную»: the owner came to the office himself. His contact makes the object «Собственник» at once (no call
 * step); the card then opens the post generator, where the agent fills everything (address, price, photos...). */
export function ManualProperty({ open, onClose, base = '', deal = 'rent' }: { open: boolean; onClose: () => void; base?: string; deal?: Deal }) {
  const empty = { owner_name: '', phones: phoneRows(), address: '', district: '', point: null as Point | null, facts: null as PointFacts | null };
  const [f, setF] = useState(empty);
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const confirm = useConfirm();
  const body = (): Parameters<typeof api.properties.create>[0] => ({ address: f.address || null, district: f.district || null, lat: f.point?.lat ?? null, lng: f.point?.lng ?? null, post_fields: { ...(f.facts?.metro ? { metro: f.facts.metro } : {}), ...(deal === 'sale' ? { deal: 'Sale', prop_type: 'Apartment' } : {}) }, source_type: 'direct' });
  const done = (p: { id: number }) => { qc.invalidateQueries({ queryKey: ['properties'] }); onClose(); setF(empty); navigate(`${base}/objects/${p.id}`); };
  const m = useMutation({
    mutationFn: () => api.properties.create({ ...body(), owner_name: f.owner_name, owner_phone: cleanPhones(f.phones)[0]?.phone, owner_phones: cleanPhones(f.phones) }),
    onSuccess: done,
    // Irakli 11-24: «Собственник закреплён за #David» here too — the object is made without the owner and the operator
    // gets the same request as in the card (move the owner to this agent and link him; his agent is told)
    onError: async (e) => {
      const phone = cleanPhones(f.phones)[0]?.phone;
      if (!(e instanceof ApiError && e.code === 'owner_taken' && phone)) return toast(errText(e), true);
      // manual-object-owner-taken 11-28: an agent read this as «дубль, отказ» and cancelled — say it isn't one
      if (!(await confirm(`${e.message}.${takenObjects(e)} ${t('Это не дубль и не отказ: объект создастся, а оператору уйдёт запрос передать собственника вам. Агента собственника тоже уведомим.')}`, t('Создать и отправить запрос')))) return;
      try {
        const p = await api.properties.create(body());
        try { await api.properties.ownerRequest(p.id, phone); toast(t('Запрос ушёл оператору')); } catch (x) { toast(errText(x), true); }
        done(p);
      } catch (x) { toast(errText(x), true); }
    },
  });
  return (
    <Modal open={open} onClose={onClose} title={deal === 'sale' ? t('Объект на продажу') : t('Объект вручную')}>
      <div className="muted" style={{ fontSize: 13, marginBottom: 12 }}>{t('Собственник пришёл сам: объект сразу в статусе «Собственник», дальше откроется пост-генератор — там цена, параметры и фото.')}</div>
      <div className="form-grid">
        <Field label={t('Имя собственника')}><Input value={f.owner_name} onChange={(e) => setF({ ...f, owner_name: e.target.value })} /></Field>
        <PhonesField label={t('Телефон собственника')} value={f.phones} onChange={(phones) => setF({ ...f, phones })} />
        <div className="field span2"><span className="label">{t('Адрес (можно позже)')}</span>
          <AddressField value={f.address} onChange={(address) => setF((x) => ({ ...x, address }))} point={f.point} onPoint={(point) => setF((x) => ({ ...x, point }))}
            onFacts={(facts) => setF((x) => ({ ...x, facts, district: facts.district || x.district }))} />
          {f.facts && <span className="faint" style={{ fontSize: 13 }}>🚇 {f.facts.metro ? `${metroLabel(f.facts.metro)} · ≈ ${distLabel(f.facts.metro_m ?? 0)}` : t('метро не найдено (точка за городом)')}</span>}</div>
        <Field label={t('Район')}><DistrictSelect value={f.district} onChange={(district) => setF({ ...f, district })} /></Field>
      </div>
      <Button className="mt-lg" disabled={!f.owner_name.trim() || !cleanPhones(f.phones).length} loading={m.isPending} onClick={() => m.mutate()}>{t('Создать и заполнить пост')}</Button>
    </Modal>
  );
}

/** «Объекты» tabs in three groups (Stanislav 11-13 mock-up): Входящие (new ads, operators' requests, competitors' ads), Работа (the board,
 * the publication queue), База (mine, the whole base, the archive). */
const OBJ_GROUPS: { key: string; label: string; icon: ReactNode; tabs: ObjTab[] }[] = [
  { key: 'in', label: t('Входящие'), icon: <Inbox size={20} />, tabs: ['new', 'requests', 'competitors'] },
  { key: 'work', label: t('Работа'), icon: <Briefcase size={20} />, tabs: ['board', 'review'] },
  { key: 'base', label: t('База'), icon: <Database size={20} />, tabs: ['mine', 'all', 'archive'] },
];
const OBJ_TAB: Record<ObjTab, string> = { new: t('Новые объявления'), requests: t('Запросы'), board: t('В работе'), review: t('На публикацию'), mine: t('Мои объекты'), all: t('База объектов'), archive: t('Архив'), competitors: t('Конкуренты') };

export function ObjectsPage() {
  const { isStaff } = useAuth();
  const [params, setParams] = useSearchParams();
  const asked = params.get('tab') as ObjTab | null;
  // Аренда / Продажа (Stanislav 11-15): each with its own feed, board, base and archive; kept in the URL (?deal=sale)
  const deal: Deal = params.get('deal') === 'sale' ? 'sale' : 'rent';
  // «На публикацию» / «Запросы» are the operators' queues: an agent's ?tab=requests falls back to his default tab;
  // «Конкуренты» is rent only for now: sale ads are not collected (Stanislav 11-16)
  const allowed = (t: ObjTab) => (isStaff || !['review', 'requests'].includes(t)) && !(deal === 'sale' && t === 'competitors');
  const tab = asked && OBJ_TAB[asked] && allowed(asked) ? asked : isStaff ? 'board' : 'new';
  const setTab = (t: ObjTab) => setParams({ tab: t, ...(deal === 'sale' ? { deal } : {}) }, { replace: true });
  const setDeal = (d: Deal) => setParams({ tab, ...(d === 'sale' ? { deal: d } : {}) }, { replace: true });
  const [manual, setManual] = useState(params.get('new') === '1');
  // «На публикацию»: objects the agents sent («Готово — отправить оператору»); same query as the dashboard's tasks
  const queue = useQuery({ queryKey: ['properties', 'list', 'review', deal], queryFn: () => api.properties.list({ status: 'review', deal }), enabled: isStaff });
  const requests = useQuery({ queryKey: ['change-requests', 'pending'], queryFn: () => api.changeRequests.list('pending'), enabled: isStaff });
  const fresh = useQuery({ queryKey: ['listings', 'new', 'count'], queryFn: () => api.listings.new({}, 1), staleTime: 60_000 });
  const rivals = useQuery({ queryKey: ['listings', 'rival', 'count'], queryFn: () => api.listings.new({ competitor: '1' }, 1), staleTime: 60_000, enabled: deal === 'rent' });
  const count: Partial<Record<ObjTab, number | undefined>> = {
    new: deal === 'sale' ? undefined : fresh.data?.total, review: queue.data ? itemsOf(queue.data).length : undefined, requests: requests.data?.total, competitors: rivals.data?.total,
  };
  const groups = OBJ_GROUPS.map((g) => ({ ...g, tabs: g.tabs.filter(allowed) }));
  const group = groups.find((g) => g.tabs.includes(tab))!;
  const badge = (t: ObjTab) => count[t] !== undefined && <span className={`tab-count${t === 'new' && count[t] ? ' hot' : ''}`}>{count[t]}</span>;
  return (
    <DealContext.Provider value={deal}>
      <div className="obj-head">
        <div className="row row-between row-wrap gap-sm">
          <div className="row gap row-wrap">
            <h1>{t('Объекты')}</h1>
            <div className="obj-deal" role="tablist" aria-label={t('Аренда или продажа')}>
              {([['rent', t('Аренда')], ['sale', t('Продажа')]] as [Deal, string][]).map(([d, l]) => (
                <button key={d} type="button" role="tab" aria-selected={deal === d} className={deal === d ? 'active' : ''} onClick={() => setDeal(d)}>{l}</button>
              ))}
            </div>
          </div>
          <button type="button" className="btn obj-head-btn" onClick={() => setManual(true)}><Plus size={16} />{t('Объект вручную')}</button>
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
            <button key={t} type="button" role="tab" aria-selected={t === tab} className={t === tab ? 'active' : ''} ref={t === tab ? (el) => el?.scrollIntoView({ inline: 'nearest', block: 'nearest' }) : undefined} onClick={() => setTab(t)}>{OBJ_TAB[t]}{badge(t)}</button>
          ))}
        </div>
      </div>
      {tab === 'new' && <NewListings />}
      {tab === 'board' && <WorkProperties />}
      {tab === 'review' && <PropertyList key={`review-${deal}`} status="review" />}
      {tab === 'requests' && <ChangeRequestQueue />}
      {tab === 'mine' && (isStaff ? <PropertyList key={`mine-${deal}`} mine /> : <MyProperties key={deal} />)}
      {tab === 'all' && <PropertyList key={`all-${deal}`} />}
      {tab === 'archive' && <PropertyList key={`archive-${deal}`} archived />}
      {tab === 'competitors' && <CompetitorListings />}
      <ManualProperty open={manual} onClose={() => setManual(false)} deal={deal} />
    </DealContext.Provider>
  );
}

export function PropertyPage() {
  const { id } = useParams();
  return (
    <>
      <BackLink to="/objects">{t('Объекты')}</BackLink>
      <PropertyDetail id={Number(id)} />
    </>
  );
}

export function LeadsPage() {
  const { isStaff } = useAuth();
  const [tab, setTab] = useState<'board' | 'mine' | 'archive'>(isStaff ? 'board' : 'mine');
  return (
    <>
      <PageHead title={t('Заявки')} />
      <Tabs value={tab} onChange={setTab} tabs={[{ value: 'board', label: t('Доска') }, { value: 'mine', label: isStaff ? t('Списком') : t('Мои заявки') }, { value: 'archive', label: t('Архив') }]} />
      {tab === 'archive' ? <ArchivedLeads /> : <LeadStages key={tab} list={tab === 'mine'} />}
    </>
  );
}

export function LeadPage() {
  const { id } = useParams();
  return (
    <>
      <BackLink to="/leads">{t('Заявки')}</BackLink>
      <LeadDetail id={Number(id)} />
    </>
  );
}
