// NAPR (Public Registry) applications by the owner's personal ID: the owner's sign page and the CRM contract generator.
// The list comes from our backend (services/registry.py; NAPR's search has no CORS); a tap opens the application on NAPR
// itself, where the person ticks its reCAPTCHA and downloads the extract / copies the cadastral code.
import { useEffect, useRef, useState } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { CheckCircle2, ExternalLink } from 'lucide-react';
import { http } from '../api/client';
import { errText } from './toast';
import { insideTelegram, tgWebApp } from '../auth/AuthContext';
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

// «01.14.01.001.122.01.01.059», optionally a Georgian letter and «/2» (backend services/registry.py _CODE); the longest
// one wins: an extract or a copied page also names the land plot's shorter code
const CODE_RE = /(?<![\d.])\d{1,3}(?:\.\d{1,6}){3,8}\p{L}?(?:\/\d{1,4}\p{L}?)?(?!\d)/gu;
export const findCadastral = (text: string) => (text.match(CODE_RE) || []).sort((a, b) => b.length - a.length)[0] || '';
// the extract's «download» link on NAPR (https://bs.napr.gov.ge/GetBlob?pid=…&bid=…; backend registry.EXTRACT_HOSTS)
export const findExtractLink = (text: string) => text.match(/https?:\/\/(?:[\w-]+\.)*(?:napr|reestri)\.gov\.ge\/\S+/i)?.[0] || '';

/** onCode: after the agent opened an application on NAPR and comes back to this tab, the clipboard is read and a
 * cadastral code in it is handed over by itself. Where the browser wants a tap first (iOS Safari, Telegram), one big
 * button does the same. The CAPTCHA on NAPR stays the person's own job.
 * onLink (the contract wizard): a copied extract link wins over a code — the server downloads and parses the extract;
 * it resolves to the note shown under the button. */
function useClipboardCode(onCode?: (code: string) => void, onLink?: (url: string) => Promise<string>) {
  const [armed, setArmed] = useState(false);  // an application was opened, nothing pasted yet
  const [note, setNote] = useState('');
  const busy = useRef(false);  // focus and visibilitychange come together on return
  const lastLink = useRef('');  // on return the same link isn't loaded twice; a tap reloads it
  const read = async (byTap: boolean) => {
    if (busy.current) return;
    busy.current = true;
    try {
      const text = await navigator.clipboard.readText();
      const link = onLink && findExtractLink(text);
      if (link && (byTap || link !== lastLink.current)) {
        lastLink.current = link;
        setNote('Загружаю выписку…');
        setNote(await onLink(link).catch((e) => errText(e)));
        return;
      }
      if (link) return;
      const code = findCadastral(text);
      if (code) { onCode?.(code); setArmed(false); setNote(''); return; }
      if (byTap) setNote(onLink ? 'В буфере нет ссылки на выписку: на сайте реестра скопируйте ссылку кнопки скачивания выписки.' : 'В буфере нет кадастрового кода: скопируйте его на странице реестра.');
    } catch {
      if (byTap) setNote(onLink ? 'Браузер не дал прочитать буфер: вставьте ссылку в поле «Ссылка на выписку».' : 'Браузер не дал прочитать буфер: вставьте код в поле вручную.');
    } finally {
      busy.current = false;
    }
  };
  useEffect(() => {
    if (!armed || !onCode) return;
    const back = () => { if (document.visibilityState === 'visible') void read(false); };
    document.addEventListener('visibilitychange', back);
    window.addEventListener('focus', back);
    return () => { document.removeEventListener('visibilitychange', back); window.removeEventListener('focus', back); };
  }, [armed, onCode]);  // eslint-disable-line react-hooks/exhaustive-deps
  return { armed: armed && !!onCode, busy: note === 'Загружаю выписку…', arm: () => { setArmed(true); setNote(''); }, read, note };
}

export function RegistryApps({ base, personalId, picked, onPick, onCode, onLink, t }: { base: string; personalId: string; picked?: RegApp | null; onPick?: (a: RegApp) => void; onCode?: (code: string) => void; onLink?: (url: string) => Promise<string>; t: RegTexts }) {
  const clip = useClipboardCode(onCode, onLink);
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
      {clip.armed && (
        <div className="col gap-sm">
          <Button loading={clip.busy} onClick={() => void clip.read(true)}>{onLink ? 'Вставить ссылку на выписку' : 'Вставить кадастровый код из реестра'}</Button>
          <span className="faint" style={{ fontSize: 12 }}>{clip.note || (onLink ? 'Скопируйте на сайте реестра ссылку на выписку и вернитесь сюда — выписка загрузится и заполнит поля сама.' : 'Скопируйте код на странице реестра и вернитесь сюда — он подставится сам.')}</span>
        </div>
      )}
      {items.map((a) => {
        const on = picked?.app_id === a.app_id;
        return (
          <a key={a.app_id} href={a.url} target="_blank" rel="noopener noreferrer"
            onClick={(e) => { onPick?.(a); clip.arm(); if (insideTelegram()) { e.preventDefault(); tgWebApp()?.openLink?.(a.url); } }} className="card card-pad col"
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

