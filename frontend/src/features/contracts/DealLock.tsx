import type { ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { Handshake, LogOut } from 'lucide-react';
import { api } from '../../api';
import { atLeast } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { Button } from '../../components/ui';
import { errText, useToast } from '../../components/toast';
import { t } from '../../lib/i18n';

/** The deal form, the contracts and the profile stay open while the deal is overdue (the backend allows the same). */
const FREE = ['/deals/new', '/contracts', '/me'];

/** David 11-23: a contract signed by both parties more than 24 h ago without a deal — the agent's CRM opens only the
 *  deal form for it (backend: contracts.overdue_for, deps.deal_lock → 423 deal_overdue). Staff are never locked. */
export function DealLock({ children }: { children: ReactNode }) {
  const { user, signOut } = useAuth();
  const loc = useLocation();
  const navigate = useNavigate();
  const toast = useToast();
  const qc = useQueryClient();
  const staff = atLeast(user, 'operator');
  const me = useQuery({ queryKey: ['me', 'deal-lock', loc.pathname], queryFn: () => api.auth.me(), enabled: !!user && !staff, staleTime: 0 });
  const c = me.data?.deal_overdue;
  const open = useMutation({
    mutationFn: () => api.contracts.dealLead(c!.id),
    onSuccess: (x) => {
      if (x.lead_deal_id || !x.lead_id) qc.invalidateQueries({ queryKey: ['me', 'deal-lock'] });
      else navigate(`/deals/new?lead_id=${x.lead_id}`);
    },
    onError: (e) => toast(errText(e), true),
  });
  if (!c || FREE.some((p) => loc.pathname.startsWith(p))) return <>{children}</>;
  if (loc.pathname === '/' && c.lead_id) return <Navigate to={`/deals/new?lead_id=${c.lead_id}`} replace />;  // first thing after sign-in
  return (
    <div role="alertdialog" aria-modal="true" style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'var(--bg, #fff)', display: 'grid', placeItems: 'center', padding: 16 }}>
      <div className="col" style={{ maxWidth: 460, gap: 16, textAlign: 'center', alignItems: 'center' }}>
        <div style={{ fontSize: 48 }}>⛔</div>
        <h2 style={{ margin: 0 }}>{t('Сначала оформите сделку по договору #{id}', { id: c.id })}</h2>
        {(c.address || c.client_name) && <div style={{ fontWeight: 600 }}>{[c.address, c.client_name].filter(Boolean).join(' · ')}</div>}
        <div className="muted">{t('Договор подписан обеими сторонами больше 24 часов назад. Пока сделка не оформлена, остальная работа в CRM закрыта.')}</div>
        <Button variant="ok" size="lg" loading={open.isPending} onClick={() => open.mutate()}><Handshake size={18} />{t('Оформить сделку')}</Button>
        <Button variant="ghost" onClick={() => navigate(`/contracts?id=${c.id}`)}>{t('Открыть договор')}</Button>
        <Button variant="ghost" onClick={() => { signOut(); navigate('/login'); }}><LogOut size={16} />{t('Выйти')}</Button>
      </div>
    </div>
  );
}
