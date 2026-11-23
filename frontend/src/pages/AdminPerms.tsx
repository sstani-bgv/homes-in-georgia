// «Права» in a person's card (superadmin, Stanislav 11-23; backend services/perms.py): the standard role gives the
// standard rights, the superadmin ticks them off / on for this one person (Irakli's operator who only posts photos =
// an operator with «Договоры» off). Ч read · С create · И update · У delete; a cell changed from the role is tinted.
import { useQuery } from '@tanstack/react-query';
import { api } from '../api';
import type { Role } from '../api/types';
import { Button, Checkbox } from '../components/ui';
import { ROLE_LABEL, fmtDateTime } from '../lib/format';
import { t } from '../lib/i18n';

const ACT: Record<string, string> = { r: 'Ч', c: 'С', u: 'И', d: 'У' };
const ACT_TITLE: Record<string, string> = { r: 'чтение', c: 'создание', u: 'изменение', d: 'удаление' };
const letters = (s: string) => [...s].map((a) => ACT[a]).join('') || '—';

export function PersonPerms({ role, value, onChange, userId }: { role: Role; value?: Record<string, string>; onChange: (v: Record<string, string> | undefined) => void; userId?: number }) {
  const q = useQuery({ queryKey: ['perms'], queryFn: api.perms.get });
  const hist = useQuery({ queryKey: ['perms', 'history', userId], queryFn: () => api.perms.history(userId!), enabled: !!userId });
  const std = q.data?.defaults[role];
  if (!q.data || !std) return null;  // superadmin (everything) and the Daily Rent / car operators (their sections only)
  const v = { ...std, ...value };
  const own = q.data.sections.some((s) => v[s.key] !== std[s.key]);
  return (
    <div className="col gap-sm">
      <span className="label">{t('Права')} · {t('Ч чтение · С создание · И изменение · У удаление')}</span>
      <table className="table">
        <tbody>{q.data.sections.map((s) => {
          const changed = v[s.key] !== std[s.key];
          const tint = changed ? { background: 'var(--warn-soft)' } : undefined;
          return (
            <tr key={s.key}>
              <td style={{ fontWeight: 600, ...tint }}>{t(s.label)}</td>
              <td style={tint}><div className="row gap-sm" style={{ flexWrap: 'wrap' }}>
                {[...s.actions].map((a) => (
                  <Checkbox key={a} checked={v[s.key].includes(a)} title={t(ACT_TITLE[a])}
                    onChange={(on) => onChange({ ...v, [s.key]: [...s.actions].filter((x) => (x === a ? on : v[s.key].includes(x))).join('') })}>{t(ACT[a])}</Checkbox>
                ))}
              </div></td>
              <td className="faint" style={{ fontSize: 12, ...tint }}>{changed ? t('у роли «{r}»: {v}', { r: ROLE_LABEL[role], v: letters(std[s.key]) }) : t('как у роли')}</td>
            </tr>
          );
        })}</tbody>
      </table>
      {own && <div><Button size="sm" variant="ghost" onClick={() => onChange(undefined)}>{t('Как у роли')}</Button></div>}
      {!!hist.data?.length && (
        <details>
          <summary className="muted" style={{ cursor: 'pointer', fontSize: 13 }}>{t('История прав ({n})', { n: hist.data.length })}</summary>
          {hist.data.map((h, i) => (
            <div key={i} className="list-row" style={{ flexWrap: 'wrap', fontSize: 14 }}>
              <div className="grow">{Object.entries(h.changes).map(([sec, c]) => (
                <div key={sec}>{t(q.data.sections.find((s) => s.key === sec)?.label ?? sec)}: {letters(c.from)} → {letters(c.to)}</div>
              ))}</div>
              <div className="faint" style={{ fontSize: 12 }}>{fmtDateTime(h.at)}{h.by ? `, ${h.by}` : ''}{h.role ? ` · ${ROLE_LABEL[h.role as Role] ?? h.role}` : ''}</div>
            </div>
          ))}
        </details>
      )}
    </div>
  );
}
