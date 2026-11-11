// /sign/<token>: the owner confirms the exclusive cooperation and signs with a finger (no CRM login; ported from
// PlatformDGE/hig-sign). The PDF is built here and attached to the object (backend properties.sign_contract).
import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { http } from '../api/client';
import { errText } from '../components/toast';
import { Button, ErrorBox, Field, Input, Loading, Seg } from '../components/ui';
import { LANG_LABEL, buildExclusivePdf, confirmText, tbilisiNow, type Lang } from '../lib/exclusivePdf';
import { fmtDateTime } from '../lib/format';

interface SignView { id: number; address: string; cadastral_code: string; owner_name: string; owner_phone: string; signed_at: string | null }

const UI: Record<Lang, { name: string; pid: string; phone: string; cad: string; sign: string; clear: string; agree: string; send: string; done: string }> = {
  ru: { name: 'ФИО', pid: 'Личный номер', phone: 'Телефон', cad: 'Кадастровый код', sign: 'Подпишите пальцем', clear: 'Стереть', agree: 'С условиями согласен(на)', send: 'Подписать', done: 'Договор подписан. Спасибо!' },
  ka: { name: 'სახელი და გვარი', pid: 'პირადი ნომერი', phone: 'ტელეფონი', cad: 'საკადასტრო კოდი', sign: 'მოაწერეთ ხელი თითით', clear: 'წაშლა', agree: 'ვეთანხმები პირობებს', send: 'ხელმოწერა', done: 'ხელშეკრულება ხელმოწერილია. გმადლობთ!' },
  en: { name: 'Full name', pid: 'Personal ID number', phone: 'Phone', cad: 'Cadastral code', sign: 'Sign with your finger', clear: 'Clear', agree: 'I agree to the terms', send: 'Sign', done: 'Signed. Thank you!' },
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
  const [lang, setLang] = useState<Lang>('ru');
  const [f, setF] = useState({ full_name: '', personal_id: '', phone: '', cadastral_code: '' });
  const [ink, setInk] = useState(false);
  const [agree, setAgree] = useState(false);
  const pad = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    if (q.data) setF((x) => ({ ...x, full_name: q.data.owner_name, phone: q.data.owner_phone, cadastral_code: q.data.cadastral_code }));
  }, [q.data]);
  const t = UI[lang];
  const sign = useMutation({
    mutationFn: async () => {
      const v = q.data!;
      const pdf = await buildExclusivePdf(v.id, { ...f, address: v.address, date: tbilisiNow() }, pad.current!, navigator.userAgent);
      const fd = new FormData();
      Object.entries(f).forEach(([k, val]) => fd.append(k, val.trim()));
      fd.append('consent', 'true');
      fd.append('file', new Blob([pdf], { type: 'application/pdf' }), `exclusive-${v.id}.pdf`);
      return http.post<SignView>(`/public/exclusive/${token}`, fd);
    },
    onSuccess: () => q.refetch(),
  });

  const wrap = (body: React.ReactNode) => <div style={{ maxWidth: 640, margin: '0 auto', padding: '24px 16px' }}><div className="card card-pad col gap-lg">{body}</div></div>;
  if (q.isLoading) return wrap(<Loading />);
  if (q.error || !q.data) return wrap(<ErrorBox error={q.error} />);
  if (q.data.signed_at) return wrap(<div className="alert alert-ok">{t.done} · {fmtDateTime(q.data.signed_at)}</div>);
  const [title, lines] = confirmText(lang, { ...f, address: q.data.address, date: tbilisiNow().split(',')[0] });
  const ready = f.full_name.trim() && f.personal_id.trim() && f.phone.trim() && ink && agree;
  return wrap(<>
    <div className="row row-between row-wrap gap-sm">
      <b style={{ color: '#1D3560' }}>Homes in Georgia · Rent in Tbilisi</b>
      <Seg value={lang} onChange={setLang} options={(Object.keys(LANG_LABEL) as Lang[]).map((l) => ({ value: l, label: LANG_LABEL[l] }))} />
    </div>
    <div className="form-grid">
      <Field label={t.name}><Input value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></Field>
      <Field label={t.pid}><Input inputMode="numeric" value={f.personal_id} onChange={(e) => setF({ ...f, personal_id: e.target.value })} /></Field>
      <Field label={t.phone}><Input type="tel" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Field>
      <Field label={t.cad}><Input value={f.cadastral_code} onChange={(e) => setF({ ...f, cadastral_code: e.target.value })} /></Field>
    </div>
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
