// /m/* — Telegram Mini App views for agents (bot deep links, backend app/telegram/links.py:
// /m/leads/<id>, /m/properties/<id>, /m/deals/<id> (T0/T14), /m/deals/new?lead=<id>).
import { useState } from 'react';
import { Link, NavLink, Outlet, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ArrowLeft, Building2, CircleUser, ClipboardList, Handshake, Inbox, Sparkles } from 'lucide-react';
import type { ReportKind } from '../api/types';
import { Card, Empty, Seg } from '../components/ui';
import { DealForm } from '../features/deals/DealForm';
import { LeadBoard, MyLeadsList } from '../features/leads/LeadBoard';
import { BrandMark } from '../layout/AppLayout';
import { LeadDetail } from '../features/leads/LeadDetail';
import { NewListings, PropertyList } from '../features/properties/lists';
import { PropertyDetail } from '../features/properties/PropertyDetail';
import { KIND_LABEL, ReportForm, ReportsList } from '../features/reports/ReportForms';
import { CabinetView } from './Cabinet';
import { DealView } from './Deals';
import { ReportKindPicker } from './Reports';

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
  return (
    <div className="m-shell" style={{ minHeight: '100%' }}>
      <header className="mobile-top">
        {isDetail ? (
          <button className="btn btn-ghost icon-btn" onClick={() => (window.history.length > 1 ? navigate(-1) : navigate(loc.pathname.replace(/\/\d+$/, '')))} aria-label="Назад"><ArrowLeft size={20} /></button>
        ) : <span style={{ width: 32, height: 32, borderRadius: 9, background: '#fff', display: 'grid', placeItems: 'center', flex: 'none' }}><BrandMark size={20} /></span>}
        <h2 className="grow">{title}</h2>
        <NavLink to="/m/me" className="btn btn-ghost icon-btn" aria-label="Кабинет"><CircleUser size={20} /></NavLink>
      </header>
      <main className="content" style={{ paddingBottom: 'calc(92px + env(safe-area-inset-bottom))' }}>
        <Outlet />
      </main>
      <nav className="bottom-nav always">
        {TABS.map((t) => <NavLink key={t.to} to={t.to}><t.icon size={20} />{t.label}</NavLink>)}
      </nav>
    </div>
  );
}

export function MLeads() {
  const [view, setView] = useState<'board' | 'list'>('board');
  return (
    <div className="col">
      <Seg value={view} onChange={setView} options={[{ value: 'board', label: 'Доска' }, { value: 'list', label: 'Списком' }]} />
      {view === 'board' ? <LeadBoard base="/m" /> : <MyLeadsList base="/m" />}
    </div>
  );
}
export function MLead() { const { id } = useParams(); return <LeadDetail id={Number(id)} compact />; }
export function MProperties() {
  const [view, setView] = useState<'mine' | 'all' | 'archive'>('mine');
  return (
    <div className="col">
      <Seg value={view} onChange={setView} options={[{ value: 'mine', label: 'Мои объекты' }, { value: 'all', label: 'База объектов' }, { value: 'archive', label: 'Архив' }]} />
      <PropertyList key={view} mine={view === 'mine'} archived={view === 'archive'} base="/m" />
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
  return <DealForm key={leadId ?? 'new'} leadId={leadId} onDone={(d) => navigate(`/m/deals/${d.id}`, { replace: true })} />;
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
export function MReports() {
  const [kind, setKind] = useState<ReportKind>('makefoto');
  const [view, setView] = useState<'new' | 'list'>('new');
  return (
    <div className="col">
      <Seg value={view} onChange={setView} options={[{ value: 'new', label: 'Новый' }, { value: 'list', label: 'Журнал' }]} />
      {view === 'new' ? (
        <>
          <ReportKindPicker value={kind} onChange={setKind} />
          <Card label="Отчёт" title={KIND_LABEL[kind]}><ReportForm key={kind} kind={kind} /></Card>
        </>
      ) : <ReportsList />}
    </div>
  );
}
export function MMe() { return <CabinetView base="/m" />; }
