// Управление → Права (superadmin, Stanislav 11-23): role × section flags Ч/С/И/У (backend services/perms.py). The
// superadmin always has every right and is not in the matrix; the Daily Rent and car operators keep only their sections.
// «Новая роль на основе …»: a copy of a ladder role's flags to take some off (Irakli's photo-only operator); it is given
// to a user in his card (Пользователи → Права), the rest of the CRM still treats him as the base role.
import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { errText, useToast } from '../components/toast';
import { Trash2 } from 'lucide-react';
import { useConfirm } from '../components/ActionDialog';
import type { Role } from '../api/types';
import { Button, Card, Checkbox, ErrorBox, Field, Input, Loading, Select } from '../components/ui';
import { ROLE_LABEL, fmtDateTime } from '../lib/format';
import { t } from '../lib/i18n';

const ACT: Record<string, string> = { r: 'Ч', c: 'С', u: 'И', d: 'У' };
const ACT_TITLE: Record<string, string> = { r: 'чтение', c: 'создание', u: 'изменение', d: 'удаление' };
const letters = (s: string) => [...s].map((a) => ACT[a]).join('') || '—';

export function PermsPage() {
  const toast = useToast();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['perms'], queryFn: api.perms.get });
  const hist = useQuery({ queryKey: ['perms', 'history'], queryFn: api.perms.history });
  const confirm = useConfirm();
  const [draft, setDraft] = useState<Record<string, Record<string, string>>>({});
  const [nr, setNr] = useState<{ label: string; base: Role }>({ label: '', base: 'operator' });
  const done = (m: Awaited<ReturnType<typeof api.perms.get>>) => { qc.setQueryData(['perms'], m); qc.invalidateQueries({ queryKey: ['perms', 'history'] }); };
  const create = useMutation({ mutationFn: () => api.perms.createRole(nr.label.trim(), nr.base), onSuccess: (m) => { done(m); setNr({ ...nr, label: '' }); toast(t('Роль создана')); }, onError: (e) => toast(errText(e), true) });
  const remove = useMutation({ mutationFn: (id: number) => api.perms.deleteRole(id), onSuccess: (m) => { done(m); toast(t('Роль удалена')); }, onError: (e) => toast(errText(e), true) });
  useEffect(() => { if (q.data) setDraft(q.data.matrix); }, [q.data]);
  const save = useMutation({
    mutationFn: () => api.perms.put(draft),
    onSuccess: (m) => { done(m); toast(t('Сохранено')); },
    onError: (e) => toast(errText(e), true),
  });
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error} />;
  const { sections, roles, custom, defaults } = q.data;
  const cols = [...roles.map((r) => ({ key: r as string, label: ROLE_LABEL[r], sub: '', id: 0, users: false })),
    ...custom.map((c) => ({ key: c.key, label: c.label, sub: t('на основе: {r}', { r: ROLE_LABEL[c.base] }), id: c.id, users: c.users }))];
  const roleName = (k: string) => cols.find((c) => c.key === k)?.label ?? ROLE_LABEL[k as Role] ?? k;
  const dirty = JSON.stringify(draft) !== JSON.stringify(q.data.matrix);
  const flip = (role: string, sec: string, a: string, on: boolean, order: string) => setDraft((d) => {
    const cur = d[role]?.[sec] ?? '';
    const next = [...order].filter((x) => (x === a ? on : cur.includes(x))).join('');
    return { ...d, [role]: { ...d[role], [sec]: next } };
  });
  return (
    <div className="stack">
      <Card label={t('Ч чтение · С создание · И изменение · У удаление')} title={t('Права ролей')}>
        <p className="muted" style={{ marginTop: 0 }}>{t('Суперадмин может всё. Оператор посуточной аренды и оператор авто видят только свои разделы.')}</p>
        <div style={{ overflowX: 'auto' }}>
          <table className="table">
            <thead><tr><th>{t('Раздел')}</th>{cols.map((c) => (
              <th key={c.key}><div className="row gap-sm" style={{ alignItems: 'flex-start' }}>
                <span>{c.label}{c.sub && <div className="faint" style={{ fontSize: 11, textTransform: 'none', letterSpacing: 0 }}>{c.sub}</div>}</span>
                {c.id > 0 && <Button size="sm" variant="ghost" aria-label={t('Удалить роль')} title={t('Удалить роль')} loading={remove.isPending && remove.variables === c.id}
                  onClick={async () => { if (await confirm(t('Удалить роль «{r}»?', { r: c.label }) + (c.users ? ' ' + t('Её пользователи получат права своей основной роли.') : ''))) remove.mutate(c.id); }}><Trash2 size={14} /></Button>}
              </div></th>))}</tr></thead>
            <tbody>{sections.map((s) => (
              <tr key={s.key}>
                <td style={{ fontWeight: 600 }}>{t(s.label)}</td>
                {cols.map(({ key: r }) => {
                  const v = draft[r]?.[s.key] ?? '';
                  const changed = v !== defaults[r]?.[s.key];
                  return (
                    <td key={r} title={changed ? t('По умолчанию: {v}', { v: letters(defaults[r]?.[s.key] ?? '') }) : undefined}
                        style={changed ? { background: 'var(--brand-tint)' } : undefined}>
                      <div className="row gap-sm" style={{ flexWrap: 'wrap' }}>
                        {[...s.actions].map((a) => (
                          <Checkbox key={a} checked={v.includes(a)} title={t(ACT_TITLE[a])} onChange={(on) => flip(r, s.key, a, on, s.actions)}>{t(ACT[a])}</Checkbox>
                        ))}
                      </div>
                    </td>
                  );
                })}
              </tr>
            ))}</tbody>
          </table>
        </div>
        <div className="row gap-sm mt">
          <Button loading={save.isPending} disabled={!dirty} onClick={() => save.mutate()}>{t('Сохранить права')}</Button>
          {dirty && <Button variant="ghost" onClick={() => setDraft(q.data.matrix)}>{t('Отменить')}</Button>}
          <Button variant="ghost" onClick={() => setDraft(defaults)}>{t('Как по умолчанию')}</Button>
        </div>
      </Card>
      <Card title={t('Новая роль')} label={t('Копия прав выбранной роли: снимите лишние флажки и назначьте её в карточке пользователя')}>
        <div className="form-grid">
          <Field label={t('Название')}><Input value={nr.label} placeholder={t('Оператор фото')} onChange={(e) => setNr({ ...nr, label: e.target.value })} /></Field>
          <Field label={t('На основе')}><Select value={nr.base} onChange={(e) => setNr({ ...nr, base: e.target.value as Role })} options={roles.map((r) => ({ value: r, label: ROLE_LABEL[r] }))} /></Field>
        </div>
        <div className="mt"><Button disabled={!nr.label.trim() || dirty} title={dirty ? t('Сначала сохраните права') : undefined} loading={create.isPending} onClick={() => create.mutate()}>{t('Создать роль')}</Button></div>
      </Card>
      <Card title={t('История изменений')}>
        {!hist.data?.length ? <p className="muted">{t('Права ещё не меняли')}</p> : hist.data.map((h, i) => (
          <div key={i} className="list-row" style={{ flexWrap: 'wrap' }}>
            <div className="grow">
              <b>{h.label ?? roleName(h.role)}</b>
              {h.action === 'role_created' && <div style={{ fontSize: 14 }}>{t('Роль создана на основе: {r}', { r: ROLE_LABEL[h.base as Role] ?? '' })}</div>}
              {h.action === 'role_deleted' && <div style={{ fontSize: 14 }}>{t('Роль удалена')}</div>}
              {Object.entries(h.changes).map(([sec, c]) => (
                <div key={sec} style={{ fontSize: 14 }}>{t(sections.find((s) => s.key === sec)?.label ?? sec)}: {letters(c.from)} → {letters(c.to)}</div>
              ))}
            </div>
            <div className="faint" style={{ fontSize: 12 }}>{fmtDateTime(h.at)}{h.by ? `, ${h.by}` : ''}</div>
          </div>
        ))}
      </Card>
    </div>
  );
}
