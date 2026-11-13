// The contract wizard, «Объект» tab: the cadastral code by the owner's personal ID.
//  1. The CRM's own records (backend registry.known: earlier contracts, e-signatures, exclusive confirmations) — the
//     code fills in by itself, no registry, no CAPTCHA.
//  2. Otherwise the owner's applications in the Public Registry (NAPR): open one, tick its CAPTCHA, copy the whole page
//     and press «Вставить из реестра» — the code is found in the text (backend registry.code_from_text); or upload the
//     extract (PDF). The CRM can't read the NAPR page itself (another site, behind NAPR's own CAPTCHA).
import { useRef, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { CheckCircle2, ClipboardPaste, Database, Link2, Upload } from 'lucide-react';
import { http } from '../../api/client';
import { errText, useToast } from '../../components/toast';
import { Button, Input, Modal, Textarea } from '../../components/ui';
import { REG_RU, RegistryApps, day } from '../../components/RegistryApps';

export interface KnownObject {
  cadastral: string; address: string | null; floor: string | null; apartment_number: string | null; area: string | null;
  owner_name: string | null; source: string; date: string | null;
}

/** What a registry extract / the pasted NAPR page gave (backend registry.parse_extract): only the keys found. */
export type ExtractFields = Partial<Record<'cadastral_code' | 'street_house' | 'floor' | 'apartment_number' | 'apartment_area'
  | 'owner_full_name' | 'owner_personal_number' | 'address', string>>;
interface ExtractOut { cadastral_code: string | null; fields: ExtractFields }
export const parseRegistryText = (text: string) => http.post<ExtractOut>('/registry/parse-text', { text: text.slice(0, 500_000) });

export function useKnownObjects(personalId: string) {
  return useQuery({
    queryKey: ['registry-known', personalId], enabled: !!personalId, retry: false, staleTime: 60_000,
    queryFn: () => http.get<{ items: KnownObject[] }>('/registry/known', { personal_id: personalId }).then((r) => r.items),
  });
}

interface UrlOut extends ExtractOut { file: string; url: string }

/** The extract by its link (NAPR's «download» PDF, https://bs.napr.gov.ge/GetBlob?…): pasted here or by the big
 * button under the registry applications (go) → the server downloads it, fills the empty fields and keeps the PDF;
 * the link and the file go into the contract's data (onSaved). go resolves to the note, rejects with the error. */
function useExtractLink(saved: string | undefined, onFields: (f: ExtractFields) => string[], onSaved: (url: string, file: string) => void) {
  const [url, setUrl] = useState(saved || '');
  const [note, setNote] = useState<{ text: string; bad?: boolean } | null>(saved ? { text: 'Выписка сохранена с договором' } : null);
  const load = useMutation({ mutationFn: (u: string) => http.post<UrlOut>('/registry/extract-from-url', { url: u }) });
  const go = async (u: string) => {
    const t = u.trim();
    if (!t) return '';
    setUrl(t);
    setNote(null);
    let text: string;
    try {
      const r = await load.mutateAsync(t);
      onSaved(r.url, r.file);
      const names = Object.keys(r.fields).length ? onFields(r.fields) : null;
      text = !names ? 'Скан без текста — выписка сохранена, заполните поля вручную'
        : names.length ? `Заполнено из выписки: ${names.join(', ')}` : 'Выписка сохранена; поля уже были заполнены — ничего не изменено';
      setNote({ text, bad: !names });
    } catch (e) {
      setNote({ text: errText(e), bad: true });
      throw e;
    }
    return text;
  };
  return { url, setUrl, note, load, go };
}

function ExtractLink({ x: { url, setUrl, note, load, go } }: { x: ReturnType<typeof useExtractLink> }) {
  const run = (u: string) => void go(u).catch(() => undefined);  // the error is in note
  return (
    <div className="col gap-sm">
      <span className="label"><Link2 size={14} style={{ verticalAlign: -2, marginRight: 4 }} />Ссылка на выписку</span>
      <div className="row gap-sm">
        <Input style={{ flex: 1, minWidth: 0 }} inputMode="url" value={url} placeholder="https://bs.napr.gov.ge/GetBlob?…" onChange={(e) => setUrl(e.target.value)}
          onPaste={(e) => { const t = e.clipboardData.getData('text').trim(); if (t) { e.preventDefault(); run(t); } }}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); run(url); } }} />
        <Button loading={load.isPending} disabled={!url.trim()} onClick={() => run(url)}>Загрузить</Button>
      </div>
      {note && <span style={{ fontSize: 13, color: note.bad ? 'var(--danger, #c0392b)' : 'var(--ok, #1a7f4b)' }}>{note.text}</span>}
    </div>
  );
}

/** onCode: a cadastral code found in the clipboard on return from NAPR (RegistryApps, 09736a7).
 * onFields fills the empty fields and returns their names; onExtract keeps the extract's link + saved PDF. */
export function OwnerLookup({ personalId, current, onKnown, onFields, onCode, extractUrl, onExtract }: {
  personalId: string; current: string; onKnown: (o: KnownObject) => void; onFields: (f: ExtractFields) => string[]; onCode?: (code: string) => void;
  extractUrl?: string; onExtract: (url: string, file: string) => void;
}) {
  const toast = useToast();
  const known = useKnownObjects(personalId);
  const file = useRef<HTMLInputElement>(null);
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasted, setPasted] = useState('');
  const parse = useMutation({
    mutationFn: parseRegistryText,
    onSuccess: (r) => {
      if (Object.keys(r.fields).length) { onFields(r.fields); setPasteOpen(false); setPasted(''); }
      else { setPasteOpen(true); toast('В тексте не нашлось данных реестра. Скопируйте всю страницу заявки после «Я не робот» или текст выписки', true); }
    },
    onError: (e) => toast(errText(e), true),
  });
  // the clipboard straight away where the browser allows it (Safari asks «Вставить»); else a box to paste into
  const pasteFromRegistry = async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (text.trim()) { parse.mutate(text); return; }
    } catch { /* not allowed here (Telegram, an older browser): paste by hand */ }
    setPasteOpen(true);
  };
  const read = useMutation({
    mutationFn: (f: File) => {
      const fd = new FormData();
      fd.append('file', f);
      return http.post<ExtractOut>('/registry/extract-code', fd);
    },
    onSuccess: (r) => {
      if (Object.keys(r.fields).length) onFields(r.fields);
      else toast('В выписке не нашлось данных (возможно, это скан) — впишите их вручную', true);
    },
    onError: (e) => toast(errText(e), true),
  });
  const x = useExtractLink(extractUrl, onFields, onExtract);
  const link = <ExtractLink x={x} />;
  if (!personalId) {
    return (
      <div className="col gap">
        {link}
        <span className="faint" style={{ fontSize: 12 }}>Или введите личный номер собственника на вкладке «Стороны» — кадастровый код подставится из базы CRM или из Публичного реестра.</span>
      </div>
    );
  }
  const items = known.data || [];
  return (
    <div className="col gap">
      {link}
      {items.length > 0 && (
        <div className="col gap-sm">
          <span className="label"><Database size={14} style={{ verticalAlign: -2, marginRight: 4 }} />Уже есть в базе CRM</span>
          {items.map((o) => {
            const on = o.cadastral === current;
            return (
              <div key={o.cadastral} className="card card-pad row row-between row-wrap gap-sm"
                style={{ padding: 12, border: `1.5px solid ${on ? 'var(--ok, #1a7f4b)' : 'var(--line, #e3e8f0)'}` }}>
                <span className="col" style={{ gap: 2, minWidth: 0 }}>
                  <b>{o.cadastral}</b>
                  <span style={{ fontSize: 13 }}>{[o.address, o.floor && `этаж ${o.floor}`, o.apartment_number && `кв. ${o.apartment_number}`, o.area && `${o.area} м²`].filter(Boolean).join(' · ') || '—'}</span>
                  <span className="faint" style={{ fontSize: 12 }}>{o.owner_name ? `${o.owner_name} · ` : ''}{o.source}{o.date ? `, ${day(o.date)}` : ''}</span>
                </span>
                {on ? <span className="row gap-sm" style={{ fontSize: 13, color: 'var(--ok, #1a7f4b)' }}><CheckCircle2 size={14} />Подставлен</span>
                  : <Button size="sm" variant="secondary" onClick={() => onKnown(o)}>Подставить</Button>}
              </div>
            );
          })}
        </div>
      )}
      <RegistryApps base="/registry/applications" personalId={personalId} onCode={onCode} onLink={x.go} t={{ ...REG_RU, listTitle: items.length ? 'Другие объекты — заявки в Публичном реестре' : REG_RU.listTitle,
        listHint: 'Откройте заявку по этому объекту и на сайте реестра отметьте «Я не робот». Скопируйте ссылку на выписку (кнопка скачивания: долгое нажатие → «Скопировать ссылку») и вернитесь сюда — выписка загрузится сама, или нажмите «Вставить ссылку на выписку». Если ссылки нет — выделите всю страницу и скопируйте (телефон: долгое нажатие → «Выбрать всё» → «Скопировать»; компьютер: Cmd/Ctrl+A, Cmd/Ctrl+C) и нажмите «Вставить из реестра». Или загрузите выписку (PDF), сохранённую в телефоне. Подставятся код, адрес, этаж, квартира, площадь и собственник.' }} />
      <input ref={file} type="file" accept="application/pdf,.pdf" hidden onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ''; if (f) read.mutate(f); }} />
      <div className="row row-wrap gap-sm">
        <Button loading={parse.isPending} onClick={pasteFromRegistry}><ClipboardPaste size={16} />Вставить из реестра</Button>
        <Button variant="secondary" loading={read.isPending} onClick={() => file.current?.click()}><Upload size={16} />Загрузить выписку (PDF)</Button>
      </div>
      <Modal open={pasteOpen} onClose={() => setPasteOpen(false)} title="Вставить из реестра">
        <div className="col gap">
          <span className="muted" style={{ fontSize: 13 }}>Вставьте сюда текст страницы заявки из реестра (долгое нажатие → «Вставить»). Кадастровый код найдётся сам.</span>
          <Textarea rows={6} value={pasted} onChange={(e) => setPasted(e.target.value)} placeholder="Текст страницы реестра"
            onPaste={(e) => { const t = e.clipboardData.getData('text'); if (t.trim()) { e.preventDefault(); setPasted(t); parse.mutate(t); } }} />
          <Button disabled={!pasted.trim()} loading={parse.isPending} onClick={() => parse.mutate(pasted)}>Найти код</Button>
        </div>
      </Modal>
    </div>
  );
}
