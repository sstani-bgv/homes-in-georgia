// /sign/<token>: the owner confirms the exclusive cooperation and signs with a finger (no CRM login; ported from
// PlatformDGE/hig-sign). The PDF is built here and attached to the object (backend properties.sign_contract).
import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { http } from '../api/client';
import { errText } from '../components/toast';
import { Button, ErrorBox, Field, Input, Loading, Seg } from '../components/ui';
import { LANG_LABEL, buildExclusivePdf, confirmText, tbilisiNow, type Lang } from '../lib/exclusivePdf';
import { fmtDateTime, phoneInput } from '../lib/format';
import { cadastralError, cadastralNorm } from '../lib/cadastral';
import { CheckCircle2, Copy, ExternalLink, Upload } from 'lucide-react';

interface SignView { id: number; address: string; cadastral_code: string; owner_name: string; owner_phone: string; signed_at: string | null }

// the registry extract: NAPR's public search (my.gov.ge service 176 embeds it); its document view is behind reCAPTCHA,
// so the owner downloads the extract himself and uploads it here
const REGISTRY_URL = 'https://naprweb.reestri.gov.ge/_dea/#/search';
const MAX_DOC = 15 * 1024 * 1024;
type Docs = { extract: string; extractHow: string; openRegistry: string; copyCode: string; copied: string; passport: string; passportHint: string; pick: string; replace: string; tooBig: string };
const DOCS: Record<Lang, Docs> = {
  ka: { extract: 'საჯარო რეესტრის ამონაწერი (PDF)', extractHow: 'გახსენით რეესტრი, ჩაწერეთ საკადასტრო კოდი, გახსენით ბოლო განცხადება და ჩამოტვირთეთ ამონაწერი (PDF), შემდეგ ატვირთეთ აქ.', openRegistry: 'რეესტრის გახსნა', copyCode: 'კოდის კოპირება', copied: 'დაკოპირდა', passport: 'პასპორტი / პირადობის მოწმობა', passportHint: 'ფოტო ან PDF (JPG, PNG, HEIC, PDF), 15 მბ-მდე. ინახება დახურულად, ხედავს მხოლოდ სააგენტო.', pick: 'ფაილის არჩევა', replace: 'შეცვლა', tooBig: 'ფაილი 15 მბ-ზე დიდია' },
  ru: { extract: 'Выписка из Публичного реестра (PDF)', extractHow: 'Откройте реестр, введите кадастровый код, откройте последнюю заявку и скачайте выписку (ამონაწერი, PDF), затем загрузите её сюда.', openRegistry: 'Открыть реестр', copyCode: 'Скопировать код', copied: 'Скопировано', passport: 'Паспорт / ID-карта', passportHint: 'Фото или PDF (JPG, PNG, HEIC, PDF), до 15 МБ. Хранится закрыто, видит только агентство.', pick: 'Выбрать файл', replace: 'Заменить', tooBig: 'Файл больше 15 МБ' },
  en: { extract: 'Public Registry extract (PDF)', extractHow: 'Open the registry, enter the cadastral code, open the latest application and download the extract (ამონაწერი, PDF), then upload it here.', openRegistry: 'Open the registry', copyCode: 'Copy code', copied: 'Copied', passport: 'Passport / ID card', passportHint: 'Photo or PDF (JPG, PNG, HEIC, PDF), up to 15 MB. Stored privately, only the agency sees it.', pick: 'Choose file', replace: 'Replace', tooBig: 'The file is larger than 15 MB' },
};

function FilePick({ label, hint, accept, file, onFile, t }: { label: string; hint?: React.ReactNode; accept: string; file: File | null; onFile: (f: File | null) => void; t: Docs }) {
  const ref = useRef<HTMLInputElement>(null);
  const [err, setErr] = useState('');
  return (
    <div className="col gap-sm">
      <span className="label">{label}</span>
      {hint && <span className="faint" style={{ fontSize: 12 }}>{hint}</span>}
      <input ref={ref} type="file" accept={accept} hidden onChange={(e) => {
        const fl = e.target.files?.[0] || null; e.target.value = '';
        if (fl && fl.size > MAX_DOC) { setErr(t.tooBig); onFile(null); } else { setErr(''); onFile(fl); }
      }} />
      <div className="row row-wrap gap-sm">
        <Button variant={file ? 'secondary' : 'primary'} onClick={() => ref.current?.click()}><Upload size={16} />{file ? t.replace : t.pick}</Button>
        {file && <span className="row gap-sm" style={{ minWidth: 0, overflowWrap: 'anywhere' }}><CheckCircle2 size={16} color="var(--ok, #1a7f4b)" />{file.name}</span>}
      </div>
      {err && <span style={{ color: 'var(--accent)', fontSize: 12 }}>{err}</span>}
    </div>
  );
}

const UI: Record<Lang, { name: string; pid: string; phone: string; cad: string; cadBad: string; sign: string; clear: string; agree: string; send: string; done: string }> = {
  ru: { name: 'ФИО', pid: 'Личный номер', phone: 'Телефон', cad: 'Кадастровый код', cadBad: 'Проверьте код: цифры через точку, например 01.14.12.003.045', sign: 'Подпишите пальцем', clear: 'Стереть', agree: 'С условиями согласен(на)', send: 'Подписать', done: 'Договор подписан. Спасибо!' },
  ka: { name: 'სახელი და გვარი', pid: 'პირადი ნომერი', phone: 'ტელეფონი', cad: 'საკადასტრო კოდი', cadBad: 'შეამოწმეთ კოდი: ციფრები წერტილით, მაგ. 01.14.12.003.045', sign: 'მოაწერეთ ხელი თითით', clear: 'წაშლა', agree: 'ვეთანხმები პირობებს', send: 'ხელმოწერა', done: 'ხელშეკრულება ხელმოწერილია. გმადლობთ!' },
  en: { name: 'Full name', pid: 'Personal ID number', phone: 'Phone', cad: 'Cadastral code', cadBad: 'Check the code: digits separated by dots, e.g. 01.14.12.003.045', sign: 'Sign with your finger', clear: 'Clear', agree: 'I agree to the terms', send: 'Sign', done: 'Signed. Thank you!' },
};

function SignaturePad({ onInk, padRef, clearLabel }: { onInk: (v: boolean) => void; padRef: React.RefObject<HTMLCanvasElement>; clearLabel: string }) {
  const last = useRef<[number, number] | null>(null);
  useEffect(() => {
    const c = padRef.current!;
    const r = c.getBoundingClientRect(), k = window.devicePixelRatio || 1;
    c.width = r.width * k; c.height = r.height * k;
    const ctx = c.getContext('2d')!;
    ctx.scale(k, k); ctx.lineWidth = 2.5; ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.strokeStyle = '#1a1f2b';
  }, [padRef]);
  const at = (e: React.PointerEvent): [number, number] => { const r = padRef.current!.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
  const move = (e: React.PointerEvent) => {
    if (!last.current) return;
    const ctx = padRef.current!.getContext('2d')!, p = at(e);
    ctx.beginPath(); ctx.moveTo(...last.current); ctx.lineTo(...p); ctx.stroke();
    last.current = p; onInk(true);
  };
  const clear = () => { const c = padRef.current!; c.getContext('2d')!.clearRect(0, 0, c.width, c.height); onInk(false); };
  return (
    <div className="col gap-sm">
      <canvas ref={padRef} style={{ width: '100%', height: 180, border: '1.5px dashed var(--line, #c9d1e0)', borderRadius: 12, touchAction: 'none', background: '#fff' }}
        onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); last.current = at(e); }}
        onPointerMove={move} onPointerUp={() => { last.current = null; }} onPointerCancel={() => { last.current = null; }} />
      <Button variant="secondary" size="sm" onClick={clear}>{clearLabel}</Button>
    </div>
  );
}

export function ExclusiveSignPage() {
  const { token = '' } = useParams();
  const q = useQuery({ queryKey: ['exclusive-sign', token], queryFn: () => http.get<SignView>(`/public/exclusive/${token}`), retry: false });
  const [lang, setLang] = useState<Lang>('ka');
  const [f, setF] = useState({ full_name: '', personal_id: '', phone: '', cadastral_code: '' });
  const [ink, setInk] = useState(false);
  const [agree, setAgree] = useState(false);
  const [idDoc, setIdDoc] = useState<File | null>(null);
  const [extract, setExtract] = useState<File | null>(null);
  const [copied, setCopied] = useState(false);
  const pad = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    if (q.data) setF((x) => ({ ...x, full_name: q.data.owner_name, phone: q.data.owner_phone, cadastral_code: q.data.cadastral_code }));
  }, [q.data]);
  const t = UI[lang], td = DOCS[lang];
  const sign = useMutation({
    mutationFn: async () => {
      const v = q.data!;
      const pdf = await buildExclusivePdf(v.id, { ...f, address: v.address, date: tbilisiNow() }, pad.current!, navigator.userAgent,
        [['პასპორტი / Паспорт (ID)', `загружен: ${idDoc!.name}`], ['ამონაწერი / Выписка из реестра', `загружена: ${extract!.name}`]]);
      const fd = new FormData();
      Object.entries(f).forEach(([k, val]) => fd.append(k, val.trim()));
      fd.append('consent', 'true');
      fd.append('file', new Blob([pdf], { type: 'application/pdf' }), `exclusive-${v.id}.pdf`);
      fd.append('id_doc', idDoc!);
      fd.append('extract', extract!);
      return http.post<SignView>(`/public/exclusive/${token}`, fd);
    },
    onSuccess: () => q.refetch(),
  });

  const wrap = (body: React.ReactNode) => <div style={{ maxWidth: 640, margin: '0 auto', padding: '24px 16px' }}><div className="card card-pad col gap-lg">{body}</div></div>;
  if (q.isLoading) return wrap(<Loading />);
  if (q.error || !q.data) return wrap(<ErrorBox error={q.error} />);
  if (q.data.signed_at) return wrap(<div className="alert alert-ok">{t.done} · {fmtDateTime(q.data.signed_at)}</div>);
  const [title, lines] = confirmText(lang, { ...f, address: q.data.address, date: tbilisiNow().split(',')[0] });
  const cadBad = !!cadastralError(f.cadastral_code, q.data?.cadastral_code);
  const ready = f.full_name.trim() && f.personal_id.trim() && f.phone.trim() && ink && agree && !cadBad && idDoc && extract;
  const copyCode = () => navigator.clipboard?.writeText(f.cadastral_code.trim()).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); }, () => undefined);
  return wrap(<>
    <div className="row row-between row-wrap gap-sm">
      <b style={{ color: '#1D3560' }}>Homes in Georgia · Rent in Tbilisi</b>
      <Seg value={lang} onChange={setLang} options={(Object.keys(LANG_LABEL) as Lang[]).map((l) => ({ value: l, label: LANG_LABEL[l] }))} />
    </div>
    <div className="form-grid">
      <Field label={t.name}><Input value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></Field>
      <Field label={t.pid}><Input inputMode="numeric" value={f.personal_id} onChange={(e) => setF({ ...f, personal_id: e.target.value })} /></Field>
      <Field label={t.phone}><Input type="tel" inputMode="tel" value={f.phone} onChange={(e) => setF({ ...f, phone: phoneInput(e) })} /></Field>
      <Field label={t.cad} hint={cadBad ? <span style={{ color: 'var(--accent)' }}>{t.cadBad}</span> : undefined}><Input value={f.cadastral_code} placeholder="01.14.12.003.045" onChange={(e) => setF({ ...f, cadastral_code: e.target.value })} onBlur={() => { if (!cadBad && f.cadastral_code !== q.data?.cadastral_code) setF({ ...f, cadastral_code: cadastralNorm(f.cadastral_code) }); }} /></Field>
    </div>
    <div className="col gap-sm">
      <FilePick label={td.extract} accept="application/pdf,.pdf" file={extract} onFile={setExtract} t={td} hint={<>
        {td.extractHow}
        <span className="row row-wrap gap-sm" style={{ marginTop: 8 }}>
          <a className="btn btn-secondary btn-sm" href={REGISTRY_URL} target="_blank" rel="noreferrer"><ExternalLink size={14} />{td.openRegistry}</a>
          {f.cadastral_code.trim() && !cadBad && <Button variant="secondary" size="sm" onClick={copyCode}><Copy size={14} />{copied ? td.copied : `${td.copyCode} ${f.cadastral_code.trim()}`}</Button>}
        </span>
      </>} />
    </div>
    <FilePick label={td.passport} hint={td.passportHint} accept="image/jpeg,image/png,image/heic,image/heif,.heic,.heif,application/pdf,.pdf" file={idDoc} onFile={setIdDoc} t={td} />
    <div className="col gap-sm">
      <h2 style={{ fontSize: 18 }}>{title}</h2>
      {lines.map((s, i) => <div key={i} style={s.startsWith('• ') ? { paddingLeft: 16 } : undefined}>{s}</div>)}
    </div>
    <div className="col gap-sm"><span className="label">{t.sign}</span><SignaturePad padRef={pad} onInk={setInk} clearLabel={t.clear} /></div>
    <label className="row gap-sm" style={{ cursor: 'pointer' }}><input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} />{t.agree}</label>
    {sign.error && <div className="alert alert-warn">{errText(sign.error)}</div>}
    <Button size="lg" variant="ok" disabled={!ready} loading={sign.isPending} onClick={() => sign.mutate()}>{t.send}</Button>
  </>);
}
