import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { CalendarClock } from 'lucide-react';
import { api } from '../../api';
import { itemsOf } from '../../api/client';
import type { Lead } from '../../api/types';
import { LeadStatusBadge, SlaChip } from '../../components/domain';
import { Empty, ErrorBox, Loading } from '../../components/ui';
import { LEAD_BOARD, LEAD_STATUS, ago, fmtSlot } from '../../lib/format';

export function LeadCard({ lead, to, showStatus }: { lead: Lead; to: string; showStatus?: boolean }) {
  return (
    <Link to={to} className="kcard">
      <div className="row row-between">
        <span className="label">#{lead.id}</span>
        {lead.sla_due_at ? <SlaChip lead={lead} /> : showStatus ? <LeadStatusBadge status={lead.status} /> : <span className="faint" style={{ fontSize: 11 }}>{ago(lead.created_at)}</span>}
      </div>
      <div className="kcard-title mt" style={{ marginTop: 6 }}>{lead.listing?.title || lead.listing_id}</div>
      <div className="kcard-meta">
        {lead.client?.name || 'Клиент'}
        {lead.client?.profile?.budget_max ? ` · до $${lead.client.profile.budget_max}` : ''}
      </div>
      <div className="row row-between" style={{ marginTop: 8, fontSize: 11 }}>
        <span className="faint" style={{ fontWeight: 600 }}>{lead.agent ? `Агент: ${lead.agent.name}` : lead.operator ? `Оператор: ${lead.operator.name}` : 'Ждёт оператора'}</span>
        {lead.viewing_at && <span className="badge"><CalendarClock size={11} />{fmtSlot(lead.viewing_at)}</span>}
      </div>
      {showStatus && lead.sla_due_at && <div style={{ marginTop: 6 }}><LeadStatusBadge status={lead.status} /></div>}
    </Link>
  );
}

export function LeadBoard() {
  const { data, isLoading, error } = useQuery({ queryKey: ['leads', 'board'], queryFn: api.leads.board, refetchInterval: 15_000 });
  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  return (
    <div className="kanban">
      {LEAD_BOARD.map((s) => {
        const items = data?.[s] || [];
        const overdue = items.filter((l) => l.sla_due_at && new Date(l.sla_due_at).getTime() < Date.now()).length;
        return (
          <div className="kanban-col" key={s}>
            <div className="kanban-col-head">
              <span className="label">{LEAD_STATUS[s]}</span>
              <span className="row gap-sm">{overdue > 0 && <span className="badge badge-red">{overdue}</span>}<span className="tab-count">{items.length}</span></span>
            </div>
            <div className="kanban-col-body">
              {items.length === 0 ? <div className="faint" style={{ fontSize: 12, padding: 6 }}>Пусто</div> : items.map((l) => <LeadCard key={l.id} lead={l} to={`/leads/${l.id}`} />)}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function MyLeadsList({ base = '' }: { base?: string }) {
  const { data, isLoading, error } = useQuery({ queryKey: ['leads', 'list', 'mine-active'], queryFn: () => api.leads.list({}), refetchInterval: 20_000 });
  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  const items = itemsOf(data);
  const order = ['agent_assigned', 'time_chosen', 'viewing_done', 'slots_sent', 'thinking', 'deal_pending'];
  const active = items.filter((l) => order.includes(l.status)).sort((a, b) => order.indexOf(a.status) - order.indexOf(b.status));
  const closed = items.filter((l) => !order.includes(l.status));
  return (
    <div className="col">
      {active.length === 0 ? <Empty text="Активных заявок нет" /> : active.map((l) => <LeadCard key={l.id} lead={l} to={`${base}/leads/${l.id}`} showStatus />)}
      {closed.length > 0 && <div className="label mt">Остальные</div>}
      {closed.map((l) => <LeadCard key={l.id} lead={l} to={`${base}/leads/${l.id}`} showStatus />)}
    </div>
  );
}
