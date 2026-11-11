// NAPR (Public Registry) applications by the owner's personal ID: the owner's sign page and the CRM contract generator.
// The list comes from our backend (services/registry.py; NAPR's search has no CORS); a tap opens the application on NAPR
// itself, where the person ticks its reCAPTCHA and downloads the extract / copies the cadastral code.
import { useEffect, useState } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { CheckCircle2, ExternalLink } from 'lucide-react';
import { http } from '../api/client';
import { Button, Loading } from './ui';

export interface RegTexts { listTitle: string; listHint: string; open: string; picked: string; more: string; none: string; down: string }
export const REG_RU: RegTexts = { listTitle: 'Заявки собственника в Публичном реестре', listHint: 'Откройте заявку по этому объекту (адрес в карточке): на сайте реестра отметьте «Я не робот» — там кадастровый код и выписка.', open: 'Открыть', picked: 'Открыта', more: 'Показать ещё', none: 'По этому личному номеру заявок не найдено.', down: 'Реестр сейчас не отвечает.' };

/** value: a personal ID; the list waits until it's 9/11 digits and settled for a moment. */
export function useSettledId(value: string): string {
  const [v, setV] = useState('');
  useEffect(() => {
    const d = value.replace(/\D/g, '');
    const h = setTimeout(() => setV(d.length === 11 || d.length === 9 ? d : ''), 600);
    return () => clearTimeout(h);
  }, [value]);
  return v;
}

export const day = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString('ru-RU', { timeZone: 'Asia/Tbilisi' }) : '');
export interface RegApp { app_id: string; reg_number: string; date: string | null; type: string; address: string; status: string; done: boolean; applicants: string[]; url: string }
interface RegPage { total: number; page: number; lastpage: number; items: RegApp[] }

export function RegistryApps({ base, personalId, picked, onPick, t }: { base: string; personalId: string; picked?: RegApp | null; onPick?: (a: RegApp) => void; t: RegTexts }) {
  const q = useInfiniteQuery({
    queryKey: ['napr', base, personalId], enabled: !!personalId, retry: false, staleTime: 3600_000, initialPageParam: 1,
    queryFn: ({ pageParam }) => http.get<RegPage>(`${base}?personal_id=${personalId}&page=${pageParam}`),
    getNextPageParam: (last) => (last.page < last.lastpage ? last.page + 1 : undefined),
  });
  if (!personalId) return null;
  if (q.isLoading) return <Loading />;
  const items = q.data?.pages.flatMap((p) => p.items) || [];
  if (q.isError || !items.length) return <span className="faint" style={{ fontSize: 12 }}>{q.isError ? t.down : t.none}</span>;
  return (
    <div className="col gap-sm">
      <span className="label">{t.listTitle}</span>
      <span className="faint" style={{ fontSize: 12 }}>{t.listHint}</span>
      {items.map((a) => {
        const on = picked?.app_id === a.app_id;
        return (
          <a key={a.app_id} href={a.url} target="_blank" rel="noreferrer" onClick={() => onPick?.(a)} className="card card-pad col"
            style={{ gap: 2, textDecoration: 'none', color: 'inherit', border: `1.5px solid ${on ? 'var(--ok, #1a7f4b)' : 'var(--line, #e3e8f0)'}`, padding: 12 }}>
            <span className="row row-between gap-sm">
              <b>{day(a.date)} · №{a.reg_number}</b>
              <span className="row gap-sm" style={{ fontSize: 13, whiteSpace: 'nowrap' }}>{on ? <><CheckCircle2 size={14} color="var(--ok, #1a7f4b)" />{t.picked}</> : <><ExternalLink size={14} />{t.open}</>}</span>
            </span>
            <span style={{ fontSize: 13 }}>{a.type}</span>
            {a.address && <span className="faint" style={{ fontSize: 12 }}>{a.address}</span>}
            <span style={{ fontSize: 12, color: a.done ? 'var(--ok, #1a7f4b)' : undefined }} className={a.done ? undefined : 'faint'}>{a.status}</span>
          </a>
        );
      })}
      {q.hasNextPage && <Button variant="secondary" size="sm" loading={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>{t.more}</Button>}
    </div>
  );
}

