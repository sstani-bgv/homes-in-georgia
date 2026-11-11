// /m/* — Telegram Mini App views for agents (bot deep links, backend app/telegram/links.py:
// /m/leads/<id>, /m/properties/<id>, /m/deals/<id> (T0/T14), /m/deals/new?lead=<id>).
import { useState } from 'react';
import { Link, NavLink, Outlet, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ArrowLeft, Building2, CircleUser, ClipboardList, Handshake, Inbox, Plus, Sparkles } from 'lucide-react';
import type { ReportKind } from '../api/types';
import { Card, Empty, Seg } from '../components/ui';
import { DealForm } from '../features/deals/DealForm';
import { LeadStages } from '../features/leads/LeadBoard';
import { BrandMark, QuickAdd } from '../layout/AppLayout';
import { LeadDetail } from '../features/leads/LeadDetail';
import { MyProperties, NewListings, PropertyList } from '../features/properties/lists';
import { useAuth } from '../auth/AuthContext';
import { PropertyDetail } from '../features/properties/PropertyDetail';
import { KIND_LABEL, ReportForm, ReportsList } from '../features/reports/ReportForms';
import { CabinetView } from './Cabinet';
import { DealView } from './Deals';
import { ReportKindPicker } from './Reports';
import { ManualProperty } from './Funnels';
import { PropertyCheck } from './Tools';

const TABS = [
  { to: '/m/properties', label: 'Объекты', icon: Building2 },
  { to: '/m/leads', label: 'Заявки', icon: Inbox },
  { to: '/m/new', label: 'Новые', icon: Sparkles },
  { to: '/m/deal', label: 'Сделка', icon: Handshake },
  { to: '/m/reports', label: 'Отчёты', icon: ClipboardList },
];
const TITLES: [RegExp, string][] = [
  [/^\/m\/leads\/\d+/, 'Заявка'], [/^\/m\/leads/, 'Заявки'], [/^\/m\/properties\/\d+/, 'Объект'], [/^\/m\/properties/, 'Объекты'],
  [/^\/m\/new/, 'Новые объявления'], [/^\/m\/deals\/\d+/, 'Сделка'], [/^\/m\/deals\/new/, 'Новая сделка'], [/^\/m\/deal/, 'Сделка'], [/^\/m\/reports/, 'Отчёты'], [/^\/m\/me/, 'Кабинет'], [/^\/m\/contracts/, 'Договор'], [/^\/m\/calendar/, 'Календарь'],
];

export function MobileLayout() {
  const loc = useLocation();
  const navigate = useNavigate();
  const title = TITLES.find(([re]) => re.test(loc.pathname))?.[1] || 'Панель';
  const isDetail = /\/\d+$/.test(loc.pathname);
  const [add, setAdd] = useState(false);
  return (
    <div className="m-shell" style={{ minHeight: '100%' }}>
      <header className="mobile-top">
        {isDetail ? (
          <button className="btn btn-ghost icon-btn" onClick={() => (window.history.length > 1 ? navigate(-1) : navigate(loc.pathname.replace(/\/\d+$/, '')))} aria-label="Назад"><ArrowLeft size={20} /></button>
        ) : <span style={{ width: 32, height: 32, borderRadius: 9, background: '#fff', display: 'grid', placeItems: 'center', flex: 'none' }}><BrandMark size={20} /></span>}
        <h2 className="grow">{title}</h2>
        <button className="btn btn-ghost icon-btn" onClick={() => setAdd(true)} aria-label="Добавить"><Plus size={20} /></button>
        <NavLink to="/m/me" className="btn btn-ghost icon-btn" aria-label="Кабинет"><CircleUser size={20} /></NavLink>
      </header>
      <main className="content" style={{ paddingBottom: 'calc(92px + env(safe-area-inset-bottom))' }}>
        <Outlet />
      </main>
      <nav className="bottom-nav always">
        {TABS.map((t) => <NavLink key={t.to} to={t.to}><t.icon size={20} />{t.label}</NavLink>)}
      </nav>
      <QuickAdd open={add} onClose={() => setAdd(false)} mobile />
    </div>
  );
}

export function MLeads() {
  const [view, setView] = useState<'board' | 'list'>('board');
  return (
    <div className="col">
      <Seg value={view} onChange={setView} options={[{ value: 'board', label: 'Доска' }, { value: 'list', label: 'Списком' }]} />
      <LeadStages key={view} base="/m" list={view === 'list'} />
    </div>
  );
}
export function MLead() { const { id } = useParams(); return <LeadDetail id={Number(id)} compact />; }
export function MProperties() {
  const { isStaff } = useAuth();
  const [params, setParams] = useSearchParams();
  const [view, setView] = useState<'new' | 'mine' | 'all' | 'archive'>(isStaff ? 'mine' : 'new');
  return (
    <div className="col">
      <Seg value={view} onChange={setView} options={[{ value: 'new', label: 'Новые объявления' }, { value: 'mine', label: 'Мои объекты' },
        { value: 'all', label: 'База объектов' }, { value: 'archive', label: 'Архив' }]} />
      {view === 'new' ? <NewListings base="/m" />
        : view === 'mine' && !isStaff ? <MyProperties base="/m" /> : <PropertyList key={view} mine={view === 'mine'} archived={view === 'archive'} base="/m" />}
      <ManualProperty open={params.get('new') === '1'} onClose={() => setParams({}, { replace: true })} base="/m" />
    </div>
  );
}
export function MProperty() { const { id } = useParams(); return <PropertyDetail id={Number(id)} compact />; }
export function MNew() { return <NewListings base="/m" />; }
/** /m/deal and /m/deals/new?lead=<id> (bot link) — also accepts ?lead_id=. */
export function MDeal() {
  const [params] = useSearchParams();
  const leadId = Number(params.get('lead') || params.get('lead_id')) || undefined;
  const navigate = useNavigate();
  return <DealForm key={leadId ?? 'new'} leadId={leadId} onDone={(d) => navigate(leadId ? `/m/leads/${leadId}` : `/m/deals/${d.id}`, { replace: true })} />;
}
/** /m/deals/<id> — deal card with T0/T14 forms (bot buttons «T0: оценить сделку», «T14: оценить»). ?eval=t0|t14 opens the form. */
export function MDealView() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const ev = params.get('eval');
  const dealId = Number(id);
  if (!dealId) return <MNotFound />;
  return <DealView key={dealId} id={dealId} initialEval={ev === 't0' || ev === 't14' ? ev : null} onClose={() => navigate('/m/me', { replace: true })} />;
}
export function MNotFound() {
  return (
    <div className="col">
      <Empty text="Страница не найдена или ссылка устарела" />
      <Link to="/m/leads" className="btn btn-secondary btn-block">К моим заявкам</Link>
    </div>
  );
}
/** ?kind=<ReportKind> preselects the form, ?tab=check opens «Проверка объекта» («+ Добавить» sheet). */
export function MReports() {
  const [params] = useSearchParams();
  return <MReportsInner key={params.toString()} qKind={params.get('kind') as ReportKind | null} qTab={params.get('tab')} />;
}
function MReportsInner({ qKind, qTab }: { qKind: ReportKind | null; qTab: string | null }) {
  const [kind, setKind] = useState<ReportKind>(qKind || 'makefoto');
  const [view, setView] = useState<'new' | 'list' | 'check'>(qTab === 'check' ? 'check' : 'new');
  return (
    <div className="col">
      <Seg value={view} onChange={setView} options={[{ value: 'new', label: 'Новый' }, { value: 'list', label: 'Журнал' }, { value: 'check', label: 'Проверка' }]} />
      {view === 'check' ? <PropertyCheck /> : view === 'new' ? (
        <>
          <ReportKindPicker value={kind} onChange={setKind} />
          <Card label="Отчёт" title={KIND_LABEL[kind]}><ReportForm key={kind} kind={kind} /></Card>
        </>
      ) : <ReportsList />}
    </div>
  );
}
export function MMe() { return <CabinetView base="/m" />; }
