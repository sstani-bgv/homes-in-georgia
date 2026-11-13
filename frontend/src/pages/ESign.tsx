// /esign/<token>: one party (owner / tenant) of a contract reads it and signs electronically with a finger — no CRM
// login, the link is the access (backend services/esign.py; the HiG Sign idea). When both parties have signed, the
// server prints the signed PDF (signatures on every page + the signing record) and the page offers it for download.
import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { CheckCircle2, Download, FileText } from 'lucide-react';
import { http } from '../api/client';
import type { ESignView } from '../api/types';
import { errText } from '../components/toast';
import { Badge, Button, ErrorBox, Field, Input, Loading, Seg } from '../components/ui';
import { SignaturePad, signaturePng } from '../components/SignaturePad';
import { LANG_LABEL, type Lang } from '../lib/exclusivePdf';
import { fmtDateTime, phoneInput } from '../lib/format';

const UI: Record<Lang, {
  intro: string; read: string; name: string; pid: string; phone: string; sign: string; clear: string; agree: string;
  send: string; done: string; waiting: string; complete: string; download: string; parties: string; signed: string; pending: string;
  as: string; toSign: string;
}> = {
  ka: {
    intro: 'ხელშეკრულების ელექტრონული ხელმოწერა', read: 'გთხოვთ, ყურადღებით წაიკითხოთ ხელშეკრულება', name: 'სახელი და გვარი',
    pid: 'პირადი ნომერი', phone: 'ტელეფონი', sign: 'მოაწერეთ ხელი თითით', clear: 'წაშლა',
    agree: 'წავიკითხე ხელშეკრულება და ვეთანხმები მის პირობებს', send: 'ხელმოწერა', done: 'თქვენ ხელი მოაწერეთ',
    waiting: 'ველოდებით მეორე მხარის ხელმოწერას. ხელმოწერილ PDF-ს ამავე ბმულზე ნახავთ.',
    complete: 'ხელშეკრულება ხელმოწერილია ორივე მხარის მიერ.', download: 'PDF-ის ჩამოტვირთვა', parties: 'მხარეები',
    signed: 'ხელმოწერილია', pending: 'ელოდება', as: 'თქვენ აწერთ ხელს, როგორც', toSign: 'ხელმოწერაზე გადასვლა ↓',
  },
  ru: {
    intro: 'Электронное подписание договора', read: 'Пожалуйста, внимательно прочитайте договор', name: 'ФИО',
    pid: 'Личный номер', phone: 'Телефон', sign: 'Распишитесь пальцем', clear: 'Стереть',
    agree: 'Я прочитал(а) договор и согласен(на) с его условиями', send: 'Подписать', done: 'Вы подписали договор',
    waiting: 'Ждём подпись второй стороны. Подписанный PDF появится по этой же ссылке.',
    complete: 'Договор подписан обеими сторонами.', download: 'Скачать PDF', parties: 'Стороны',
    signed: 'подписал(а)', pending: 'ожидает', as: 'Вы подписываете как', toSign: 'К подписи ↓',
  },
  en: {
    intro: 'Electronic contract signing', read: 'Please read the contract carefully', name: 'Full name',
    pid: 'Personal ID number', phone: 'Phone', sign: 'Sign with your finger', clear: 'Clear',
    agree: 'I have read the contract and agree to its terms', send: 'Sign', done: 'You have signed the contract',
    waiting: 'Waiting for the other party to sign. The signed PDF will be available at this same link.',
    complete: 'The contract is signed by both parties.', download: 'Download PDF', parties: 'Parties',
    signed: 'signed', pending: 'pending', as: 'You are signing as', toSign: 'Go to signing ↓',
  },
};

const DEFAULT_LANG: Record<ESignView['lang'], Lang> = { GEO: 'ka', RU: 'ru', EN: 'en' };

/** The contract as stored (cleaned server-side), in a sandboxed frame — no scripts; sized once to its content.
 *  (Measured from the body, not the frame's own viewport: that grows with the frame and never settles.) */
function ContractFrame({ html }: { html: string }) {
  const ref = useRef<HTMLIFrameElement>(null);
  const [h, setH] = useState(600);
  const fit = () => {
    const d = ref.current?.contentDocument;
    if (!d?.body) return;
    const want = Math.ceil(d.body.getBoundingClientRect().height) + 24;  // + the body padding
    setH((x) => (Math.abs(x - want) > 4 ? want : x));
  };
  useEffect(() => {
    // fonts / images settle shortly after load; then the frame keeps its size (nothing moves under the finger)
    const ids = [300, 1000, 2500].map((t) => setTimeout(fit, t));
    return () => ids.forEach(clearTimeout);
  }, [html]);  // eslint-disable-line react-hooks/exhaustive-deps
  const doc = html.replace('</head>', '<style>html,body{height:auto!important}body{margin:0;padding:12px;background:#fff;color:#111;overflow-x:hidden}.cd-p{overflow-wrap:anywhere}</style></head>');
  return <iframe ref={ref} title="contract" srcDoc={doc} sandbox="allow-same-origin" onLoad={fit} scrolling="no"
    style={{ width: '100%', height: h, border: '1px solid var(--line, #dfe4ee)', borderRadius: 12, background: '#fff', display: 'block' }} />;
}

export function ESignPage() {
  const { token = '' } = useParams();
  const q = useQuery({ queryKey: ['esign', token], queryFn: () => http.get<ESignView>(`/public/esign/${token}`), retry: false, refetchOnWindowFocus: false });
  const [lang, setLang] = useState<Lang | null>(null);
  const [f, setF] = useState({ full_name: '', personal_id: '', phone: '' });
  const [ink, setInk] = useState(false);
  const [agree, setAgree] = useState(false);
  const pad = useRef<HTMLCanvasElement>(null);
  const v = q.data;
  useEffect(() => {
    if (v) setF((x) => ({ ...x, full_name: x.full_name || v.prefill.full_name, personal_id: x.personal_id || v.prefill.personal_id }));
  }, [v]);
  const l: Lang = lang || (v ? DEFAULT_LANG[v.lang] : 'ka');
  const t = UI[l];
  const sign = useMutation({
    mutationFn: () => http.post<ESignView>(`/public/esign/${token}`, {
      ...Object.fromEntries(Object.entries(f).map(([k, x]) => [k, x.trim()])), consent: true,
      signature: signaturePng(pad.current!), doc_sha256: v!.doc_sha256, lang: l,
    }),
    onSuccess: () => q.refetch(),
  });

  const wrap = (body: React.ReactNode) => <div style={{ maxWidth: 820, margin: '0 auto', padding: '24px 16px' }}><div className="card card-pad col gap-lg">{body}</div></div>;
  if (q.isLoading) return wrap(<Loading />);
  if (q.error || !v) return wrap(<ErrorBox error={q.error} />);

  const head = (
    <>
      <div className="row row-between row-wrap gap-sm">
        <b style={{ color: '#1D3560' }}>Homes in Georgia · Rent in Tbilisi</b>
        <Seg value={l} onChange={setLang} options={(Object.keys(LANG_LABEL) as Lang[]).map((x) => ({ value: x, label: LANG_LABEL[x] }))} />
      </div>
      <div className="col gap-sm">
        <h2 style={{ fontSize: 20 }}><FileText size={20} style={{ verticalAlign: -3, marginRight: 6 }} />{t.intro}</h2>
        <div className="muted">{[v.address, v.cadastral].filter(Boolean).join(' · ') || `#${v.contract_id}`}</div>
        <div className="row row-wrap gap-sm" style={{ fontSize: 13 }}>
          <span className="muted">{t.parties}:</span>
          {v.parties.map((p) => (
            <Badge key={p.role} tone={p.signed ? 'ok' : 'grey'}>{p.label[l]} · {p.signed ? t.signed : t.pending}</Badge>
          ))}
        </div>
      </div>
    </>
  );

  if (v.signed_at) {
    return wrap(<>
      {head}
      <div className="alert alert-ok"><CheckCircle2 size={16} style={{ verticalAlign: -3 }} /> {t.done}{v.full_name ? ` (${v.full_name})` : ''} · {fmtDateTime(v.signed_at)}</div>
      {v.complete
        ? <>
            <div className="alert alert-ok">{t.complete}</div>
            <a className="btn btn-ok btn-lg" href={`/api/public/esign/${token}/pdf?download=1`}><Download size={18} />{t.download}</a>
          </>
        : <div className="muted">{t.waiting}</div>}
    </>);
  }

  const ready = f.full_name.trim() && f.personal_id.trim() && f.phone.trim() && ink && agree;
  return wrap(<>
    {head}
    <div className="col gap-sm">
      <div className="row row-between row-wrap gap-sm">
        <span className="label">{t.read}</span>
        <Button size="sm" variant="secondary" onClick={() => document.getElementById('esign-form')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>{t.toSign}</Button>
      </div>
      {v.html && <ContractFrame html={v.html} />}
    </div>
    <div className="col gap-sm" id="esign-form">
      <span className="muted">{t.as} <b>«{v.role_label[l]}»</b></span>
      <div className="form-grid">
        <Field label={t.name}><Input value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} autoComplete="name" /></Field>
        <Field label={t.pid}><Input inputMode="numeric" value={f.personal_id} onChange={(e) => setF({ ...f, personal_id: e.target.value })} /></Field>
        <Field label={t.phone}><Input type="tel" inputMode="tel" value={f.phone} onChange={(e) => setF({ ...f, phone: phoneInput(e) })} autoComplete="tel" /></Field>
      </div>
    </div>
    <div className="col gap-sm"><span className="label">{t.sign}</span><SignaturePad padRef={pad} onInk={setInk} clearLabel={t.clear} /></div>
    <label className="row gap-sm" style={{ cursor: 'pointer' }}><input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} />{t.agree}</label>
    {sign.error && <div className="alert alert-warn">{errText(sign.error)}</div>}
    <Button size="lg" variant="ok" disabled={!ready} loading={sign.isPending} onClick={() => sign.mutate()}>{t.send}</Button>
  </>);
}
