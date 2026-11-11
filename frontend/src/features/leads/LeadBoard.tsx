import { useState, type DragEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowRightLeft, CalendarClock } from 'lucide-react';
import { api } from '../../api';
import { itemsOf } from '../../api/client';
import type { Lead, LeadStatus } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { errText, useToast } from '../../components/toast';
import { AssignModal } from './LeadDetail';
import { LeadStatusBadge, SlaChip } from '../../components/domain';
import { Button, Empty, ErrorBox, Loading, Modal, Seg } from '../../components/ui';
import { LEAD_BOARD, LEAD_STATUS, OPERATOR_STAGES, REFUSAL_LABEL, ago, fmtSlot } from '../../lib/format';

export function LeadCard({ lead, to, showStatus, onMove }: { lead: Lead; to: string; showStatus?: boolean; onMove?: () => void }) {
  return (
    <Link to={to} className="kcard" draggable onDragStart={(e) => e.dataTransfer.setData('text/lead', String(lead.id))}>
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
      {onMove && (
        <button type="button" className="btn btn-secondary btn-sm mt" style={{ marginTop: 8 }}
          onClick={(e) => { e.preventDefault(); e.stopPropagation(); onMove(); }}><ArrowRightLeft size={14} />Перенести</button>
      )}
      {showStatus && lead.sla_due_at && <div style={{ marginTop: 6 }}><LeadStatusBadge status={lead.status} /></div>}
    </Link>
  );
}

const BEFORE_VIEWING: LeadStatus[] = ['new', 'operator_taken', 'agent_assigned', 'slots_sent'];

/** Drag boards: dragover fires every ~50 ms while a card is held (even with the pointer still), so scroll the board
 * sideways, the column under the pointer and the page whenever the pointer is near an edge. */
export function dragEdgeScroll(e: DragEvent<HTMLElement>) {
  const EDGE = 80, STEP = 28;
  const el = e.currentTarget;
  const r = el.getBoundingClientRect();
  if (e.clientX > r.right - EDGE) el.scrollLeft += STEP;
  else if (e.clientX < r.left + EDGE) el.scrollLeft -= STEP;
  const col = (e.target as Element).closest?.('.kanban-col-body');
  if (col) {
    const c = col.getBoundingClientRect();
    if (e.clientY > c.bottom - 40) col.scrollTop += STEP / 2;
    else if (e.clientY < c.top + 40) col.scrollTop -= STEP / 2;
  }
  if (e.clientY > window.innerHeight - EDGE) window.scrollBy(0, STEP);
  else if (e.clientY < EDGE) window.scrollBy(0, -STEP);
}
/** scroll-snap would pull programmatic scrolling back to a column: off while dragging */
export const dragBoardProps = {
  onDragOver: dragEdgeScroll,
  onDragStart: (e: DragEvent<HTMLElement>) => { e.currentTarget.style.scrollSnapType = 'none'; },
  onDragEnd: (e: DragEvent<HTMLElement>) => { e.currentTarget.style.scrollSnapType = ''; },
  onDropCapture: (e: DragEvent<HTMLElement>) => { e.currentTarget.style.scrollSnapType = ''; },
};

/** Drag a card to a column = do that step. Steps that need input open a dialog; the rest open the lead page. */
export function LeadBoard({ base = '' }: { base?: string }) {
  const { data, isLoading, error } = useQuery({ queryKey: ['leads', 'board'], queryFn: api.leads.board, refetchInterval: 15_000 });
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const { isStaff } = useAuth();
  // an agent works from «Агент назначен» to the end: the operator columns are neither shown nor a drop target
  const stages = isStaff ? LEAD_BOARD : LEAD_BOARD.filter((s) => !OPERATOR_STAGES.includes(s));
  const [over, setOver] = useState<LeadStatus | null>(null);
  const [assign, setAssign] = useState<Lead | null>(null);
  const [refuse, setRefuse] = useState<Lead | null>(null);
  const [moving, setMoving] = useState<Lead | null>(null);
  const [reason, setReason] = useState('price');
  const act = useMutation({
    mutationFn: ({ lead, to }: { lead: Lead; to: LeadStatus }) =>
      to === 'operator_taken' ? api.leads.take(lead.id)
        : to === 'contract' ? api.leads.startContract(lead.id)
        : to === 'refused' ? (BEFORE_VIEWING.includes(lead.status) ? api.leads.cancel(lead.id, reason) : api.leads.outcome(lead.id, 'refused', reason))
          : api.leads.outcome(lead.id, to),
    onSuccess: (l) => {
      qc.invalidateQueries({ queryKey: ['leads'] }); toast('Заявка перенесена'); setRefuse(null);
      if (l.status === 'contract') navigate(`${base}/contracts?lead_id=${l.id}`);
    },
    onError: (e) => toast(errText(e), true),
  });
  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  const all = Object.values(data || {}).flat();
  const drop = (to: LeadStatus, id: number) => {
    setOver(null);
    const lead = all.find((l) => l.id === id);
    if (!lead || lead.status === to) return;
    if (!stages.includes(to)) { toast(`«${LEAD_STATUS[to]}» — этап оператора`, true); return; }
    if (!isStaff && to === 'refused' && BEFORE_VIEWING.includes(lead.status)) { toast('До просмотра заявку отменяет оператор', true); return; }
    if (!isStaff && to === 'agent_assigned') { toast('Агента назначает оператор; перенос просмотра — в карточке заявки'); navigate(`${base}/leads/${lead.id}`); return; }
    if (to === 'operator_taken' || to === 'thinking' || to === 'contract') act.mutate({ lead, to });
    else if (to === 'agent_assigned') setAssign(lead);
    else if (to === 'refused') setRefuse(lead);
    else if (to === 'deal_pending') navigate(`${base}/deals/new?lead_id=${lead.id}`);
    else if (to === 'rented') { toast('«Сдано» — когда оператор подтвердит сделку'); navigate(`${base}/leads/${lead.id}`); }
    else { toast(`«${LEAD_STATUS[to]}»: этот шаг делается в карточке заявки`); navigate(`${base}/leads/${lead.id}`); }
  };
  return (
    <>
      <div className="kanban" {...dragBoardProps}>
        {/* Mini App: only non-empty stages (the «Перенести» menu still lists them all) */}
        {stages.filter((s) => !base || (data?.[s] || []).length).map((s) => {
          const items = data?.[s] || [];
          const overdue = items.filter((l) => l.sla_due_at && new Date(l.sla_due_at).getTime() < Date.now()).length;
          return (
            <div className={`kanban-col ${over === s ? 'drop-over' : ''}`} key={s}
              onDragOver={(e) => { e.preventDefault(); setOver(s); }} onDragLeave={() => setOver(null)}
              onDrop={(e) => { e.preventDefault(); drop(s, Number(e.dataTransfer.getData('text/lead'))); }}>
              <div className="kanban-col-head">
                <span className="label">{LEAD_STATUS[s]}</span>
                <span className="row gap-sm">{overdue > 0 && <span className="badge badge-red">{overdue}</span>}<span className="tab-count">{items.length}</span></span>
              </div>
              <div className="kanban-col-body">
                {items.length === 0 ? <div className="faint" style={{ fontSize: 12, padding: 6 }}>Пусто</div> : items.map((l) => <LeadCard key={l.id} lead={l} to={`${base}/leads/${l.id}`} onMove={() => setMoving(l)} />)}
              </div>
            </div>
          );
        })}
      </div>
      <Modal open={!!moving} onClose={() => setMoving(null)} title={`Перенести заявку #${moving?.id}`}>
        <div className="col">
          {stages.filter((st) => st !== moving?.status).map((st) => (
            <Button key={st} variant="secondary" block onClick={() => { const l = moving!; setMoving(null); drop(st, l.id); }}>{LEAD_STATUS[st]}</Button>
          ))}
        </div>
      </Modal>
      {assign && <AssignModal lead={assign} open onClose={() => { setAssign(null); qc.invalidateQueries({ queryKey: ['leads'] }); }} />}
      <Modal open={!!refuse} onClose={() => setRefuse(null)} title={`Отказ по заявке #${refuse?.id}`}>
        <div className="col">
          <Seg value={reason} onChange={setReason} options={Object.entries(REFUSAL_LABEL).map(([value, label]) => ({ value, label }))} />
          <Button variant="danger" loading={act.isPending} onClick={() => refuse && act.mutate({ lead: refuse, to: 'refused' })}>Сохранить отказ</Button>
        </div>
      </Modal>
    </>
  );
}

/** Stage chips with counts (GET /leads/counts: an agent's own leads, staff all), wrapping on a phone: a tap shows only
 * that stage, the same chip again (or «Все») goes back to the board / list. */
export function LeadStages({ base = '', list }: { base?: string; list?: boolean }) {
  const { isStaff } = useAuth();
  const [stage, setStage] = useState<LeadStatus | ''>('');
  const counts = useQuery({ queryKey: ['leads', 'counts'], queryFn: api.leads.counts, refetchInterval: 15_000 });
  const stages = isStaff ? LEAD_BOARD : LEAD_BOARD.filter((s) => !OPERATOR_STAGES.includes(s));
  const c = counts.data || {};
  const total = stages.reduce((a, s) => a + (c[s] || 0), 0);
  const label = (t: string, n: number) => <>{t} <span className="tab-count">{n}</span></>;
  return (
    <div className="col">
      <Seg value={stage} onChange={(s) => setStage(s === stage ? '' : s)}
        options={[{ value: '' as const, label: label('Все', total) }, ...stages.map((s) => ({ value: s, label: label(LEAD_STATUS[s], c[s] || 0) }))]} />
      {stage ? <StageLeads key={stage} stage={stage} base={base} /> : list ? <div style={{ maxWidth: 640 }}><MyLeadsList base={base} /></div> : <LeadBoard base={base} />}
    </div>
  );
}

function StageLeads({ stage, base }: { stage: LeadStatus; base: string }) {
  const { data, isLoading, error } = useQuery({ queryKey: ['leads', 'list', 'stage', stage], queryFn: () => api.leads.list({ status: stage }), refetchInterval: 20_000 });
  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  const items = itemsOf(data);
  return (
    <div className="col" style={{ maxWidth: 640 }}>
      {items.length === 0 ? <Empty text={`«${LEAD_STATUS[stage]}»: заявок нет`} /> : items.map((l) => <LeadCard key={l.id} lead={l} to={`${base}/leads/${l.id}`} />)}
    </div>
  );
}

/** agentId: the cabinet opened by staff for one agent (agents always get their own, backend leads.query) */
export function MyLeadsList({ base = '', agentId }: { base?: string; agentId?: number }) {
  const { data, isLoading, error } = useQuery({ queryKey: ['leads', 'list', 'mine-active', agentId], queryFn: () => api.leads.list({ agent_id: agentId }), refetchInterval: 20_000 });
  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  const items = itemsOf(data);
  const order = ['agent_assigned', 'time_chosen', 'viewing_done', 'slots_sent', 'thinking', 'contract', 'deal_pending'];
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

/** «Архив»: closed leads taken off the board; search by client, address, district or #id. */
export function ArchivedLeads({ base = '' }: { base?: string }) {
  const [q, setQ] = useState('');
  const [status, setStatus] = useState('');
  const { data, isLoading, error } = useQuery({
    queryKey: ['leads', 'list', 'archived', q, status],
    queryFn: () => api.leads.list({ archived: true, q: q.trim() || undefined, status: status || undefined }),
  });
  const items = itemsOf(data);
  return (
    <div className="col" style={{ maxWidth: 640 }}>
      <div className="row row-wrap">
        <input className="input" style={{ flex: 1, minWidth: 200 }} placeholder="Клиент, адрес, район или #номер" value={q} onChange={(e) => setQ(e.target.value)} />
        <Seg value={status} onChange={setStatus} options={[{ value: '', label: 'Все' }, { value: 'rented', label: 'Сдано' }, { value: 'refused', label: 'Отказ' }, { value: 'thinking', label: 'Думает' }]} />
      </div>
      {isLoading ? <Loading /> : error ? <ErrorBox error={error} /> : items.length === 0 ? <Empty text="В архиве ничего не нашлось" />
        : items.map((l) => <LeadCard key={l.id} lead={l} to={`${base}/leads/${l.id}`} showStatus />)}
    </div>
  );
}
