import type { ReactNode } from 'react';
import { Link, Navigate, Route, Routes, useLocation, useParams } from 'react-router-dom';
import type { Role } from './api/types';
import { ADMIN_ROLES, STAFF_ROLES, SUPER_ROLES } from './api/types';
import { useAuth } from './auth/AuthContext';
import { Empty, Loading } from './components/ui';
import { AppLayout } from './layout/AppLayout';
import { AdminPage } from './pages/Admin';
import { AnalyticsPage } from './pages/Analytics';
import { CabinetPage } from './pages/Cabinet';
import { CalcPage } from './pages/Calc';
import { CalendarPage } from './pages/Calendar';
import { ContractsPage } from './pages/Contracts';
import { DashboardPage } from './pages/Dashboard';
import { DealsPage } from './pages/Deals';
import { ExclusiveSignPage } from './pages/ExclusiveSign';
import { InvitePage } from './pages/Invite';
import { ESignPage } from './pages/ESign';
import { LeadPage, LeadsPage, ObjectsPage, PropertyPage } from './pages/Funnels';
import { LoginPage } from './pages/Login';
import { ClientPage, ClientsPage, OwnersPage } from './pages/People';
import { ReportsPage } from './pages/Reports';
import { TeamPage } from './pages/Team';

function RequireAuth({ children, roles }: { children: ReactNode; roles?: Role[] }) {
  const { user, loading, error } = useAuth();
  const loc = useLocation();
  if (loading) return <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center' }}><Loading /></div>;
  if (!user) {
    const next = encodeURIComponent(loc.pathname + loc.search);
    return <Navigate to={`/login?next=${next}`} replace state={{ error }} />;
  }
  if (roles && !roles.includes(user.role)) return <Empty text={roles.includes('admin') || roles.includes('operator') ? 'Раздел доступен только администраторам' : 'Раздел доступен только суперадминам'} />;
  return <>{children}</>;
}

/** /deals/<id> → the deals page with the drawer open (same as ?open=<id>). */
function DealRedirect() {
  const { id } = useParams();
  return <Navigate to={`/deals?open=${Number(id) || ''}`} replace />;
}
function PropertyRedirect() {
  const { id } = useParams();
  return <Navigate to={`/objects/${id}`} replace />;
}
/** Old Mini App links (/m/..., still on buttons in Telegram chats) → the same page of the main site. */
function mPath(path: string, search: string): string {
  const p = path.replace(/^\/m\/?/, '').replace(/\/$/, '');
  const id = p.match(/^(?:properties|objects|deals)\/(\d+)$/)?.[1];
  if (/^(properties|objects)\/\d+$/.test(p)) return `/objects/${id}${search}`;
  if (/^deals\/\d+$/.test(p)) return `/deals?open=${id}`;
  if (p === 'new') return '/objects?tab=new';
  if (p === 'properties') return `/objects${search}`;
  if (p === 'deal' || p === 'deals') return `/deals/new${search}`;
  return `/${p}${search}`;  // '' (panel) → dashboard; leads, leads/:id, deals/new, reports, me, contracts, calendar as is
}
function MRedirect() {
  const loc = useLocation();
  return <Navigate to={mPath(loc.pathname, loc.search)} replace />;
}
function NotFound() {
  return (
    <div className="col" style={{ alignItems: 'center' }}>
      <Empty text="Страница не найдена" />
      <Link to="/" className="btn btn-secondary">На главную</Link>
    </div>
  );
}

const STAFF = STAFF_ROLES;

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/sign/:token" element={<ExclusiveSignPage />} />
      <Route path="/esign/:token" element={<ESignPage />} />
      <Route path="/invite/:token" element={<InvitePage />} />
      <Route path="/m/*" element={<MRedirect />} />
      <Route element={<RequireAuth><AppLayout /></RequireAuth>}>
        <Route index element={<DashboardPage />} />
        <Route path="objects" element={<ObjectsPage />} />
        <Route path="objects/:id" element={<PropertyPage />} />
        <Route path="leads" element={<LeadsPage />} />
        <Route path="leads/:id" element={<LeadPage />} />
        <Route path="deals" element={<DealsPage />} />
        <Route path="deals/new" element={<DealsPage newDeal />} />
        <Route path="deals/:id" element={<DealRedirect />} />
        <Route path="properties/:id" element={<PropertyRedirect />} />
        <Route path="clients" element={<RequireAuth roles={STAFF}><ClientsPage /></RequireAuth>} />
        <Route path="clients/:id" element={<RequireAuth roles={STAFF}><ClientPage /></RequireAuth>} />
        <Route path="owners" element={<RequireAuth roles={STAFF}><OwnersPage /></RequireAuth>} />
        <Route path="team" element={<TeamPage />} />
        <Route path="contracts" element={<ContractsPage />} />
        <Route path="calendar" element={<CalendarPage />} />
        <Route path="calc" element={<CalcPage />} />
        <Route path="posts" element={<Navigate to="/objects?tab=review" replace />} />
        <Route path="reports" element={<ReportsPage />} />
        <Route path="analytics" element={<RequireAuth roles={SUPER_ROLES}><AnalyticsPage /></RequireAuth>} />
        <Route path="admin" element={<RequireAuth roles={ADMIN_ROLES}><AdminPage /></RequireAuth>} />
        <Route path="me" element={<CabinetPage />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
