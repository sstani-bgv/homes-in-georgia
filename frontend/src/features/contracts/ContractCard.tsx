// Stored contracts (backend services/contracts.py): the list, one contract with its PDF and lifecycle actions.
// PDF: open in the browser's viewer (Telegram: the expiring share link in the external browser), download, share.
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, Download, ExternalLink, FileText, Send, Share2, Upload, XCircle } from 'lucide-react';
import { api } from '../../api';
import { itemsOf } from '../../api/client';
import type { Contract, ContractCloseReason, ContractStatus } from '../../api/types';
import { insideTelegram, tgWebApp } from '../../auth/AuthContext';
import { errText, useToast } from '../../components/toast';
import { Badge, Button, Card, Empty, ErrorBox, Field, Input, Loading, Modal, Tabs, Textarea } from '../../components/ui';
import { fmtDate, fmtDateTime } from '../../lib/format';

export const CLOSE_REASONS: Record<ContractCloseReason, string> = {
  deal_failed: 'Сделка сорвалась', client_changed_mind: 'Клиент передумал', owner_refused: 'Собственник отказался', other: 'Другое',
};
const TONE: Record<ContractStatus, 'blue' | 'ok' | 'solid' | 'grey'> = { draft: 'blue', signed: 'ok', deal: 'solid', closed_no_deal: 'grey' };
const KIND: Record<Contract['kind'], string> = { rent: 'Аренда', comm: 'Коммерция', sale: 'Продажа' };

export function ContractStatusBadge({ c }: { c: { status: ContractStatus; status_label: string } }) {
  return <Badge tone={TONE[c.status]}>{c.status_label}</Badge>;
}

const absolute = (path: string) => `${window.location.origin}${path}`;
const mobile = () => window.matchMedia('(max-width: 767px)').matches;

function saveBlob(blob: Blob, name: string) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}

/** PDF + lifecycle buttons of one contract; used on the contract page and right after «Сохранить PDF» in the wizard. */
export function ContractActions({ c, onChange }: { c: Contract; onChange: (c: Contract) => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const tg = insideTelegram();
  const [signOpen, setSignOpen] = useState(false);
  const [closeOpen, setCloseOpen] = useState(false);
  const [link, setLink] = useState<string>(c.share ? absolute(c.share.path) : '');
  const done = (x: Contract, msg: string) => { onChange(x); qc.invalidateQueries({ queryKey: ['contracts'] }); toast(msg); };
  const shareLink = async () => {
    const x = c.share && new Date(c.share.expires_at).getTime() - Date.now() > 86_400_000 ? c : await api.contracts.share(c.id);
    if (x !== c) onChange(x);
    const url = absolute(x.share!.path);
    setLink(url);
    return url;
  };
  const busy = useMutation({ mutationFn: (fn: () => Promise<unknown>) => fn(), onError: (e) => toast(errText(e), true) });
  const closed = c.status === 'closed_no_deal';

  const open = () => busy.mutate(async () => {
    if (tg) { tgWebApp()?.openLink?.(await shareLink()); return; }  // the webview has no PDF viewer: the system browser
    const w = window.open('', '_blank');  // opened in the click, filled after the fetch (popup blockers)
    const url = URL.createObjectURL(await api.contracts.pdf(c.id, c.has_signed_pdf));
    if (w) w.location.href = url; else window.location.href = url;
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  });
  const download = () => busy.mutate(async () => {
    if (tg) { await api.contracts.sendPdf(c.id); toast('PDF отправлен вам в Telegram'); return; }
    saveBlob(await api.contracts.pdf(c.id, c.has_signed_pdf), c.filename);
  });
  const share = () => busy.mutate(async () => {
    // the file itself where the browser can (phones: Telegram / WhatsApp in the share sheet), else an expiring link
    if (!tg && navigator.canShare) {
      const file = new File([await api.contracts.pdf(c.id, c.has_signed_pdf)], c.filename, { type: 'application/pdf' });
      if (navigator.canShare({ files: [file] })) {
        try { await navigator.share({ files: [file], title: c.filename }); } catch { /* the user closed the sheet */ }
        return;
      }
    }
    const url = await shareLink();
    const text = `Договор ${c.cadastral || `#${c.id}`}`;
    if (tg) { tgWebApp()?.openTelegramLink?.(`https://t.me/share/url?url=${encodeURIComponent(url)}&text=${encodeURIComponent(text)}`); return; }
    if (navigator.share && mobile()) {
      try { await navigator.share({ url, title: text }); } catch { /* closed */ }
      return;
    }
    await navigator.clipboard?.writeText(url).then(() => toast('Ссылка на PDF скопирована'), () => undefined);
  });

  return (
    <div className="col gap-sm">
      <div className="row row-wrap gap-sm">
        <Button onClick={open} loading={busy.isPending}><ExternalLink size={16} />Открыть PDF</Button>
        <Button variant="secondary" onClick={download}>{tg ? <><Send size={16} />PDF в Telegram</> : <><Download size={16} />Скачать PDF</>}</Button>
        {!closed && <Button variant="secondary" onClick={share}><Share2 size={16} />Поделиться</Button>}
        {c.status === 'draft' && <Button variant="ok" onClick={() => setSignOpen(true)}><CheckCircle2 size={16} />Договор подписан</Button>}
        {(c.status === 'signed' || c.status === 'deal') && !c.has_signed_pdf && <Button variant="ghost" onClick={() => setSignOpen(true)}><Upload size={16} />Прикрепить скан</Button>}
        {(c.status === 'draft' || c.status === 'signed') && <Button variant="ghost" onClick={() => setCloseOpen(true)}><XCircle size={16} />Сделка не состоялась</Button>}
      </div>
      {link && !closed && (
        <div className="row row-wrap gap-sm" style={{ fontSize: 12 }}>
          <span className="muted">Ссылка на PDF (только просмотр, до {fmtDate(c.share?.expires_at)}):</span>
          <a href={link} target="_blank" rel="noreferrer" style={{ wordBreak: 'break-all' }}>{link}</a>
          <a className="btn btn-sm btn-ghost" href={`https://t.me/share/url?url=${encodeURIComponent(link)}`} target="_blank" rel="noreferrer"><Send size={14} />Telegram</a>
        </div>
      )}
      <SignModal c={c} open={signOpen} onClose={() => setSignOpen(false)} onDone={(x) => { setSignOpen(false); done(x, 'Отмечено: договор подписан'); }} />
      <CloseModal c={c} open={closeOpen} onClose={() => setCloseOpen(false)} onDone={(x) => { setCloseOpen(false); done(x, 'Договор закрыт: сделка не состоялась'); }} />
    </div>
  );
}

function SignModal({ c, open, onClose, onDone }: { c: Contract; open: boolean; onClose: () => void; onDone: (c: Contract) => void }) {
  const toast = useToast();
  const ref = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const m = useMutation({ mutationFn: () => api.contracts.sign(c.id, file), onSuccess: onDone, onError: (e) => toast(errText(e), true) });
  const attach = c.status !== 'draft';
  return (
    <Modal open={open} onClose={onClose} title={attach ? 'Скан подписанного договора' : 'Договор подписан'}>
      <div className="col gap">
        <div className="muted" style={{ fontSize: 13 }}>
          {attach ? 'Загрузите PDF подписанного договора.' : 'Прикрепите скан подписанного договора (PDF) или просто подтвердите подпись — скан можно добавить позже.'}
          {' '}PDF уйдёт в папку договоров на Google Drive.
        </div>
        <input ref={ref} type="file" accept="application/pdf,.pdf" hidden onChange={(e) => setFile(e.target.files?.[0] || null)} />
        <Button variant="secondary" onClick={() => ref.current?.click()}><Upload size={16} />{file ? file.name : 'Выбрать PDF'}</Button>
        <Button variant="ok" disabled={attach && !file} loading={m.isPending} onClick={() => m.mutate()}>
          <CheckCircle2 size={16} />{attach ? 'Загрузить' : file ? 'Подписан, загрузить скан' : 'Подтвердить без скана'}
        </Button>
      </div>
    </Modal>
  );
}

function CloseModal({ c, open, onClose, onDone }: { c: Contract; open: boolean; onClose: () => void; onDone: (c: Contract) => void }) {
  const toast = useToast();
  const [reason, setReason] = useState<ContractCloseReason | ''>('');
  const [comment, setComment] = useState('');
  useEffect(() => { if (open) { setReason(''); setComment(''); } }, [open]);
  const m = useMutation({ mutationFn: () => api.contracts.close(c.id, reason as ContractCloseReason, comment), onSuccess: onDone, onError: (e) => toast(errText(e), true) });
  const ok = !!reason && (reason !== 'other' || comment.trim());
  return (
    <Modal open={open} onClose={onClose} title="Сделка не состоялась">
      <div className="col gap">
        <div className="muted" style={{ fontSize: 13 }}>Договор уйдёт из активных и останется в истории со статусом «Сделка не состоялась».</div>
        <div className="col gap-sm" role="radiogroup" aria-label="Причина">
          {(Object.keys(CLOSE_REASONS) as ContractCloseReason[]).map((r) => (
            <label key={r} className={`choice ${reason === r ? 'on' : ''}`} style={{ padding: '10px 12px' }}>
              <input type="radio" name="close-reason" checked={reason === r} onChange={() => setReason(r)} /> <b>{CLOSE_REASONS[r]}</b>
            </label>
          ))}
        </div>
        <Field label={`Комментарий${reason === 'other' ? ' *' : ''}`}>
          <Textarea rows={3} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Что произошло" />
        </Field>
        <Button variant="danger" disabled={!ok} loading={m.isPending} onClick={() => m.mutate()}><XCircle size={16} />Закрыть договор</Button>
      </div>
    </Modal>
  );
}

/** /contracts?id=N */
export function ContractView({ id, base }: { id: number; base: string }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['contracts', 'one', id], queryFn: () => api.contracts.get(id) });
  const [pdf, setPdf] = useState('');
  const c = q.data;
  // desktop: the PDF inline (the browser's viewer); phones open it full screen with «Открыть PDF»
  useEffect(() => {
    if (!c || mobile() || insideTelegram()) return;
    let url = '';
    api.contracts.pdf(c.id, c.has_signed_pdf).then((b) => { url = URL.createObjectURL(b); setPdf(url); }, () => setPdf(''));
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [c?.id, c?.has_signed_pdf]);  // eslint-disable-line react-hooks/exhaustive-deps
  if (q.isLoading) return <Loading />;
  if (q.error || !c) return <ErrorBox error={q.error} />;
  const set = (x: Contract) => qc.setQueryData(['contracts', 'one', id], x);
  const items: [string, React.ReactNode][] = [
    ['Статус', <ContractStatusBadge c={c} />],
    ['Тип', `${KIND[c.kind]} · ${c.lang}`],
    ['Кадастровый код', c.cadastral || '—'],
    ['Адрес', c.address || '—'],
    ['Собственник', c.owner_name || '—'],
    ['Клиент', c.client_name || '—'],
    ['Создал', `${c.created_by?.name || '—'} · ${fmtDateTime(c.created_at)}`],
  ];
  if (c.signed_at) items.push(['Подписан', `${fmtDateTime(c.signed_at)}${c.signed_by ? ` · ${c.signed_by.name}` : ''}${c.has_signed_pdf ? ' · скан приложен' : ''}${c.in_drive ? ' · в Google Drive' : ''}`]);
  if (c.status === 'closed_no_deal') items.push(['Причина', `${c.close_reason_label}${c.close_comment ? `: ${c.close_comment}` : ''}`],
    ['Закрыл', `${c.closed_by?.name || '—'} · ${fmtDateTime(c.closed_at)}`]);
  return (
    <div className="col gap-lg">
      <Card label={`Договор #${c.id}`} title={c.address || c.cadastral || c.title || 'Договор'}
        actions={<Link className="btn btn-sm btn-ghost" to={base}>Все договоры</Link>}>
        <dl className="kv contract-kv">{items.map(([k, v]) => <div key={k} style={{ display: 'contents' }}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
        <div className="row row-wrap gap-sm mt" style={{ fontSize: 13 }}>
          {c.lead_id && <Link to={`${base.replace(/\/contracts$/, '')}/leads/${c.lead_id}`}>Заявка #{c.lead_id}</Link>}
          {c.deal_id && <Link to={`${base.replace(/\/contracts$/, '')}/deals/${c.deal_id}`}>Сделка #{c.deal_id}</Link>}
        </div>
        <div className="mt"><ContractActions c={c} onChange={set} /></div>
      </Card>
      {pdf && <iframe title="PDF договора" src={pdf} className="contract-pdf-frame" />}
    </div>
  );
}

const TABS = [
  { value: 'active', label: 'Активные' }, { value: 'deal', label: 'Сделки' },
  { value: 'closed_no_deal', label: 'Сделка не состоялась' }, { value: 'all', label: 'Все' },
] as const;

/** /contracts: the agent's (staff: all) contracts; closed ones only under «Сделка не состоялась» / «Все». */
export function ContractList({ base }: { base: string }) {
  const [status, setStatus] = useState<(typeof TABS)[number]['value']>('active');
  const [text, setText] = useState('');
  const q = useQuery({ queryKey: ['contracts', 'list', status, text], queryFn: () => api.contracts.list({ status, q: text.trim() || undefined }) });
  const rows = itemsOf(q.data);
  return (
    <Card label="История" title="Договоры">
      <Tabs value={status} onChange={setStatus} tabs={TABS.map((t) => ({ ...t }))} />
      <Input className="input mt" placeholder="Кадастровый код, адрес, клиент, собственник" value={text} onChange={(e) => setText(e.target.value)} />
      {q.isLoading ? <Loading /> : q.error ? <ErrorBox error={q.error} /> : rows.length === 0 ? <Empty text="Договоров нет" icon={<FileText size={22} />} /> : (
        <div className="col gap-sm mt">
          {rows.map((c) => (
            <Link key={c.id} to={`${base}?id=${c.id}`} className="contract-row">
              <span className="grow">
                <b>{c.address || c.cadastral || c.title || `Договор #${c.id}`}</b>
                <small className="muted">
                  #{c.id} · {KIND[c.kind]} {c.lang}{c.cadastral ? ` · ${c.cadastral}` : ''}{c.client_name ? ` · ${c.client_name}` : ''} · {c.created_by?.name || '—'}, {fmtDate(c.created_at)}
                </small>
                {c.status === 'closed_no_deal' && (
                  <small className="muted">{c.close_reason_label}{c.close_comment ? `: ${c.close_comment}` : ''} · {c.closed_by?.name || '—'}, {fmtDate(c.closed_at)}</small>
                )}
              </span>
              <ContractStatusBadge c={c} />
            </Link>
          ))}
        </div>
      )}
    </Card>
  );
}
