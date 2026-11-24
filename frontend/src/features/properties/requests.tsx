// «Запрос оператору» (stage 6): an agent's change of a locked field / archive / owner transfer after verification.
// The operator queue «Запросы» and the same rows on the object card: было → стало, who, when, approve / reject.
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, FileText, X } from 'lucide-react';
import { api } from '../../api';
import { itemsOf, mediaUrl } from '../../api/client';
import type { ChangeRequest, DupsGate as Gate } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { errText, useToast } from '../../components/toast';
import { Badge, Button, Empty, ErrorBox, Input, Loading } from '../../components/ui';
import { ARCHIVE_REASON, CR_FIELD, CR_KIND, ago, fmtDateTime, money, phonesLine } from '../../lib/format';
import { t } from '../../lib/i18n';

function val(field: string, v: unknown) {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'boolean') return v ? t('да') : t('нет');
  if (field === 'archive_reason') return ARCHIVE_REASON[String(v)] || String(v);
  if (['price', 'deposit', 'sale_price'].includes(field)) return money(Number(v));
  if (field === 'duplicate_of') return `#${v}`;
  if (Array.isArray(v)) return v.every((x) => typeof x !== 'object') ? v.join(', ') : phonesLine(v) || '—';  // owner_phones
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

const GATE: Record<Gate['state'], [string, 'ok' | 'warn' | 'info']> = {
  clear: ['Эксклюзив подтверждён: дублей нет', 'ok'], waiting: ['Ожидается устранение дублей', 'warn'],
  review: ['Сомнительные совпадения — ручная проверка', 'info'], operator: ['Дубли остались — решение оператора', 'warn'],
};
const LEVEL: Record<string, string> = { confirmed: 'дубль', probable: 'вероятный дубль', doubtful: 'сомнительно' };

function left(deadline: string, at: number) {
  const ms = new Date(deadline).getTime() - at;
  if (ms <= 0) return t('срок вышел, идёт повторная проверка');
  const h = Math.floor(ms / 3_600_000), m = Math.floor((ms % 3_600_000) / 60_000);
  return t('осталось {h} ч {m} мин', { h, m });
}

/** §12 (David 11-19): the duplicates gate of «Без электронной подписи» — the status, the 24 h countdown, the list.
 * The system never removes an outside ad: the agent has the owner take them down. */
export function DupsGate({ d }: { d?: Gate | null }) {
  const [at, setAt] = useState(Date.now());
  useEffect(() => {
    if (d?.state !== 'waiting') return;
    const id = setInterval(() => setAt(Date.now()), 60_000);
    return () => clearInterval(id);
  }, [d?.state]);
  if (!d) return null;
  const [label, tone] = GATE[d.state];
  return (
    <div className={`alert alert-${tone}`} style={{ fontSize: 13 }}>
      <div className="col gap-sm" style={{ minWidth: 0 }}>
        <b>{t(label)}{d.state === 'waiting' && d.deadline ? ` · ${left(d.deadline, at)}` : ''}</b>
        {d.state === 'waiting' && <span className="muted">{t('До {at}: подписанный собственником эксклюзивный договор и снятие остальных объявлений. Система сама чужие объявления не удаляет.', { at: d.deadline ? fmtDateTime(d.deadline) : '—' })}</span>}
        {d.items.map((x) => (
          <span key={x.key} style={{ overflowWrap: 'anywhere' }}>
            • {x.where}{x.property_id ? ` #${x.property_id}` : ''} — {t(LEVEL[x.level] || x.level)}{x.pct != null ? ` · ${Math.round(x.pct * 100)}%` : ''}
            {x.url && <> · <a href={x.url} target="_blank" rel="noreferrer">{t('ссылка')}</a></>}
            {x.reasons.length > 0 && <span className="muted">: {x.reasons.join(', ')}</span>}
          </span>
        ))}
      </div>
    </div>
  );
}

export function RequestRow({ r, link = (id: number) => `/objects/${id}`, showObject = true }: { r: ChangeRequest; link?: (id: number) => string; showObject?: boolean }) {
  const { isStaff, user } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const [comment, setComment] = useState('');
  const decide = useMutation({
    mutationFn: (approve: boolean) => api.changeRequests.decide(r.id, approve, comment),
    onSuccess: (x) => {
      ['change-requests', 'property', 'property-history', 'properties'].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
      toast(x.status === 'approved' ? t('Одобрено: изменение применено') : t('Отклонено: агенту ушёл комментарий'));
    },
    onError: (e) => toast(errText(e), true),
  });
  return (
    <div className="list-row col gap-sm" style={{ alignItems: 'stretch' }}>
      <div className="row row-between row-wrap gap-sm">
        <div className="row row-wrap gap-sm" style={{ fontWeight: 700 }}>
          <Badge tone="warn">{CR_KIND[r.kind] || r.kind}</Badge>
          {showObject && r.property && <Link to={link(r.property.id)}>{t('Объект #{id}', { id: r.property.id })}{r.property.address ? ` · ${r.property.address}` : ''}</Link>}
          {r.owner && <span>{t('Собственник')} {r.owner.name || `#${r.owner.id}`}</span>}
        </div>
        <span className="muted" style={{ fontSize: 12 }} title={fmtDateTime(r.created_at)}>{r.requested_by?.name || '—'} · {ago(r.created_at)}</span>
      </div>
      <div className="col gap-sm" style={{ fontSize: 13 }}>
        {r.changes.map((c) => (
          <div key={c.field} className="row row-wrap gap-sm">
            <span className="muted" style={{ minWidth: 150 }}>{CR_FIELD[c.field] || (c.field.startsWith('pf:') ? t('Пост: {k}', { k: c.field.slice(3) }) : c.field)}</span>
            <s className="muted">{val(c.field, c.old)}</s><span>→</span><b>{val(c.field, c.new)}</b>
          </div>
        ))}
        {r.reason && <div className="muted">{t('Причина:')} {r.reason}</div>}
        {r.scan_url && <a href={mediaUrl(r.scan_url)} target="_blank" rel="noreferrer"><FileText size={14} /> {t('скан договора')}</a>}
        <DupsGate d={r.dups} />
      </div>
      {isStaff && r.status === 'pending' && r.requested_by?.id === user?.id ? <div className="muted" style={{ fontSize: 12 }}>{t('Свой запрос решает другой оператор')}</div>
        : isStaff && r.status === 'pending' ? (
        <div className="row row-wrap gap-sm">
          <Button size="sm" variant="ok" loading={decide.isPending && decide.variables} onClick={() => decide.mutate(true)}><Check size={14} />{t('Одобрить')}</Button>
          <Input value={comment} placeholder={t('Комментарий агенту (нужен для отказа)')} onChange={(e) => setComment(e.target.value)} style={{ maxWidth: 320 }} />
          <Button size="sm" variant="danger" disabled={!comment.trim()} loading={decide.isPending && !decide.variables} onClick={() => decide.mutate(false)}><X size={14} />{t('Отклонить')}</Button>
        </div>
      ) : r.status === 'pending' ? <div className="muted" style={{ fontSize: 12 }}>{t('Ждёт решения оператора')}</div> : null}
    </div>
  );
}

/** «Запросы»: the operators' queue of pending requests, oldest first. */
export function ChangeRequestQueue() {
  const { data, isLoading, error } = useQuery({ queryKey: ['change-requests', 'pending'], queryFn: () => api.changeRequests.list('pending'), refetchInterval: 30_000 });
  const items = itemsOf(data);
  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!items.length) return <Empty text={t('Запросов нет')} />;
  return <div className="card card-pad col" style={{ maxWidth: 860 }}>{items.map((r) => <RequestRow key={r.id} r={r} />)}</div>;
}
