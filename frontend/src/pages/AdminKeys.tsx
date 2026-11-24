// Управление → Ключи (admin+): one card per service — AI (provider, model, key, balance, real spend per model), Apify
// (keys, each key's month), MapTiler, other keys. Settings show in full, secrets masked with «Показать» (logged on the
// server), «Проверить» makes a cheap live call. Backend: /keys, /keys/{name}/value, /keys/{name}/check, /spend.
import { useConfirm } from '../components/ActionDialog';
import { useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Eye, EyeOff, Pencil, Plus, Trash2, Zap } from 'lucide-react';
import { api } from '../api';
import { itemsOf } from '../api/client';
import type { IntegrationKey, KeyCheck, LlmSum } from '../api/types';
import { errText, useToast } from '../components/toast';
import { Badge, Button, Card, ErrorBox, Field, Input, Loading, Modal, Progress, Select } from '../components/ui';
import { fmtDateTime, num } from '../lib/format';
import { t } from '../lib/i18n';

const AI_KEYS = ['OPENAI_BASE_URL', 'LLM_MODEL', 'OPENAI_API_KEY'];
const APIFY_KEYS = ['APIFY_TOKENS', 'APIFY_TOKEN'];
const MAP_KEYS = ['MAPTILER_KEY'];
const CHECKABLE = new Set(['OPENAI_API_KEY', ...APIFY_KEYS]);
const usd = (v: number | null | undefined, d = 2) => (v == null ? '—' : `$${v.toFixed(v > 0 && v < 0.01 ? 4 : d)}`);
const PURPOSE: Record<string, string> = {
  search_query: 'Подбор: разбор запроса клиента', search_near: 'Подбор: место рядом с которым искать',
  search_pick: 'Подбор: выбор вариантов', search_translate: 'Подбор: перевод карточек на английский', contract_translate: 'Договоры: автоперевод пунктов', post_translate: 'Посты: перевод описания на английский',
  index: 'Парсер: разбор новых объявлений (текст + фото)', index_competitor: 'Парсер: разбор постов конкурентов (текст + фото)',
  index_facts: 'Парсер: факты для старых объявлений (текст)', other: 'Прочее (проверка ключа, ручные запуски)',
};
const mono = { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 13, overflowWrap: 'anywhere' as const };

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
type Edit = { name: string; value: string; isNew?: boolean; custom?: boolean };

function KeyRow({ k, onEdit, onDelete, deleting }: { k: IntegrationKey; onEdit: () => void; onDelete: () => void; deleting: boolean }) {
  const toast = useToast();
  const [shown, setShown] = useState<string | null>(null);
  const reveal = useMutation({ mutationFn: () => api.keys.reveal(k.name), onSuccess: (r) => setShown(r.value), onError: (e) => toast(errText(e), true) });
  const check = useMutation({ mutationFn: () => api.keys.check(k.name), onError: (e) => toast(errText(e), true) });
  const value = shown ?? k.value;
  return (
    <div className="list-row" style={{ flexWrap: 'wrap', alignItems: 'flex-start' }}>
      <div className="grow" style={{ minWidth: 0, flexBasis: 240 }}>
        <div style={{ fontWeight: 700 }}>{k.title ? cap(t(k.title).replace(/^[^:]+:\s*/, '')) : k.name}</div>
        <div style={{ ...mono, marginTop: 4, color: value ? 'var(--ink)' : 'var(--muted)' }}>{value || t('не задан')}</div>
        <div className="faint" style={{ fontSize: 12, marginTop: 4 }}>
          <span style={{ fontFamily: 'monospace' }}>{k.name}</span>
          {' · '}{k.source === 'crm' ? t('задан в CRM') : k.source === 'env' ? t('с сервера (.env)') : t('нигде не задан')}
          {k.source === 'crm' && k.changed_at ? ` · ${fmtDateTime(k.changed_at)}${k.changed_by ? `, ${k.changed_by}` : ''}` : ''}
        </div>
        {check.data && <div className="row gap-sm" style={{ flexWrap: 'wrap', marginTop: 6 }}>
          {check.data.results.map((r: KeyCheck, i) => <span key={i} className={`badge badge-${r.ok ? 'ok' : 'red'}`} style={{ textTransform: 'none', letterSpacing: 0 }} title={r.error || undefined}>
            {r.label} · {r.ok ? `OK · ${num(r.ms)} ${t('мс')}` : t(r.error || 'ошибка')}</span>)}
        </div>}
      </div>
      <div className="row gap-sm" style={{ flexWrap: 'wrap' }}>
        {k.secret && k.value && <Button size="sm" variant="ghost" aria-label={shown ? t('Скрыть') : t('Показать')} title={shown ? t('Скрыть') : t('Показать')}
          loading={reveal.isPending} onClick={() => (shown ? setShown(null) : reveal.mutate())}>{shown ? <EyeOff size={15} /> : <Eye size={15} />}</Button>}
        {CHECKABLE.has(k.name) && k.value && <Button size="sm" variant="secondary" loading={check.isPending} onClick={() => check.mutate()}><Zap size={14} />{t('Проверить')}</Button>}
        <Button size="sm" variant="secondary" onClick={onEdit}><Pencil size={14} />{k.source === 'crm' ? t('Заменить') : t('Задать')}</Button>
        {k.source === 'crm' && <Button size="sm" variant="ghost" aria-label={t('Удалить')} loading={deleting} onClick={onDelete}><Trash2 size={14} /></Button>}
      </div>
    </div>
  );
}

function Tile({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div style={{ background: 'var(--brand-tint)', borderRadius: 12, padding: '10px 12px', minWidth: 0 }}>
      <div className="label">{label}</div>
      <div style={{ fontSize: 20, fontWeight: 800, fontVariantNumeric: 'tabular-nums', marginTop: 2 }}>{value}</div>
      {sub && <div className="faint" style={{ fontSize: 12 }}>{sub}</div>}
    </div>
  );
}
const tiles = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 10 } as const;

export function KeysPage() {
  const confirm = useConfirm();
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ['keys'], queryFn: api.keys.list });
  const sp = useQuery({ queryKey: ['spend'], queryFn: api.spend });
  const [edit, setEdit] = useState<Edit | null>(null);
  const done = (d: { items: IntegrationKey[] }) => { qc.setQueryData(['keys'], d); qc.invalidateQueries({ queryKey: ['spend'] }); setEdit(null); toast(t('Сохранено')); };
  const put = useMutation({ mutationFn: () => api.keys.put(edit!.name.trim().toUpperCase(), edit!.value.trim()), onSuccess: done, onError: (e) => toast(errText(e), true) });
  const del = useMutation({ mutationFn: (n: string) => api.keys.remove(n), onSuccess: (d) => { qc.setQueryData(['keys'], d); qc.invalidateQueries({ queryKey: ['spend'] }); }, onError: (e) => toast(errText(e), true) });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const all = itemsOf(q.data);
  const by = (names: string[]) => names.map((n) => all.find((k) => k.name === n)).filter(Boolean) as IntegrationKey[];
  const other = all.filter((k) => ![...AI_KEYS, ...APIFY_KEYS, ...MAP_KEYS].includes(k.name));
  const rows = (ks: IntegrationKey[]) => ks.map((k) => <KeyRow key={k.name} k={k} onEdit={() => setEdit({ name: k.name, value: '' })}
    deleting={del.isPending && del.variables === k.name}
    onDelete={async () => await confirm(t('Удалить ключ {name} из CRM? Останется значение с сервера, если оно есть.', { name: k.name })) && del.mutate(k.name)} />);
  const s = sp.data;
  const ai = s?.ai;
  const sumSub = (x: LlmSum) => t('{calls} вызовов · {tokens} токенов', { calls: num(x.calls), tokens: num(x.prompt + x.completion) });
  const models = ai ? [...new Set([...ai.month.models, ...ai.week.models, ...ai.today.models].map((m) => m.model))] : [];
  const cell = (x: LlmSum, m: string) => { const r = x.models.find((y) => y.model === m); return r ? (x.unpriced.includes(m) ? '—' : usd(r.usd)) : '·'; };
  const bal = ai?.balance;
  return (
    <div className="col gap">
      <Card label={ai?.provider || t('Нейросеть')} title={t('Нейросеть')}>
        {sp.error ? <ErrorBox error={sp.error} /> : sp.isLoading ? <Loading /> : ai && <>
          {ai.mode === 'codex' && <div className="alert alert-warn" style={{ marginBottom: 12 }}>{t('Сейчас ИИ идёт через подписку Codex: токены не считаются')}</div>}
          <div style={tiles}>
            {bal && bal.balance_usd != null && <Tile label={t('Баланс')} value={usd(bal.balance_usd)} sub={bal.used_usd != null ? t('потрачено всего {v}', { v: usd(bal.used_usd) }) : undefined} />}
            {bal && bal.balance_usd == null && bal.key_remaining_usd != null && <Tile label={t('Остаток лимита ключа')} value={usd(bal.key_remaining_usd)} sub={t('из {v}', { v: usd(bal.key_limit_usd) })} />}
            <Tile label={t('Сегодня')} value={usd(ai.today.usd)} sub={sumSub(ai.today)} />
            <Tile label={t('7 дней')} value={usd(ai.week.usd)} sub={sumSub(ai.week)} />
            <Tile label={t('Месяц')} value={usd(ai.month.usd)} sub={sumSub(ai.month)} />
          </div>
          {bal?.error && <div className="alert alert-warn mt">{bal.error === 'invalid_key' ? t('Ключ не подходит: баланс не прочитать') : t('{p} не отвечает', { p: ai.provider })}</div>}
          {models.length > 0 && <div className="table-wrap mt"><table className="table table-tight" style={{ fontSize: 13 }}>
            <thead><tr><th>{t('Модель')}</th><th style={{ textAlign: 'right' }}>{t('Сегодня')}</th><th style={{ textAlign: 'right' }}>{t('7 дней')}</th><th style={{ textAlign: 'right' }}>{t('Месяц')}</th></tr></thead>
            <tbody>{models.map((m) => <tr key={m}><td style={mono}>{m}</td>{[ai.today, ai.week, ai.month].map((x, i) => <td key={i} style={{ textAlign: 'right', whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>{cell(x, m)}</td>)}</tr>)}</tbody>
          </table></div>}
          {ai.purposes && ai.purposes.month.rows.length > 0 && (() => {
            const ps = ai.purposes;
            const names = [...new Set([...ps.month.rows, ...ps.week.rows, ...ps.today.rows].map((r) => r.purpose))];
            const pc = (x: typeof ps.today, p: string) => { const r = x.rows.find((y) => y.purpose === p);
              return r ? <>{usd(r.usd)}<div className="faint" style={{ fontSize: 11 }}>{t('{calls} выз. · {tokens} ток.', { calls: num(r.calls), tokens: num(r.tokens) })}</div></> : '·'; };
            const avg = ps.week.per_search_usd;
            return <>
              <div className="table-wrap mt"><table className="table table-tight" style={{ fontSize: 13 }}>
                <thead><tr><th>{t('На что')}</th><th style={{ textAlign: 'right' }}>{t('Сегодня')}</th><th style={{ textAlign: 'right' }}>{t('7 дней')}</th><th style={{ textAlign: 'right' }}>{t('Месяц')}</th></tr></thead>
                <tbody>{names.map((p) => <tr key={p}><td>{PURPOSE[p] ? t(PURPOSE[p]) : p}</td>{[ps.today, ps.week, ps.month].map((x, i) => <td key={i} style={{ textAlign: 'right', whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>{pc(x, p)}</td>)}</tr>)}</tbody>
              </table></div>
              {avg != null && <p style={{ fontSize: 13, marginBottom: 0 }}>{t('Один подбор клиенту в среднем: {v} ({n} подборов за 7 дней)', { v: usd(avg, 4), n: num(ps.week.searches) })}</p>}
              <p className="faint" style={{ fontSize: 12, marginBottom: 0 }}>{t('Разбивка по назначению ведётся с 16.11.2026, поэтому может быть меньше общей суммы.')}</p>
            </>;
          })()}
          {ai.month.estimated && <p className="faint" style={{ fontSize: 12, marginBottom: 0 }}>{t('Вызовы, за которые провайдер не прислал стоимость, посчитаны по прайсу модели.')}</p>}
        </>}
        <div className="mt">{rows(by(AI_KEYS))}</div>
      </Card>

      <Card label={t('Парсеры Facebook, Threads, ss.ge')} title="Apify">
        {sp.error ? <ErrorBox error={sp.error} /> : sp.isLoading ? <Loading /> : s && <>
          <div style={tiles}>
            <Tile label={t('Сегодня')} value={usd(s.apify.today_usd)} />
            <Tile label={t('7 дней')} value={usd(s.apify.week_usd)} sub={Object.entries(s.apify.by_source_week).map(([k, v]) => `${k} ${usd(v)}`).join(' · ') || undefined} />
          </div>
          {s.apify.accounts.map((a) => (
            <div key={a.masked || a.tail} className="list-row" style={{ flexWrap: 'wrap' }}>
              <div className="grow" style={{ minWidth: 0, flexBasis: 200 }}>
                <div style={mono}>{a.masked || `…${a.tail}`}</div>
                {a.cycle_end && <div className="faint" style={{ fontSize: 12 }}>{t('месяц до {date}', { date: a.cycle_end })}</div>}
              </div>
              {a.error ? <Badge tone="warn">{a.error === 'invalid_key' ? t('ключ не работает') : t('Apify не отвечает')}</Badge>
                : <div style={{ minWidth: 140 }}><b>{usd(a.used_usd)}{a.limit_usd != null ? ` ${t('из')} ${usd(a.limit_usd)}` : ''}</b>
                  {a.limit_usd ? <Progress value={a.used_usd || 0} max={a.limit_usd} /> : null}</div>}
            </div>
          ))}
        </>}
        <div className="mt">{rows(by(APIFY_KEYS))}</div>
      </Card>

      <Card label={t('Подложка карт в CRM и на сайте')} title="MapTiler">{rows(by(MAP_KEYS))}</Card>

      <Card title={t('Другие ключи')}
        actions={<Button size="sm" variant="secondary" onClick={() => setEdit({ name: '', value: '', isNew: true })}><Plus size={15} />{t('Добавить ключ')}</Button>}>
        {rows(other)}
        <p className="faint" style={{ fontSize: 12, marginBottom: 0 }}>{t('Ключ из CRM главнее ключа на сервере и начинает работать в течение минуты, без перезапуска.')}</p>
      </Card>

      <Modal open={!!edit} onClose={() => setEdit(null)} title={edit?.isNew ? t('Новый ключ') : t(all.find((k) => k.name === edit?.name)?.title || edit?.name || '')}>
        {edit && <form className="col gap" onSubmit={(e) => { e.preventDefault(); put.mutate(); }}>
          {edit.isNew && <Field label={t('Какой ключ')}><Select value={edit.custom ? '_' : edit.name}
            onChange={(e) => setEdit({ ...edit, name: e.target.value === '_' ? '' : e.target.value, custom: e.target.value === '_' })}
            placeholder={t('Выберите…')}
            options={[...all.filter((k) => k.title).map((k) => ({ value: k.name, label: t(k.title!) })), { value: '_', label: t('Другой ключ (ввести имя)') }]} /></Field>}
          {edit.isNew && edit.custom && <Field label={t('Имя ключа латиницей, например THREADS_TOKEN')}><Input value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, '') })} required /></Field>}
          <Field label={t('Значение')}><Input type={all.find((k) => k.name === edit.name)?.secret === false ? 'text' : 'password'} autoComplete="off" value={edit.value} onChange={(e) => setEdit({ ...edit, value: e.target.value })} required /></Field>
          <Button type="submit" loading={put.isPending} disabled={!edit.name.trim() || !edit.value.trim()}>{t('Сохранить')}</Button>
        </form>}
      </Modal>
    </div>
  );
}
