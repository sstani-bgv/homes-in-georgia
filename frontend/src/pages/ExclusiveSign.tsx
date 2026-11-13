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
import { cadastralError, cadastralInput, cadastralNorm } from '../lib/cadastral';
import { CheckCircle2, Copy, ExternalLink, Upload } from 'lucide-react';
import { RegistryApps, day, useSettledId, type RegApp, type RegTexts } from '../components/RegistryApps';
import { SignaturePad } from '../components/SignaturePad';

interface SignView { id: number; address: string; cadastral_code: string; owner_name: string; owner_phone: string; signed_at: string | null }

// the registry extract: our backend lists NAPR's applications for the code (services/registry.py); the owner taps one,
// NAPR's own page opens with its reCAPTCHA, he downloads the extract (ამონაწერი) himself and uploads it here
const REGISTRY_URL = 'https://naprweb.reestri.gov.ge/_dea/#/search';
const MAX_DOC = 15 * 1024 * 1024;
type Docs = RegTexts & { fromExtract: string; upload: string; extract: string; extractHow: string; openRegistry: string; copyCode: string; copied: string; passport: string; passportHint: string; pick: string; replace: string; tooBig: string };
const DOCS: Record<Lang, Docs> = {
  ka: { fromExtract: 'საკადასტრო კოდი ამონაწერიდან', listTitle: 'თქვენი განცხადებები საჯარო რეესტრში', listHint: 'შეეხეთ ამ ობიექტის ბოლო დასრულებულ განცხადებას: გაიხსნება რეესტრის გვერდი — მონიშნეთ „მე არ ვარ რობოტი“, ჩამოტვირთეთ ამონაწერი (PDF) და ატვირთეთ აქ.', open: 'გახსნა', picked: 'არჩეულია', more: 'მეტის ჩვენება', none: 'ამ პირადი ნომრით განცხადებები ვერ მოიძებნა.', down: 'რეესტრი ახლა არ პასუხობს.', upload: 'ჩამოტვირთული ამონაწერის ატვირთვა', extract: 'საჯარო რეესტრის ამონაწერი (PDF)', extractHow: 'გახსენით რეესტრი, ჩაწერეთ პირადი ნომერი, გახსენით ამ ობიექტის ბოლო განცხადება და ჩამოტვირთეთ ამონაწერი (PDF), შემდეგ ატვირთეთ აქ.', openRegistry: 'რეესტრის გახსნა', copyCode: 'პირადი ნომრის კოპირება', copied: 'დაკოპირდა', passport: 'პასპორტი / პირადობის მოწმობა', passportHint: 'ფოტო ან PDF (JPG, PNG, HEIC, PDF), 15 მბ-მდე. ინახება დახურულად, ხედავს მხოლოდ სააგენტო.', pick: 'ფაილის არჩევა', replace: 'შეცვლა', tooBig: 'ფაილი 15 მბ-ზე დიდია' },
  ru: { fromExtract: 'Кадастровый код из выписки', listTitle: 'Ваши заявки в Публичном реестре', listHint: 'Нажмите на последнюю завершённую заявку по этому объекту: откроется страница реестра — отметьте «Я не робот», скачайте выписку (PDF) и загрузите её сюда.', open: 'Открыть', picked: 'Выбрано', more: 'Показать ещё', none: 'По этому личному номеру заявок не найдено.', down: 'Реестр сейчас не отвечает.', upload: 'Загрузите скачанную выписку', extract: 'Выписка из Публичного реестра (PDF)', extractHow: 'Откройте реестр, введите личный номер, откройте последнюю заявку по этому объекту и скачайте выписку (ამონაწერი, PDF), затем загрузите её сюда.', openRegistry: 'Открыть реестр', copyCode: 'Скопировать личный номер', copied: 'Скопировано', passport: 'Паспорт / ID-карта', passportHint: 'Фото или PDF (JPG, PNG, HEIC, PDF), до 15 МБ. Хранится закрыто, видит только агентство.', pick: 'Выбрать файл', replace: 'Заменить', tooBig: 'Файл больше 15 МБ' },
  en: { fromExtract: 'Cadastral code from the extract', listTitle: 'Your applications in the Public Registry', listHint: 'Tap the latest completed application for this property: the registry page opens — tick “I’m not a robot”, download the extract (PDF) and upload it here.', open: 'Open', picked: 'Selected', more: 'Show more', none: 'No applications found for this personal ID.', down: 'The registry is not responding right now.', upload: 'Upload the downloaded extract', extract: 'Public Registry extract (PDF)', extractHow: 'Open the registry, enter your personal ID, open the latest application for this property and download the extract (ამონაწერი, PDF), then upload it here.', openRegistry: 'Open the registry', copyCode: 'Copy personal ID', copied: 'Copied', passport: 'Passport / ID card', passportHint: 'Photo or PDF (JPG, PNG, HEIC, PDF), up to 15 MB. Stored privately, only the agency sees it.', pick: 'Choose file', replace: 'Replace', tooBig: 'The file is larger than 15 MB' },
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
  ka: { name: 'სახელი და გვარი', pid: 'პირადი ნომერი', phone: 'ტელეფონი', cad: 'საკადასტრო კოდი', cadBad: 'შეამოწმეთ კოდი: ციფრები წერტილით, მაგ. 01.14.12.003.045', sign: 'მოაწერეთ ხელი თითით', clear: 'წაშლა', agree: 'ვეთანხმები ზემოთ აღნიშნულ პირობებს', send: 'ხელმოწერა', done: 'ხელშეკრულება ხელმოწერილია. გმადლობთ!' },
  en: { name: 'Full name', pid: 'Personal ID number', phone: 'Phone', cad: 'Cadastral code', cadBad: 'Check the code: digits separated by dots, e.g. 01.14.12.003.045', sign: 'Sign with your finger', clear: 'Clear', agree: 'I agree to the above terms', send: 'Sign', done: 'Signed. Thank you!' },
};

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
  const [app, setApp] = useState<RegApp | null>(null);
  const [fromExtract, setFromExtract] = useState<string | null | undefined>(undefined);  // undefined: not read yet
  const lookup = useSettledId(f.personal_id);
  const pad = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    if (q.data) setF((x) => ({ ...x, full_name: q.data.owner_name, phone: q.data.owner_phone, cadastral_code: q.data.cadastral_code }));
  }, [q.data]);
  const t = UI[lang], td = DOCS[lang];
  // the owner doesn't type the cadastral code: it's read from the extract he picked (backend registry.extract_code)
  const pickExtract = (fl: File | null) => {
    setExtract(fl); setFromExtract(undefined);
    if (!fl) return;
    const fd = new FormData(); fd.append('file', fl);
    http.post<{ cadastral_code: string | null }>(`/public/exclusive/${token}/extract-code`, fd).then((r) => {
      setFromExtract(r.cadastral_code);
      if (r.cadastral_code) setF((x) => ({ ...x, cadastral_code: r.cadastral_code! }));
    }, () => setFromExtract(null));
  };
  const sign = useMutation({
    mutationFn: async () => {
      const v = q.data!;
      const pdf = await buildExclusivePdf(v.id, { ...f, address: v.address, date: tbilisiNow() }, pad.current!, navigator.userAgent,
        [['პასპორტი / Паспорт (ID)', `загружен: ${idDoc!.name}`], ['ამონაწერი / Выписка из реестра', `загружена: ${extract!.name}`],
          ...(app ? [['განცხადება / Заявка NAPR', `№${app.reg_number} · ${day(app.date)}`] as [string, string]] : [])]);
      const fd = new FormData();
      Object.entries(f).forEach(([k, val]) => fd.append(k, val.trim()));
      fd.append('consent', 'true');
      fd.append('file', new Blob([pdf], { type: 'application/pdf' }), `exclusive-${v.id}.pdf`);
      fd.append('id_doc', idDoc!);
      fd.append('extract', extract!);
      if (app) fd.append('registry_app', JSON.stringify(app));
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
  const copyCode = () => navigator.clipboard?.writeText(f.personal_id.trim()).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); }, () => undefined);
  return wrap(<>
    <div className="row row-between row-wrap gap-sm">
      <b style={{ color: '#1D3560' }}>Homes in Georgia · Rent in Tbilisi</b>
      <Seg value={lang} onChange={setLang} options={(Object.keys(LANG_LABEL) as Lang[]).map((l) => ({ value: l, label: LANG_LABEL[l] }))} />
    </div>
    <div className="form-grid">
      <Field label={t.name}><Input value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></Field>
      <Field label={t.pid}><Input inputMode="numeric" value={f.personal_id} onChange={(e) => setF({ ...f, personal_id: e.target.value })} /></Field>
      <Field label={t.phone}><Input type="tel" inputMode="tel" value={f.phone} onChange={(e) => setF({ ...f, phone: phoneInput(e) })} /></Field>
    </div>
    <RegistryApps base={`/public/exclusive/${token}/registry`} personalId={lookup} picked={app} onPick={setApp} t={td} />
    <div className="col gap-sm">
      <FilePick label={app ? td.upload : td.extract} accept="application/pdf,.pdf" file={extract} onFile={pickExtract} t={td} hint={app ? undefined : <>
        {td.extractHow}
        <span className="row row-wrap gap-sm" style={{ marginTop: 8 }}>
          <a className="btn btn-secondary btn-sm" href={REGISTRY_URL} target="_blank" rel="noreferrer"><ExternalLink size={14} />{td.openRegistry}</a>
          {lookup && <Button variant="secondary" size="sm" onClick={copyCode}><Copy size={14} />{copied ? td.copied : td.copyCode}</Button>}
        </span>
      </>} />
      {extract && fromExtract && <div className="alert alert-ok">{td.fromExtract}: <b>{fromExtract}</b></div>}
      {extract && fromExtract === null && !q.data.cadastral_code && <div className="form-grid"><Field label={t.cad} hint={cadBad ? <span style={{ color: 'var(--accent)' }}>{t.cadBad}</span> : undefined}><Input value={f.cadastral_code} placeholder="01.14.12.003.045" onChange={(e) => setF({ ...f, cadastral_code: cadastralInput(e) })} onBlur={() => { if (!cadBad && f.cadastral_code !== q.data?.cadastral_code) setF({ ...f, cadastral_code: cadastralNorm(f.cadastral_code) }); }} /></Field></div>}
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
