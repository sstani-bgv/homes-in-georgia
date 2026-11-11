// Report forms (fields as in the old CRM / apps-script save* functions) + list.
import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Camera, CalendarDays, ClipboardList, Eye, LayoutList, StickyNote } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { api } from '../../api';
import { itemsOf } from '../../api/client';
import type { ReportKind } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { DupCheck } from '../../components/domain';
import { errText, useToast } from '../../components/toast';
import { Badge, Button, Card, Empty, ErrorBox, Field, Input, Loading, Progress, Select, Textarea, DistrictSelect } from '../../components/ui';
import { fmtDate, todayStr } from '../../lib/format';
import { useUserNames } from '../../lib/useUserNames';

type FieldDef = { k: string; label: string; type?: 'text' | 'number' | 'date' | 'datetime-local' | 'textarea' | 'district' | 'result' | 'url' | 'tel'; span?: boolean; required?: boolean };
export const REPORT_KINDS: { kind: ReportKind; label: string; icon: LucideIcon; recruit?: boolean; counter?: 'photo' | 'showing' | 'stickers'; fields: FieldDef[] }[] = [
  { kind: 'photo', label: 'Фото', icon: Camera, recruit: true, counter: 'photo', fields: [
    { k: 'district_address', label: 'Район / адрес', span: true, required: true }, { k: 'ad_link_advertising', label: 'Ссылка на пост', type: 'url', span: true }, { k: 'date', label: 'Дата', type: 'date' },
  ] },
  { kind: 'showing', label: 'Показ', icon: Eye, recruit: true, counter: 'showing', fields: [
    { k: 'district_address', label: 'Район / адрес', span: true, required: true }, { k: 'ad_link_advertising', label: 'Ссылка на объявление', type: 'url' },
    { k: 'listing_link', label: 'Ссылка на объект', type: 'url' }, { k: 'client_name', label: 'Клиент' }, { k: 'client_telegram', label: 'Telegram клиента' },
    { k: 'client_phone', label: 'Телефон клиента', type: 'tel' }, { k: 'owner_phone', label: 'Телефон собственника', type: 'tel' },
    { k: 'show_time', label: 'Время показа' }, { k: 'result', label: 'Результат', type: 'result' }, { k: 'date', label: 'Дата', type: 'date' },
  ] },
  { kind: 'stickers', label: 'Расклейка', icon: StickyNote, recruit: true, counter: 'stickers', fields: [
    { k: 'district', label: 'Район', type: 'district', required: true }, { k: 'stickers_count', label: 'Стикеров', type: 'number', required: true },
    { k: 'map_photo_url', label: 'Ссылка на фото карты', type: 'url', span: true }, { k: 'date', label: 'Дата', type: 'date' },
  ] },
  { kind: 'daily', label: 'Дневной', icon: CalendarDays, fields: [
    { k: 'date', label: 'Дата', type: 'date' }, { k: 'calling', label: 'Обзвон', type: 'number' }, { k: 'not_agreed', label: 'Не согласились', type: 'number' },
    { k: 'agreed', label: 'Согласились', type: 'number' }, { k: 'stickers', label: 'Стикеры', type: 'number' }, { k: 'photos', label: 'Съёмки', type: 'number' },
    { k: 'showings', label: 'Показы', type: 'number' }, { k: 'comment', label: 'Комментарий', type: 'textarea', span: true },
  ] },
  { kind: 'general', label: 'Общий', icon: LayoutList, fields: [
    { k: 'photo', label: 'Фото, шт', type: 'number' }, { k: 'photoAddr', label: 'Адреса съёмок' }, { k: 'showing', label: 'Показов', type: 'number' },
    { k: 'showingDist', label: 'Район показов', type: 'district' }, { k: 'stickers', label: 'Стикеров', type: 'number' }, { k: 'stickersDist', label: 'Район расклейки', type: 'district' },
  ] },
  { kind: 'makefoto', label: 'Запись на съёмку', icon: ClipboardList, fields: [
    { k: 'address', label: 'Адрес', span: true, required: true }, { k: 'owner', label: 'Собственник' }, { k: 'phone', label: 'Телефон', type: 'tel', required: true },
    { k: 'date', label: 'Дата и время съёмки', type: 'datetime-local', required: true }, { k: 'link', label: 'Ссылка на объявление', type: 'url' },
  ] },
];
export const KIND_LABEL: Record<string, string> = Object.fromEntries(REPORT_KINDS.map((k) => [k.kind, k.label]));

export function ReportForm({ kind, onDone }: { kind: ReportKind; onDone?: () => void }) {
  const def = REPORT_KINDS.find((k) => k.kind === kind)!;
  const { user } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const users = useQuery({ queryKey: ['users', 'all-active'], queryFn: () => api.users.list({ active: true }) });
  const counters = useQuery({ queryKey: ['counters', 'now'], queryFn: () => api.reports.counters(), enabled: !!def.counter });
  const all = itemsOf(users.data);
  const recruits = all.filter((u) => u.role === 'recruit');
  const mentors = all.filter((u) => ['agent', 'superagent', 'superadmin', 'admin'].includes(u.role));
  const initial = useMemo(() => {
    const v: Record<string, string> = {};
    def.fields.forEach((f) => { if (f.type === 'date') v[f.k] = todayStr(); });
    if (user?.role === 'recruit') { v.recruit_id = String(user.id); if (user.mentor_id) v.mentor_id = String(user.mentor_id); }
    return v;
  }, [def, user]);
  const [v, setV] = useState<Record<string, string>>(initial);
  const [result, setResult] = useState<string | null>(null);
  const set = (k: string, val: string) => setV((x) => ({ ...x, [k]: val }));
  const pickRecruit = (id: string) => {
    const r = recruits.find((x) => String(x.id) === id);
    setV((x) => ({ ...x, recruit_id: id, mentor_id: r?.mentor_id ? String(r.mentor_id) : x.mentor_id || '' }));
  };
  const cnt = itemsOf(counters.data).find((c) => String(c.user_id) === v.recruit_id);
  const pair = def.counter && cnt ? cnt[def.counter] : null;

  const m = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = { ...v };
      def.fields.forEach((f) => { if (f.type === 'number' && v[f.k] !== undefined && v[f.k] !== '') body[f.k] = Number(v[f.k]); });
      if (v.recruit_id) { body.recruit_id = Number(v.recruit_id); body.recruit = recruits.find((r) => String(r.id) === v.recruit_id)?.name; }
      if (v.mentor_id) { body.mentor_id = Number(v.mentor_id); body.mentor = mentors.find((r) => String(r.id) === v.mentor_id)?.name; }
      if (kind === 'makefoto') body.agent = user?.name;
      if (!body.author) body.author = user?.name;
      return api.reports.create(kind, body);
    },
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['reports'] });
      qc.invalidateQueries({ queryKey: ['counters'] });
      toast('Отчёт сохранён и отправлен в топик');
      setResult(r.dup_check || (r.data?.dup_check as string) || null);
      if (kind !== 'makefoto') { setV(initial); onDone?.(); }
    },
    onError: (e) => toast(errText(e), true),
  });
  const valid = def.fields.every((f) => !f.required || (v[f.k] || '').trim()) && (!def.recruit || !!v.recruit_id);

  return (
    <div className="col">
      <div className="form-grid">
        {def.recruit && <>
          <Field label="Рекрут"><Select value={v.recruit_id || ''} onChange={(e) => pickRecruit(e.target.value)} placeholder="Выбрать рекрута" options={recruits.map((r) => ({ value: r.id, label: r.name }))} /></Field>
          <Field label="Наставник"><Select value={v.mentor_id || ''} onChange={(e) => set('mentor_id', e.target.value)} placeholder="Выбрать наставника" options={mentors.map((r) => ({ value: r.id, label: r.name }))} /></Field>
        </>}
        {def.fields.map((f) => {
          const common = { value: v[f.k] || '', onChange: (e: { target: { value: string } }) => set(f.k, e.target.value) };
          let input;
          if (f.type === 'textarea') input = <Textarea {...common} />;
          else if (f.type === 'district') input = <DistrictSelect value={common.value} onChange={(x) => set(f.k, x)} />;
          else if (f.type === 'result') input = <Select {...common} placeholder="—" options={['Снимает', 'Думает', 'Не подошло', 'Перенос'].map((d) => ({ value: d, label: d }))} />;
          else input = <Input type={f.type === 'url' ? 'url' : f.type === 'tel' ? 'tel' : f.type || 'text'} inputMode={f.type === 'number' ? 'numeric' : undefined} {...common} />;
          return <Field key={f.k} label={f.label} className={f.span ? 'span2' : ''}>{input}</Field>;
        })}
      </div>
      {pair && (
        <div className="card card-pad" style={{ background: 'var(--brand-tint)' }}>
          <div className="row row-between"><span className="label">Учёт за месяц</span><b className="num">{pair.done}/{pair.plan}</b></div>
          <div className="mt"><Progress value={pair.done} max={pair.plan} /></div>
        </div>
      )}
      {kind === 'makefoto' && <DupCheck value={result} />}
      <Button size="lg" disabled={!valid} loading={m.isPending} onClick={() => m.mutate()}>Отправить отчёт</Button>
    </div>
  );
}

function summary(kind: string, d: Record<string, unknown>): string {
  const g = (k: string) => (d[k] === undefined || d[k] === null || d[k] === '' ? null : String(d[k]));
  switch (kind) {
    case 'photo': return [g('district_address'), g('ad_link_advertising')].filter(Boolean).join(' · ');
    case 'showing': return [g('district_address'), g('client_telegram') || g('client_name'), g('result')].filter(Boolean).join(' · ');
    case 'stickers': return `${g('district') || ''} · ${g('stickers_count') || 0} стикеров`;
    case 'daily': return `обзвон ${g('calling') || 0} · согласились ${g('agreed') || 0} · съёмки ${g('photos') || 0} · показы ${g('showings') || 0}`;
    case 'general': return `фото ${g('photo') || 0} · показы ${g('showing') || 0} · стикеры ${g('stickers') || 0}`;
    case 'makefoto': return [g('address'), g('owner'), g('date')].filter(Boolean).join(' · ');
    default: return '';
  }
}

export function ReportsList({ kind }: { kind?: string }) {
  const { data, isLoading, error } = useQuery({ queryKey: ['reports', kind || 'all'], queryFn: () => api.reports.list({ kind }) });
  const items = itemsOf(data);
  const nameOf = useUserNames();
  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!items.length) return <Empty text="Отчётов пока нет" />;
  return (
    <Card pad={false}>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Дата</th><th>Тип</th><th>Автор</th><th>Детали</th></tr></thead>
          <tbody>
            {items.map((r) => (
              <tr key={r.id}>
                <td className="nowrap">{fmtDate(r.date || r.created_at)}</td>
                <td><Badge>{KIND_LABEL[r.kind] || r.kind}</Badge></td>
                <td>{r.recruit?.name || (r.data?.recruit as string) || nameOf(r.author_id, r.author?.name || (r.data?.author as string))}</td>
                <td className="muted" style={{ fontSize: 13 }}>{summary(r.kind, r.data || {})}{r.dup_check === 'exists' ? ' · ⚠️ уже в базе' : ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
