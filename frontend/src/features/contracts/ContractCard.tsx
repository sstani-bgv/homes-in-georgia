// Stored contracts (backend services/contracts.py): the list, one contract with its PDF and lifecycle actions.
// PDF: open in the browser's viewer (Telegram: the expiring share link in the external browser), download.
// No «Поделиться» (Irakli 11-13): the parties get the contract via the e-sign links or «Отправить клиенту в Telegram».
import { useConfirm } from '../../components/ActionDialog';
import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, Copy, Download, ExternalLink, FileText, Handshake, Pencil, PenLine, Send, Share2, Unlock, Upload, XCircle } from 'lucide-react';
import { api } from '../../api';
import { itemsOf } from '../../api/client';
import type { Contract, ContractCloseReason, ContractStatus } from '../../api/types';
import { insideTelegram, tgWebApp } from '../../auth/AuthContext';
import { errText, useToast } from '../../components/toast';
import { Badge, Button, Card, Empty, ErrorBox, Field, Input, Loading, Modal, Select, Tabs, Textarea } from '../../components/ui';
import { CONTRACT_STAGES, fmtDate, fmtDateTime } from '../../lib/format';
import { useAuth } from '../../auth/AuthContext';
import { t } from '../../lib/i18n';

export const CLOSE_REASONS: Record<ContractCloseReason, string> = {
  deal_failed: t('Сделка сорвалась'), client_changed_mind: t('Клиент передумал'), owner_refused: t('Собственник отказался'), other: t('Другое'),
};
const TONE: Record<ContractStatus, 'blue' | 'ok' | 'solid' | 'grey'> = { draft: 'blue', signed: 'ok', deal: 'solid', closed_no_deal: 'grey' };
const KIND: Record<NonNullable<Contract['kind']>, string> = { rent: t('Аренда'), comm: t('Коммерция'), sale: t('Продажа') };
const kindLang = (c: Contract) => [c.kind && KIND[c.kind], c.lang].filter(Boolean).join(' ') || t('тип не выбран');

/** «Черновик» (being filled, no PDF) / «Сгенерирован» / «Договор подписан» / «Сделка» / «Сделка не состоялась» */
export function ContractStatusBadge({ c }: { c: { status: ContractStatus; status_label: string; has_pdf?: boolean } }) {
  return <Badge tone={c.status === 'draft' && c.has_pdf === false ? 'warn' : TONE[c.status]}>{t(c.status_label)}</Badge>;
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
  const { isStaff } = useAuth();
  const [signOpen, setSignOpen] = useState(false);
  const [closeOpen, setCloseOpen] = useState(false);
  const done = (x: Contract, msg: string) => { onChange(x); qc.invalidateQueries({ queryKey: ['contracts'] }); toast(msg); };
  const shareLink = async () => {
    const x = c.share && new Date(c.share.expires_at).getTime() - Date.now() > 86_400_000 ? c : await api.contracts.share(c.id);
    if (x !== c) onChange(x);
    return absolute(x.share!.path);
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
    if (tg) { await api.contracts.sendPdf(c.id); toast(t('PDF отправлен вам в Telegram')); return; }
    saveBlob(await api.contracts.pdf(c.id, c.has_signed_pdf), c.filename);
  });

  return (
    <div className="col gap-sm">
      <div className="row row-wrap gap-sm">
        <Button onClick={open} loading={busy.isPending}><ExternalLink size={16} />{t('Открыть PDF')}</Button>
        <Button variant="secondary" onClick={download}>{tg ? <><Send size={16} />{t('PDF в Telegram')}</> : <><Download size={16} />{t('Скачать PDF')}</>}</Button>
        {c.status === 'draft' && !c.pdf_stale && <Button variant="ok" onClick={() => setSignOpen(true)}><CheckCircle2 size={16} />{t('Договор подписан')}</Button>}
        {(c.status === 'signed' || c.status === 'deal') && !c.has_signed_pdf && <Button variant="ghost" onClick={() => setSignOpen(true)}><Upload size={16} />{t('Прикрепить скан')}</Button>}
        {(c.status === 'draft' || c.status === 'signed') && <Button variant="ghost" onClick={() => setCloseOpen(true)}><XCircle size={16} />{t('Сделка не состоялась')}</Button>}
        {isStaff && c.status === 'signed' && c.deal_due_at && !c.lead_deal_id && new Date(c.deal_due_at).getTime() < Date.now()
          && <Button variant="ghost" loading={busy.isPending} onClick={() => busy.mutate(async () => done(await api.contracts.liftDealDue(c.id), t('Блокировка агента снята')))}>
            <Unlock size={16} />{t('Снять блокировку агента')}</Button>}
      </div>
      {c.status === 'signed' && c.deal_due_at && !c.lead_deal_id && <div className="muted" style={{ fontSize: 13 }}>
        {new Date(c.deal_due_at).getTime() < Date.now() ? '⛔ ' + t('Срок оформления сделки истёк: CRM агента закрыта до сделки') : '⏳ ' + t('Сделку нужно оформить до {at}', { at: fmtDateTime(c.deal_due_at) })}</div>}
      {!closed && <SendToClient c={c} onChange={onChange} />}
      <ESignBlock c={c} onChange={(x) => { onChange(x); qc.invalidateQueries({ queryKey: ['contracts'] }); }} />
      <SignModal c={c} open={signOpen} onClose={() => setSignOpen(false)} onDone={(x) => { setSignOpen(false); done(x, t('Отмечено: договор подписан')); }} />
      <CloseModal c={c} open={closeOpen} onClose={() => setCloseOpen(false)} onDone={(x) => { setCloseOpen(false); done(x, t('Договор закрыт: сделка не состоялась')); }} />
    </div>
  );
}

/** «Отправить клиенту в Telegram» (11-13): the PDF + the registry extract from the operator's account (userbot), only on
 *  this click; to the lead's client, or to the @username / phone the agent types. A re-send asks first. */
function SendToClient({ c, onChange }: { c: Contract; onChange: (c: Contract) => void }) {
  const confirm = useConfirm();
  const toast = useToast();
  const [edit, setEdit] = useState(false);
  const [to, setTo] = useState('');
  const manual = edit || !c.client_tg;
  const sent = c.sent_to_client;
  const m = useMutation({
    mutationFn: () => api.contracts.sendClient(c.id, manual ? to.trim() : undefined),
    onSuccess: (x) => { onChange(x); setEdit(false); toast(t('Договор отправлен клиенту: {to}', { to: x.sent_to_client?.to ?? '' })); },
    onError: (e) => toast(errText(e), true),
  });
  const go = async () => {
    if (manual && !to.trim()) { toast(t('Укажите @username в Telegram или телефон клиента'), true); return; }
    if (sent && !await confirm(t('Договор уже отправлен клиенту {at} ({to}). Отправить ещё раз?', { at: fmtDateTime(sent.at), to: sent.to }))) return;
    m.mutate();
  };
  return (
    <div className="col gap-sm">
      <div className="row row-wrap gap-sm" style={{ alignItems: 'center' }}>
        <Button variant="secondary" loading={m.isPending} disabled={c.pdf_stale} onClick={go}
          title={c.pdf_stale ? t('Сначала обновите PDF') : undefined}><Send size={16} />{t('Отправить клиенту в Telegram')}</Button>
        {manual
          ? <Input style={{ maxWidth: 240 }} value={to} placeholder={t('@username или +995…')} aria-label={t('Получатель в Telegram')}
              onChange={(e) => setTo(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') go(); }} />
          : <span className="muted" style={{ fontSize: 13 }}>{t('кому: {to}', { to: c.client_tg!.label })}</span>}
        {c.client_tg && <button type="button" className="btn btn-sm btn-ghost" onClick={() => setEdit(!edit)}>{edit ? t('Клиенту из заявки') : t('Другой получатель')}</button>}
      </div>
      <div className="muted" style={{ fontSize: 12 }}>
        {sent ? <Badge tone="ok">{t('Отправлено клиенту')} {fmtDateTime(sent.at)} · {sent.to} · {typeof sent.version === 'number' ? t('версия {n}', { n: sent.version }) : sent.version}{sent.extract ? ` + ${t('выписка')}` : ''}</Badge>
          : manual && !c.client_tg ? `${c.lead_id || c.deal_id ? t('У клиента из заявки нет Telegram') : t('Договор без заявки')}: ${t('укажите @username или телефон получателя.')} ` : null}
        {' '}{c.data?.extract_file ? t('PDF и выписка из реестра уйдут с аккаунта оператора.') : t('PDF уйдёт с аккаунта оператора.')}
      </div>
    </div>
  );
}

/** Electronic signing (backend services/esign.py): a personal link for the owner and one for the tenant; when both
 *  have signed, the signed PDF replaces the draft and the contract becomes «Договор подписан» by itself. */
function ESignBlock({ c, onChange }: { c: Contract; onChange: (c: Contract) => void }) {
  const confirm = useConfirm();
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
          <PenLine size={16} />{t('Подписать электронно')}
        </Button>
        <span className="muted" style={{ fontSize: 12 }}>{t('Собственник и арендатор получат по ссылке и распишутся с телефона')}</span>
      </div>
    );
  }
  // the message goes in the signer's language, like the page it opens (11-13): the owner Georgian, the tenant English
  const where = c.address || c.cadastral || `#${c.id}`;
  // with a saved extract the message says it is on the same page (Irakli 11-13: it goes with the link)
  const ex = !!c.data?.extract_file;
  const text = (role: 'owner' | 'tenant') => role === 'owner'
    ? `ხელშეკრულება: ${where}. ${ex ? 'ხელშეკრულება და ამონაწერი საჯარო რეესტრიდან — ბმულზე. ' : ''}გთხოვთ, გახსნათ ბმული და ელექტრონულად მოაწეროთ ხელი (გამქირავებელი).`
    : `Contract: ${where}. ${ex ? 'The contract and the public registry extract are at the link. ' : ''}Please open the link and sign it electronically (tenant).`;
  const copy = (url: string, role: 'owner' | 'tenant') =>
    navigator.clipboard?.writeText(`${text(role)}\n${url}`).then(() => toast(t('Сообщение со ссылкой скопировано')), () => undefined);
  const send = async (url: string, role: 'owner' | 'tenant') => {
    if (tg) { tgWebApp()?.openTelegramLink?.(`https://t.me/share/url?url=${encodeURIComponent(url)}&text=${encodeURIComponent(text(role))}`); return; }
    if (navigator.share && mobile()) {
      try { await navigator.share({ url, text: text(role) }); } catch { /* closed */ }
      return;
    }
    await copy(url, role);
  };
  return (
    <div className="col gap-sm" style={{ border: '1px solid var(--line, #dfe4ee)', borderRadius: 12, padding: 12 }}>
      <b><PenLine size={16} style={{ verticalAlign: -3, marginRight: 6 }} />{t('Электронная подпись')}{es.complete ? `: ${t('подписан обеими сторонами')}` : ''}</b>
      {es.signers.map((s) => {
        const url = s.path ? absolute(s.path) : '';
        return (
          <div key={s.role} className="col gap-sm">
            <div className="row row-wrap gap-sm" style={{ alignItems: 'center' }}>
              <span>{s.label} —</span>
              {s.signed_at
                ? <Badge tone="ok">{t('подписал(а)')} · {s.full_name} · {fmtDateTime(s.signed_at)}</Badge>
                : <Badge tone="grey">{t('ожидает подписи')}</Badge>}
            </div>
            {url && !s.signed_at && (
              <div className="row row-wrap gap-sm">
                <Button size="sm" variant="secondary" onClick={() => send(url, s.role)}>{tg ? <><Send size={14} />{t('Отправить')}</> : <><Share2 size={14} />{t('Отправить ссылку')}</>}</Button>
                {!tg && <Button size="sm" variant="ghost" onClick={() => copy(url, s.role)}><Copy size={14} />{t('Копировать')}</Button>}
                {!tg && <a className="btn btn-sm btn-ghost" href={`https://t.me/share/url?url=${encodeURIComponent(url)}&text=${encodeURIComponent(text(s.role))}`} target="_blank" rel="noreferrer"><Send size={14} />Telegram</a>}
              </div>
            )}
          </div>
        );
      })}
      {c.status === 'draft' && (
        <>
          <p className="muted" style={{ fontSize: 12, margin: 0 }}>{t('Ссылки личные: каждая сторона видит договор, вводит ФИО, личный номер, телефон и расписывается. Собственник получает сообщение и страницу на грузинском, арендатор — на английском (язык на странице можно переключить). Правка текста отменяет неподписанные ссылки.')}</p>
          <Button size="sm" variant="ghost" style={{ alignSelf: 'flex-start' }} loading={m.isPending} onClick={async () => {
            if (await confirm(es.signers.some((s) => s.signed_at) ? t('Отменить подписание? Уже данные подписи будут удалены.') : t('Отменить ссылки на подпись?'))) m.mutate(() => api.contracts.esignCancel(c.id));
          }}><XCircle size={14} />{t('Отменить подписание')}</Button>
        </>
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
    <Modal open={open} onClose={onClose} title={attach ? t('Скан подписанного договора') : t('Договор подписан')}>
      <div className="col gap">
        <div className="muted" style={{ fontSize: 13 }}>
          {attach ? t('Загрузите PDF подписанного договора.') : t('Прикрепите скан подписанного договора (PDF) или просто подтвердите подпись — скан можно добавить позже.')}
          {' '}{t('PDF уйдёт в папку договоров на Google Drive.')}
        </div>
        <input ref={ref} type="file" accept="application/pdf,.pdf" hidden onChange={(e) => setFile(e.target.files?.[0] || null)} />
        <Button variant="secondary" onClick={() => ref.current?.click()}><Upload size={16} />{file ? file.name : t('Выбрать PDF')}</Button>
        <Button variant="ok" disabled={attach && !file} loading={m.isPending} onClick={() => m.mutate()}>
          <CheckCircle2 size={16} />{attach ? t('Загрузить') : file ? t('Подписан, загрузить скан') : t('Подтвердить без скана')}
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
    <Modal open={open} onClose={onClose} title={t('Сделка не состоялась')}>
      <div className="col gap">
        <div className="muted" style={{ fontSize: 13 }}>{t('Договор уйдёт из активных и останется в истории со статусом «Сделка не состоялась».')}</div>
        <div className="col gap-sm" role="radiogroup" aria-label={t('Причина')}>
          {(Object.keys(CLOSE_REASONS) as ContractCloseReason[]).map((r) => (
            <label key={r} className={`choice ${reason === r ? 'on' : ''}`} style={{ padding: '10px 12px' }}>
              <input type="radio" name="close-reason" checked={reason === r} onChange={() => setReason(r)} /> <b>{CLOSE_REASONS[r]}</b>
            </label>
          ))}
        </div>
        <Field label={`${t('Комментарий')}${reason === 'other' ? ' *' : ''}`}>
          <Textarea rows={3} value={comment} onChange={(e) => setComment(e.target.value)} placeholder={t('Что произошло')} />
        </Field>
        <Button variant="danger" disabled={!ok} loading={m.isPending} onClick={() => m.mutate()}><XCircle size={16} />{t('Закрыть договор')}</Button>
      </div>
    </Modal>
  );
}

/** «Оформить сделку» on a signed contract (Irakli 11-18): the deal form on its lead; a contract from the object card
 *  has none, so the backend finds the client's lead on the object or makes the client + lead from the contract. */
function DealButton({ c, root, onChange }: { c: Contract; root: string; onChange: (c: Contract) => void }) {
  const toast = useToast();
  const navigate = useNavigate();
  const m = useMutation({
    mutationFn: () => api.contracts.dealLead(c.id),
    onSuccess: (x) => { onChange(x); if (!x.lead_deal_id && x.lead_id) navigate(`${root}/deals/new?lead_id=${x.lead_id}`); },
    onError: (e) => toast(errText(e), true),
  });
  return (
    <div className="row row-wrap gap-sm mt" style={{ alignItems: 'center' }}>
      <Button variant="ok" size="lg" loading={m.isPending} onClick={() => m.mutate()}><Handshake size={18} />{t('Оформить сделку')}</Button>
      <span className="muted" style={{ fontSize: 13 }}>{c.lead_id ? t('Договор подписан: оформите сделку по заявке') : t('Договор подписан: оформите сделку — клиент и условия возьмутся из договора')}</span>
    </div>
  );
}

/** «Привязать к заявке»: a contract made without a lead / deal joins one later (11-13); quiet: nothing when no lead fits */
function LinkLead({ c, onChange, quiet }: { c: Contract; onChange: (c: Contract) => void; quiet?: boolean }) {
  const toast = useToast();
  const qc = useQueryClient();
  const leads = useQuery({ queryKey: ['leads', 'list', 'contract'], queryFn: () => api.leads.list({ status: CONTRACT_STAGES.join(',') }) });
  const m = useMutation({
    mutationFn: (lead_id: number) => api.contracts.link(c.id, { lead_id }),
    onSuccess: (x) => { onChange(x); qc.invalidateQueries({ queryKey: ['contracts'] }); toast(t('Договор привязан к заявке #{id}', { id: x.lead_id ?? '' })); },
    onError: (e) => toast(errText(e), true),
  });
  const items = itemsOf(leads.data);
  if (!items.length && quiet) return null;
  if (!items.length) return <div className="muted mt" style={{ fontSize: 13 }}>{leads.isLoading ? t('Без заявки') : t('Без заявки: нет заявок после просмотра, чтобы привязать')}.</div>;
  return (
    <div className="mt" style={{ maxWidth: 520 }}>
      <Field label={t('Привязать к заявке')}>
        <Select aria-label={t('Привязать к заявке')} value="" disabled={m.isPending} placeholder={t('Договор без заявки — выберите заявку')}
          onChange={(e) => e.target.value && m.mutate(Number(e.target.value))}
          options={items.map((l) => ({ value: l.id, label: `#${l.id} · ${l.listing?.address || l.listing?.title || t('объект')} · ${l.client?.name || t('клиент')}${l.agent?.name ? ` · ${l.agent.name}` : ''}` }))} />
      </Field>
    </div>
  );
}

/** «Привязать объект» (David 11-26): a contract without a lead gets its object by number, #15102 */
function LinkObject({ c, onChange }: { c: Contract; onChange: (c: Contract) => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [id, setId] = useState('');
  const m = useMutation({
    mutationFn: () => api.contracts.link(c.id, { property_id: Number(id.replace(/\D/g, '')) }),
    onSuccess: (x) => { onChange(x); qc.invalidateQueries({ queryKey: ['contracts'] }); toast(t('Договор привязан к объекту #{id}', { id: x.property_id ?? '' })); },
    onError: (e) => toast(errText(e), true),
  });
  return (
    <div className="row gap-sm mt" style={{ maxWidth: 520, alignItems: 'flex-end' }}>
      <Field label={t('Привязать объект')}><Input inputMode="numeric" value={id} placeholder={t('Номер объекта, например 15102')} onChange={(e) => setId(e.target.value)} /></Field>
      <Button variant="secondary" disabled={!/\d/.test(id)} loading={m.isPending} onClick={() => m.mutate()}>{t('Привязать')}</Button>
    </div>
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
  const root = base.replace(/\/contracts$/, '');
  const deal = c.deal_id || c.lead_deal_id;  // a pending deal is the lead's until its confirm
  const items: [string, React.ReactNode][] = [
    [t('Статус'), <ContractStatusBadge c={c} />],
    [t('Тип'), kindLang(c)],
    [t('Кадастровый код'), c.cadastral || '—'],
    [t('Адрес'), c.address || '—'],
    [t('Собственник'), c.owner_name || '—'],
    [t('Клиент'), c.client_name || '—'],
    [t('Создал'), `${c.created_by?.name || '—'} · ${fmtDateTime(c.created_at)}`],
  ];
  if (c.generated_at) items.push([t('Сгенерирован'), `${fmtDateTime(c.generated_at)}${c.versions?.length ? ` · ${t('версия {n}', { n: c.versions.length + 1 })}` : ''}`]);
  const openVersion = async (n: number) => {
    const w = window.open('', '_blank');
    const url = URL.createObjectURL(await api.contracts.pdf(c.id, false, n));
    if (w) w.location.href = url; else window.location.href = url;
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  };
  const extractUrl = String(c.data?.extract_url || '');
  if (extractUrl || c.data?.extract_file) items.push([t('Выписка из реестра'), (
    <span className="row row-wrap gap-sm">
      {/^https:\/\//.test(extractUrl) && <a href={extractUrl} target="_blank" rel="noopener noreferrer">{t('ссылка NAPR')}</a>}
      {c.data?.extract_file && <a href="#" onClick={async (e) => {
        e.preventDefault();
        const w = window.open('', '_blank');
        const url = URL.createObjectURL(await api.contracts.extract(c.id));
        if (w) w.location.href = url; else window.location.href = url;
        setTimeout(() => URL.revokeObjectURL(url), 60_000);
      }}>{t('сохранённый PDF')}</a>}
    </span>
  )]);
  if (c.signed_at) items.push([t('Подписан'), `${fmtDateTime(c.signed_at)}${c.signed_by ? ` · ${c.signed_by.name}` : ''}${c.has_signed_pdf ? ` · ${t('скан приложен')}` : ''}${c.in_drive ? ` · ${t('в Google Drive')}` : ''}`]);
  if (c.status === 'closed_no_deal') items.push([t('Причина'), `${c.close_reason_label}${c.close_comment ? `: ${c.close_comment}` : ''}`],
    [t('Закрыл'), `${c.closed_by?.name || '—'} · ${fmtDateTime(c.closed_at)}`]);
  return (
    <div className="col gap-lg">
      <Card label={t('Договор #{id}', { id: c.id })} title={c.address || c.cadastral || c.title || t('Договор')}
        actions={<Link className="btn btn-sm btn-ghost" to={base}>{t('Все договоры')}</Link>}>
        <dl className="kv contract-kv">{items.map(([k, v]) => <div key={k} style={{ display: 'contents' }}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
        <div className="row row-wrap gap-sm mt" style={{ fontSize: 13 }}>
          {c.lead_id && <Link to={`${root}/leads/${c.lead_id}`}>{t('Заявка #{id}', { id: c.lead_id })}</Link>}
          {deal && <Link to={`${root}/deals/${deal}`}>{t('Сделка #{id}', { id: deal })}</Link>}
          {c.property_id && <Link to={`${root === '/m' ? '/m/properties' : '/objects'}/${c.property_id}`}>{t('Объект #{id}', { id: c.property_id })}</Link>}
        </div>
        {c.status === 'signed' && !deal && <DealButton c={c} root={root} onChange={set} />}
        {!c.lead_id && !c.deal_id && c.status !== 'closed_no_deal' && <LinkLead c={c} onChange={set} quiet={c.status === 'signed'} />}
        {!c.lead_id && !c.deal_id && !c.property_id && c.status !== 'closed_no_deal' && <LinkObject c={c} onChange={set} />}
        {c.status === 'draft' && (
          <div className="row row-wrap gap-sm mt">
            <Link className={`btn ${c.has_pdf ? 'btn-secondary' : ''}`} to={editLink(base, c)}><Pencil size={16} />{c.has_pdf ? t('Изменить данные и перегенерировать') : t('Продолжить заполнение')}</Link>
            {c.pdf_stale && <span className="alert alert-warn" style={{ margin: 0 }}>{t('Данные изменены после генерации: откройте форму и нажмите «Обновить PDF»')}</span>}
          </div>
        )}
        {c.has_pdf ? <div className="mt"><ContractActions c={c} onChange={set} /></div>
          : <div className="muted mt" style={{ fontSize: 13 }}>{t('Черновик: форма сохраняется автоматически, PDF появится после «Сохранить PDF» на шаге «Документ».')}</div>}
        {!!c.versions?.length && (
          <div className="col gap-sm mt" style={{ fontSize: 13 }}>
            <span className="label">{t('Прошлые версии PDF')}</span>
            {c.versions.slice().reverse().map((v) => (
              <div key={v.n} className="row gap-sm">
                <span className="muted">{t('Версия {n}', { n: v.n + 1 })} · {fmtDateTime(v.at)}{v.by ? ` · ${v.by.name}` : ''}</span>
                <Button size="sm" variant="ghost" onClick={() => openVersion(v.n)}><ExternalLink size={14} />{t('Открыть')}</Button>
              </div>
            ))}
          </div>
        )}
      </Card>
      {pdf && <iframe title={t('PDF договора')} src={pdf} className="contract-pdf-frame" />}
    </div>
  );
}

const TABS = [
  { value: 'active', label: t('Активные') }, { value: 'deal', label: t('Сделки') },
  { value: 'closed_no_deal', label: t('Сделка не состоялась') }, { value: 'all', label: t('Все') },
] as const;

/** /contracts and the cabinet: the agent's (staff: all, or agentId's) contracts by deal; closed ones only under
 *  «Сделка не состоялась» / «Все». A draft opens in the form (continue / edit and regenerate), the rest on its page. */
export function ContractList({ base, agentId, label = t('История'), title = t('Договоры') }: { base: string; agentId?: number; label?: string; title?: string }) {
  const [status, setStatus] = useState<(typeof TABS)[number]['value']>('active');
  const [text, setText] = useState('');
  const q = useQuery({ queryKey: ['contracts', 'list', status, text, agentId], queryFn: () => api.contracts.list({ status, q: text.trim() || undefined, agent_id: agentId }) });
  const rows = itemsOf(q.data);
  const root = base.replace(/\/contracts$/, '');
  return (
    <Card label={label} title={title}>
      <Tabs value={status} onChange={setStatus} tabs={TABS.map((t) => ({ ...t }))} />
      <Input className="input mt" placeholder={t('Кадастровый код, адрес, клиент, собственник')} value={text} onChange={(e) => setText(e.target.value)} />
      {q.isLoading ? <Loading /> : q.error ? <ErrorBox error={q.error} /> : rows.length === 0 ? <Empty text={t('Договоров нет')} icon={<FileText size={22} />} /> : (
        <div className="col gap-sm mt">
          {rows.map((c) => (
            <div key={c.id} className="contract-row">
              <Link to={c.status === 'draft' && !c.has_pdf ? editLink(base, c) : `${base}?id=${c.id}`} className="grow" style={{ color: 'inherit', textDecoration: 'none' }}>
                <b>{c.address || c.cadastral || c.title || t('Договор #{id}', { id: c.id })}</b>
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
                  {c.deal_id || c.lead_deal_id ? <Link to={`${root}/deals/${c.deal_id || c.lead_deal_id}`}>{t('Сделка #{id}', { id: (c.deal_id || c.lead_deal_id)! })}</Link>
                    : c.lead_id ? <Link to={`${root}/leads/${c.lead_id}`}>{t('Заявка #{id}', { id: c.lead_id })}</Link> : null}
                  {c.status === 'draft' && c.has_pdf && <Link to={editLink(base, c)} title={t('Изменить данные и перегенерировать')}><Pencil size={13} /> {t('Изменить')}</Link>}
                </span>
              </span>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
