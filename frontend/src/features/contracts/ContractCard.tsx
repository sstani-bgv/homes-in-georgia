// Stored contracts (backend services/contracts.py): the list, one contract with its PDF and lifecycle actions.
// PDF: open in the browser's viewer (Telegram: the expiring share link in the external browser), download, share.
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, Copy, Download, ExternalLink, FileText, Pencil, PenLine, Send, Share2, Upload, XCircle } from 'lucide-react';
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
const KIND: Record<NonNullable<Contract['kind']>, string> = { rent: 'Аренда', comm: 'Коммерция', sale: 'Продажа' };
const kindLang = (c: Contract) => [c.kind && KIND[c.kind], c.lang].filter(Boolean).join(' ') || 'тип не выбран';

/** «Черновик» (being filled, no PDF) / «Сгенерирован» / «Договор подписан» / «Сделка» / «Сделка не состоялась» */
export function ContractStatusBadge({ c }: { c: { status: ContractStatus; status_label: string; has_pdf?: boolean } }) {
  return <Badge tone={c.status === 'draft' && c.has_pdf === false ? 'warn' : TONE[c.status]}>{c.status_label}</Badge>;
}

/** The form for a draft: a draft being filled continues, a generated one is edited and regenerated. */
export const editLink = (base: string, c: { id: number }) => `${base}?edit=${c.id}`;

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
        {c.status === 'draft' && !c.pdf_stale && <Button variant="ok" onClick={() => setSignOpen(true)}><CheckCircle2 size={16} />Договор подписан</Button>}
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
      <ESignBlock c={c} onChange={(x) => { onChange(x); qc.invalidateQueries({ queryKey: ['contracts'] }); }} />
      <SignModal c={c} open={signOpen} onClose={() => setSignOpen(false)} onDone={(x) => { setSignOpen(false); done(x, 'Отмечено: договор подписан'); }} />
      <CloseModal c={c} open={closeOpen} onClose={() => setCloseOpen(false)} onDone={(x) => { setCloseOpen(false); done(x, 'Договор закрыт: сделка не состоялась'); }} />
    </div>
  );
}

/** Electronic signing (backend services/esign.py): a personal link for the owner and one for the tenant; when both
 *  have signed, the signed PDF replaces the draft and the contract becomes «Договор подписан» by itself. */
function ESignBlock({ c, onChange }: { c: Contract; onChange: (c: Contract) => void }) {
  const toast = useToast();
  const tg = insideTelegram();
  const es = c.esign;
  const m = useMutation({ mutationFn: (fn: () => Promise<Contract>) => fn(), onSuccess: onChange, onError: (e) => toast(errText(e), true) });
  if (c.status !== 'draft' && !es) return null;
  if (c.kind === 'sale' && !es) return null;
  if (!es) {
    return (
      <div className="row row-wrap gap-sm">
        <Button variant="secondary" loading={m.isPending} onClick={() => m.mutate(() => api.contracts.esign(c.id))}>
          <PenLine size={16} />Подписать электронно
        </Button>
        <span className="muted" style={{ fontSize: 12 }}>Собственник и арендатор получат по ссылке и распишутся с телефона</span>
      </div>
    );
  }
  const text = (label: string) => `Договор ${c.address || c.cadastral || `#${c.id}`}: подпишите, пожалуйста (${label.toLowerCase()})`;
  const send = async (url: string, label: string) => {
    if (tg) { tgWebApp()?.openTelegramLink?.(`https://t.me/share/url?url=${encodeURIComponent(url)}&text=${encodeURIComponent(text(label))}`); return; }
    if (navigator.share && mobile()) {
      try { await navigator.share({ url, title: text(label) }); } catch { /* closed */ }
      return;
    }
    await navigator.clipboard?.writeText(url).then(() => toast('Ссылка скопирована'), () => undefined);
  };
  return (
    <div className="col gap-sm" style={{ border: '1px solid var(--line, #dfe4ee)', borderRadius: 12, padding: 12 }}>
      <b><PenLine size={16} style={{ verticalAlign: -3, marginRight: 6 }} />Электронная подпись{es.complete ? ': подписан обеими сторонами' : ''}</b>
      {es.signers.map((s) => {
        const url = s.path ? absolute(s.path) : '';
        return (
          <div key={s.role} className="row row-wrap gap-sm" style={{ alignItems: 'center' }}>
            <span style={{ minWidth: 110 }}>{s.label}</span>
            {s.signed_at
              ? <Badge tone="ok">подписал(а) · {s.full_name} · {fmtDateTime(s.signed_at)}</Badge>
              : <Badge tone="grey">ожидает подписи</Badge>}
            {url && !s.signed_at && <>
              <Button size="sm" variant="secondary" onClick={() => send(url, s.label)}>{tg ? <><Send size={14} />Отправить</> : <><Share2 size={14} />Отправить ссылку</>}</Button>
              {!tg && <Button size="sm" variant="ghost" onClick={() => navigator.clipboard?.writeText(url).then(() => toast('Ссылка скопирована'), () => undefined)}><Copy size={14} />Копировать</Button>}
              {!tg && <a className="btn btn-sm btn-ghost" href={`https://t.me/share/url?url=${encodeURIComponent(url)}&text=${encodeURIComponent(text(s.label))}`} target="_blank" rel="noreferrer"><Send size={14} />Telegram</a>}
            </>}
          </div>
        );
      })}
      {c.status === 'draft' && (
        <div className="row row-wrap gap-sm">
          <span className="muted grow" style={{ fontSize: 12 }}>Ссылки личные: каждая сторона видит договор, вводит ФИО, личный номер, телефон и расписывается. Правка текста отменяет неподписанные ссылки.</span>
          <Button size="sm" variant="ghost" loading={m.isPending} onClick={() => {
            if (window.confirm(es.signers.some((s) => s.signed_at) ? 'Отменить подписание? Уже данные подписи будут удалены.' : 'Отменить ссылки на подпись?')) m.mutate(() => api.contracts.esignCancel(c.id));
          }}><XCircle size={14} />Отменить подписание</Button>
        </div>
      )}
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
    if (!c || !c.has_pdf || mobile() || insideTelegram()) return;
    let url = '';
    api.contracts.pdf(c.id, c.has_signed_pdf).then((b) => { url = URL.createObjectURL(b); setPdf(url); }, () => setPdf(''));
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [c?.id, c?.has_signed_pdf, c?.generated_at]);  // eslint-disable-line react-hooks/exhaustive-deps
  if (q.isLoading) return <Loading />;
  if (q.error || !c) return <ErrorBox error={q.error} />;
  const set = (x: Contract) => qc.setQueryData(['contracts', 'one', id], x);
  const items: [string, React.ReactNode][] = [
    ['Статус', <ContractStatusBadge c={c} />],
    ['Тип', kindLang(c)],
    ['Кадастровый код', c.cadastral || '—'],
    ['Адрес', c.address || '—'],
    ['Собственник', c.owner_name || '—'],
    ['Клиент', c.client_name || '—'],
    ['Создал', `${c.created_by?.name || '—'} · ${fmtDateTime(c.created_at)}`],
  ];
  if (c.generated_at) items.push(['Сгенерирован', `${fmtDateTime(c.generated_at)}${c.versions?.length ? ` · версия ${c.versions.length + 1}` : ''}`]);
  const openVersion = async (n: number) => {
    const w = window.open('', '_blank');
    const url = URL.createObjectURL(await api.contracts.pdf(c.id, false, n));
    if (w) w.location.href = url; else window.location.href = url;
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  };
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
        {c.status === 'draft' && (
          <div className="row row-wrap gap-sm mt">
            <Link className={`btn ${c.has_pdf ? 'btn-secondary' : ''}`} to={editLink(base, c)}><Pencil size={16} />{c.has_pdf ? 'Изменить данные и перегенерировать' : 'Продолжить заполнение'}</Link>
            {c.pdf_stale && <span className="alert alert-warn" style={{ margin: 0 }}>Данные изменены после генерации: откройте форму и нажмите «Обновить PDF»</span>}
          </div>
        )}
        {c.has_pdf ? <div className="mt"><ContractActions c={c} onChange={set} /></div>
          : <div className="muted mt" style={{ fontSize: 13 }}>Черновик: форма сохраняется автоматически, PDF появится после «Сохранить PDF» на шаге «Документ».</div>}
        {!!c.versions?.length && (
          <div className="col gap-sm mt" style={{ fontSize: 13 }}>
            <span className="label">Прошлые версии PDF</span>
            {c.versions.slice().reverse().map((v) => (
              <div key={v.n} className="row gap-sm">
                <span className="muted">Версия {v.n + 1} · {fmtDateTime(v.at)}{v.by ? ` · ${v.by.name}` : ''}</span>
                <Button size="sm" variant="ghost" onClick={() => openVersion(v.n)}><ExternalLink size={14} />Открыть</Button>
              </div>
            ))}
          </div>
        )}
      </Card>
      {pdf && <iframe title="PDF договора" src={pdf} className="contract-pdf-frame" />}
    </div>
  );
}

const TABS = [
  { value: 'active', label: 'Активные' }, { value: 'deal', label: 'Сделки' },
  { value: 'closed_no_deal', label: 'Сделка не состоялась' }, { value: 'all', label: 'Все' },
] as const;

/** /contracts and the cabinet: the agent's (staff: all, or agentId's) contracts by deal; closed ones only under
 *  «Сделка не состоялась» / «Все». A draft opens in the form (continue / edit and regenerate), the rest on its page. */
export function ContractList({ base, agentId, label = 'История', title = 'Договоры' }: { base: string; agentId?: number; label?: string; title?: string }) {
  const [status, setStatus] = useState<(typeof TABS)[number]['value']>('active');
  const [text, setText] = useState('');
  const q = useQuery({ queryKey: ['contracts', 'list', status, text, agentId], queryFn: () => api.contracts.list({ status, q: text.trim() || undefined, agent_id: agentId }) });
  const rows = itemsOf(q.data);
  const root = base.replace(/\/contracts$/, '');
  return (
    <Card label={label} title={title}>
      <Tabs value={status} onChange={setStatus} tabs={TABS.map((t) => ({ ...t }))} />
      <Input className="input mt" placeholder="Кадастровый код, адрес, клиент, собственник" value={text} onChange={(e) => setText(e.target.value)} />
      {q.isLoading ? <Loading /> : q.error ? <ErrorBox error={q.error} /> : rows.length === 0 ? <Empty text="Договоров нет" icon={<FileText size={22} />} /> : (
        <div className="col gap-sm mt">
          {rows.map((c) => (
            <div key={c.id} className="contract-row">
              <Link to={c.status === 'draft' && !c.has_pdf ? editLink(base, c) : `${base}?id=${c.id}`} className="grow" style={{ color: 'inherit', textDecoration: 'none' }}>
                <b>{c.address || c.cadastral || c.title || `Договор #${c.id}`}</b>
                <small className="muted">
                  #{c.id} · {kindLang(c)}{c.cadastral ? ` · ${c.cadastral}` : ''}{c.client_name ? ` · ${c.client_name}` : ''} · {c.created_by?.name || '—'}, {fmtDate(c.updated_at || c.created_at)}
                </small>
                {c.status === 'closed_no_deal' && (
                  <small className="muted">{c.close_reason_label}{c.close_comment ? `: ${c.close_comment}` : ''} · {c.closed_by?.name || '—'}, {fmtDate(c.closed_at)}</small>
                )}
              </Link>
              <span className="col gap-sm" style={{ alignItems: 'flex-end' }}>
                <ContractStatusBadge c={c} />
                <span className="row gap-sm" style={{ fontSize: 12 }}>
                  {c.deal_id ? <Link to={`${root}/deals/${c.deal_id}`}>Сделка #{c.deal_id}</Link>
                    : c.lead_id ? <Link to={`${root}/leads/${c.lead_id}`}>Заявка #{c.lead_id}</Link> : null}
                  {c.status === 'draft' && c.has_pdf && <Link to={editLink(base, c)} title="Изменить данные и перегенерировать"><Pencil size={13} /> Изменить</Link>}
                </span>
              </span>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
