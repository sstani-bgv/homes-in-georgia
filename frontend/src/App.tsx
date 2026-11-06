import type { ReactNode } from 'react';
import { Link, Navigate, Route, Routes, useLocation, useParams } from 'react-router-dom';
import type { Role } from './api/types';
import { useAuth } from './auth/AuthContext';
import { Empty, Loading } from './components/ui';
import { AppLayout } from './layout/AppLayout';
import { AdminPage } from './pages/Admin';
import { CabinetPage } from './pages/Cabinet';
import { CalcPage } from './pages/Calc';
import { CalendarPage, MCalendar } from './pages/Calendar';
import { ContractsPage, MContracts } from './pages/Contracts';
import { DashboardPage } from './pages/Dashboard';
import { DealsPage } from './pages/Deals';
import { LeadPage, LeadsPage, ObjectsPage, PropertyPage } from './pages/Funnels';
import { LoginPage } from './pages/Login';
import { MDeal, MDealView, MLead, MLeads, MMe, MNew, MNotFound, MProperties, MProperty, MReports, MobileLayout } from './pages/Mobile';
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
  if (roles && !roles.includes(user.role)) return <Empty text="Раздел доступен только администраторам" />;
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
function MPropertyRedirect() {
  const { id } = useParams();
  return <Navigate to={`/m/properties/${id}`} replace />;
}
function NotFound() {
  return (
    <div className="col" style={{ alignItems: 'center' }}>
      <Empty text="Страница не найдена" />
      <Link to="/" className="btn btn-secondary">На главную</Link>
    </div>
  );
}

const STAFF: Role[] = ['superadmin', 'admin', 'operator'];

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/m" element={<RequireAuth><MobileLayout /></RequireAuth>}>
        <Route index element={<Navigate to="/m/leads" replace />} />
        <Route path="leads" element={<MLeads />} />
        <Route path="leads/:id" element={<MLead />} />
        <Route path="properties" element={<MProperties />} />
        <Route path="properties/:id" element={<MProperty />} />
        <Route path="new" element={<MNew />} />
        <Route path="deal" element={<MDeal />} />
        <Route path="deals" element={<Navigate to="/m/deal" replace />} />
        <Route path="deals/new" element={<MDeal />} />
        <Route path="deals/:id" element={<MDealView />} />
        <Route path="objects/:id" element={<MPropertyRedirect />} />
        <Route path="reports" element={<MReports />} />
        <Route path="me" element={<MMe />} />
        <Route path="contracts" element={<MContracts />} />
        <Route path="calendar" element={<MCalendar />} />
        <Route path="*" element={<MNotFound />} />
      </Route>
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
        <Route path="admin" element={<RequireAuth roles={['superadmin', 'admin']}><AdminPage /></RequireAuth>} />
        <Route path="me" element={<CabinetPage />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
