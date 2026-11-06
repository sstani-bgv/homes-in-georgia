// «Запрос оператору» (stage 6): an agent's change of a locked field / archive / owner transfer after verification.
// The operator queue «Запросы» and the same rows on the object card: было → стало, who, when, approve / reject.
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, X } from 'lucide-react';
import { api } from '../../api';
import { itemsOf } from '../../api/client';
import type { ChangeRequest } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { errText, useToast } from '../../components/toast';
import { Badge, Button, Empty, ErrorBox, Input, Loading } from '../../components/ui';
import { ARCHIVE_REASON, CR_FIELD, CR_KIND, ago, fmtDateTime, money } from '../../lib/format';

function val(field: string, v: unknown) {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'boolean') return v ? 'да' : 'нет';
  if (field === 'archive_reason') return ARCHIVE_REASON[String(v)] || String(v);
  if (['price', 'deposit', 'sale_price'].includes(field)) return money(Number(v));
  if (field === 'duplicate_of') return `#${v}`;
  return String(v);
}

export function RequestRow({ r, link = (id: number) => `/objects/${id}`, showObject = true }: { r: ChangeRequest; link?: (id: number) => string; showObject?: boolean }) {
  const { isStaff } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const [comment, setComment] = useState('');
  const decide = useMutation({
    mutationFn: (approve: boolean) => api.changeRequests.decide(r.id, approve, comment),
    onSuccess: (x) => {
      ['change-requests', 'property', 'property-history', 'properties'].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
      toast(x.status === 'approved' ? 'Одобрено: изменение применено' : 'Отклонено: агенту ушёл комментарий');
    },
    onError: (e) => toast(errText(e), true),
  });
  return (
    <div className="list-row col gap-sm" style={{ alignItems: 'stretch' }}>
      <div className="row row-between row-wrap gap-sm">
        <div className="row row-wrap gap-sm" style={{ fontWeight: 700 }}>
          <Badge tone="warn">{CR_KIND[r.kind] || r.kind}</Badge>
          {showObject && r.property && <Link to={link(r.property.id)}>Объект #{r.property.id}{r.property.address ? ` · ${r.property.address}` : ''}</Link>}
        </div>
        <span className="muted" style={{ fontSize: 12 }} title={fmtDateTime(r.created_at)}>{r.requested_by?.name || '—'} · {ago(r.created_at)}</span>
      </div>
      <div className="col gap-sm" style={{ fontSize: 13 }}>
        {r.changes.map((c) => (
          <div key={c.field} className="row row-wrap gap-sm">
            <span className="muted" style={{ minWidth: 150 }}>{CR_FIELD[c.field] || c.field}</span>
            <s className="muted">{val(c.field, c.old)}</s><span>→</span><b>{val(c.field, c.new)}</b>
          </div>
        ))}
        {r.reason && <div className="muted">Причина: {r.reason}</div>}
      </div>
      {isStaff && r.status === 'pending' ? (
        <div className="row row-wrap gap-sm">
          <Button size="sm" variant="ok" loading={decide.isPending && decide.variables} onClick={() => decide.mutate(true)}><Check size={14} />Одобрить</Button>
          <Input value={comment} placeholder="Комментарий агенту (нужен для отказа)" onChange={(e) => setComment(e.target.value)} style={{ maxWidth: 320 }} />
          <Button size="sm" variant="danger" disabled={!comment.trim()} loading={decide.isPending && !decide.variables} onClick={() => decide.mutate(false)}><X size={14} />Отклонить</Button>
        </div>
      ) : r.status === 'pending' ? <div className="muted" style={{ fontSize: 12 }}>Ждёт решения оператора</div> : null}
    </div>
  );
}

/** «Запросы»: the operators' queue of pending requests, oldest first. */
export function ChangeRequestQueue() {
  const { data, isLoading, error } = useQuery({ queryKey: ['change-requests', 'pending'], queryFn: () => api.changeRequests.list('pending'), refetchInterval: 30_000 });
  const items = itemsOf(data);
  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!items.length) return <Empty text="Запросов нет" />;
  return <div className="card card-pad col" style={{ maxWidth: 860 }}>{items.map((r) => <RequestRow key={r.id} r={r} />)}</div>;
}
