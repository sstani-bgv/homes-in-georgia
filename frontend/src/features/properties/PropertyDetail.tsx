// Property page: stepper (C2–C4), operator review (B7a), publish (B7b), and the full object card: gallery, parameters,
// owner, where it is published, its ads on other sites, deals, history. Used by /objects/:id and /m/properties/:id.
import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Archive, Camera, CheckCircle2, ChevronLeft, ChevronRight, Clock, ExternalLink, Home, Pencil, Phone, PhoneOff, Sparkles, Trash2, Upload, Wand2, X } from 'lucide-react';
import { api } from '../../api';
import { itemsOf, mediaUrl } from '../../api/client';
import type { Media, Property } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { AiFlagBadges, DupCheck, PropertyStatusBadge, Timeline } from '../../components/domain';
import { errText, useToast } from '../../components/toast';
import { Badge, Button, Card, ErrorBox, Field, Input, KV, Loading, Modal, Seg, Select, Switch, Textarea } from '../../components/ui';
import { DISTRICTS, SOURCE_LABEL, bedroomsLabel, fmtDate, fmtDateTime, isoToLocal, localToIso, money } from '../../lib/format';
import { DealStatusBadge } from '../../pages/Deals';

export const ROOMS = ['Гостиная', 'Кухня', 'Спальня 1', 'Спальня 2', 'Спальня 3', 'Ванная', 'Балкон', 'Вид из окна', 'Подъезд'];
const STEPS = ['Взят', 'Звонок', 'Собственник', 'Съёмка', 'Фото', 'Проверка', 'Публикация'];

function stepOf(p: Property): number {
  switch (p.status) {
    case 'new': return 0;
    case 'taken': case 'call_failed': return 1;
    case 'agent_marked': return 2;
    case 'owner_confirmed': return 3;
    case 'shoot_scheduled': case 'media_uploaded': case 'returned': return 4;
    case 'review': return 5;
    case 'published': case 'rented': return 7;
    default: return 0;
  }
}

function Stepper({ current }: { current: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current?.querySelector('.step.current') as HTMLElement | null;
    if (el && ref.current) ref.current.scrollLeft = el.offsetLeft - ref.current.clientWidth / 2 + el.clientWidth / 2;
  }, [current]);
  return (
    <div className="stepper" ref={ref}>
      {STEPS.map((s, i) => (
        <div key={s} className={`step ${i < current ? 'done' : i === current ? 'current' : ''}`}>
          <span className="dot">{i < current ? <CheckCircle2 size={16} /> : i + 1}</span>
          <span className="step-label">{s}</span>
        </div>
      ))}
    </div>
  );
}

function useProp(id: number) {
  const qc = useQueryClient();
  const toast = useToast();
  const set = (p: Property) => {
    qc.setQueryData(['property', id], (old: Property | undefined) => ({ ...(old || {}), ...p }));
    qc.invalidateQueries({ queryKey: ['property', id] });
    qc.invalidateQueries({ queryKey: ['property-history', id] });
    qc.invalidateQueries({ queryKey: ['properties'] });
  };
  return { qc, toast, set };
}

function OwnerPhone({ p }: { p: Property }) {
  const phone = p.owner?.phone || p.owner_phone;
  if (!phone) return <span className="muted">скрыт</span>;
  return <a href={`tel:${phone.replace(/\s/g, '')}`} style={{ fontWeight: 700 }}>{phone}</a>;
}

function CallStep({ p }: { p: Property }) {
  const { toast, set } = useProp(p.id);
  const [reached, setReached] = useState(false);
  const [isOwner, setIsOwner] = useState<'owner' | 'agent'>('owner');
  const [name, setName] = useState(p.owner?.name || '');
  const [phone, setPhone] = useState(p.owner?.phone || '');
  const fail = useMutation({ mutationFn: () => api.properties.call(p.id, false), onSuccess: (r) => { set(r); toast('Бот напомнит позвонить через 2 часа'); }, onError: (e) => toast(errText(e), true) });
  const save = useMutation({
    mutationFn: async () => {
      await api.properties.call(p.id, true);
      return api.properties.owner(p.id, { is_owner: isOwner === 'owner', owner_name: name, owner_phone: phone });
    },
    onSuccess: (r) => { set(r); toast(isOwner === 'owner' ? 'Собственник подтверждён' : 'Объект в базе с пометкой «агент»'); },
    onError: (e) => toast(errText(e), true),
  });
  return (
    <div className="col">
      <div className="row row-wrap">
        <Phone size={18} color="var(--brand)" />
        <span className="label">Собственник</span>
        <b>{p.owner?.name || '—'}</b>
        <OwnerPhone p={p} />
        {p.call_attempts ? <Badge tone="grey">попыток: {p.call_attempts}</Badge> : null}
      </div>
      {p.status === 'call_failed' && p.next_call_at && <div className="alert alert-info">Не дозвонились · бот напомнит в {fmtDateTime(p.next_call_at)}</div>}
      <div className="alert alert-info" style={{ fontWeight: 500 }}>
        <Sparkles size={16} style={{ flex: 'none', marginTop: 2 }} />
        <span>Подсказка: представьтесь, уточните, сдаёт ли собственник сам, свободна ли квартира и когда удобно провести съёмку. Спросите про животных и минимальный срок.</span>
      </div>
      {!reached ? (
        <div className="row row-wrap">
          <Button onClick={() => setReached(true)}><Phone size={16} />Дозвонился</Button>
          <Button variant="secondary" loading={fail.isPending} onClick={() => fail.mutate()}><PhoneOff size={16} />Не дозвонился</Button>
        </div>
      ) : (
        <div className="card card-pad col" style={{ background: 'var(--brand-tint)' }}>
          <span className="label">Кто ответил</span>
          <Seg value={isOwner} onChange={setIsOwner} options={[{ value: 'owner', label: 'Собственник' }, { value: 'agent', label: 'Агент' }]} />
          <div className="form-grid">
            <Field label="Имя"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
            <Field label="Телефон"><Input value={phone} onChange={(e) => setPhone(e.target.value)} inputMode="tel" /></Field>
          </div>
          {isOwner === 'agent' && <div className="muted" style={{ fontSize: 12 }}>Объект останется в базе с пометкой «агент», а номер попадёт в список агентов.</div>}
          <div className="row"><Button loading={save.isPending} onClick={() => save.mutate()}>Сохранить</Button><Button variant="ghost" onClick={() => setReached(false)}>Отмена</Button></div>
        </div>
      )}
    </div>
  );
}

/** What the owner said, besides "yes": thinking (bot reminds to call back), rented it out himself, won't work with us. */
function OwnerOutcomes({ p }: { p: Property }) {
  const { toast, set } = useProp(p.id);
  const [thinkOpen, setThinkOpen] = useState(false);
  const [until, setUntil] = useState(() => isoToLocal(new Date(Date.now() + 2 * 864e5).toISOString()));
  const [comment, setComment] = useState('');
  const done = (msg: string) => (r: Property) => { set(r); toast(msg); setThinkOpen(false); setComment(''); };
  const fail = (e: unknown) => toast(errText(e), true);
  const think = useMutation({ mutationFn: () => api.properties.thinking(p.id, localToIso(until)!, comment), onSuccess: done('Бот напомнит перезвонить'), onError: fail });
  const rented = useMutation({ mutationFn: (c: string) => api.properties.rentedSelf(p.id, c), onSuccess: done('Отмечено: собственник сдал сам'), onError: fail });
  const refused = useMutation({ mutationFn: (c: string) => api.properties.refused(p.id, c), onSuccess: done('Объект вернулся в ленту: другой агент увидит, что вы звонили'), onError: fail });
  const ask = (q: string, m: { mutate: (c: string) => void }) => { const c = window.prompt(q, ''); if (c !== null) m.mutate(c); };
  return (
    <div className="col gap-sm mt">
      {p.status !== 'shoot_scheduled' && p.status !== 'call_failed' && p.next_call_at && (
        <div className="alert alert-info">Собственник думает · бот напомнит перезвонить {fmtDateTime(p.next_call_at)}</div>
      )}
      <div className="row row-wrap gap-sm">
        {p.status !== 'shoot_scheduled' && <Button variant="secondary" size="sm" onClick={() => setThinkOpen(true)}><Clock size={14} />Думает</Button>}
        <Button variant="secondary" size="sm" loading={rented.isPending} onClick={() => ask('Собственник сдал сам? Объект закроется и не вернётся в ленту. Комментарий (необязательно):', rented)}><Home size={14} />Сдал сам</Button>
        <Button variant="ghost" size="sm" loading={refused.isPending} onClick={() => ask('Собственник не хочет работать. Комментарий для следующего агента (необязательно):', refused)}><PhoneOff size={14} />Не хочет работать</Button>
      </div>
      <Modal open={thinkOpen} onClose={() => setThinkOpen(false)} title="Собственник думает">
        <div className="col gap-lg">
          <Field label="Когда перезвонить"><Input type="datetime-local" value={until} onChange={(e) => setUntil(e.target.value)} /></Field>
          <Field label="Комментарий"><Input value={comment} placeholder="Например: есть свои клиенты, ждёт их ответ" onChange={(e) => setComment(e.target.value)} /></Field>
          <div className="muted" style={{ fontSize: 12 }}>Объект остаётся за вами. Если собственник согласится — назначайте съёмку, если сдаст сам — нажмите «Сдал сам».</div>
          <Button block disabled={!until} loading={think.isPending} onClick={() => think.mutate()}>Сохранить</Button>
        </div>
      </Modal>
    </div>
  );
}

function ShootStep({ p }: { p: Property }) {
  const { toast, set } = useProp(p.id);
  const [at, setAt] = useState(isoToLocal(p.shoot_at));
  const [dup, setDup] = useState<string | null>(p.dup_check || null);
  const m = useMutation({
    mutationFn: () => api.properties.shoot(p.id, localToIso(at)),
    onSuccess: (r) => { setDup(r.dup_check || null); set(r); toast('Съёмка назначена'); },
    onError: (e) => toast(errText(e), true),
  });
  return (
    <div className="col">
      <div className="row row-wrap" style={{ alignItems: 'flex-end' }}>
        <Field label="Дата и время съёмки"><Input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} /></Field>
        <Button disabled={!at} loading={m.isPending} onClick={() => m.mutate()}><Camera size={16} />Назначить съёмку</Button>
      </div>
      <DupCheck value={dup} />
    </div>
  );
}

function MediaStep({ p, canEdit }: { p: Property; canEdit: boolean }) {
  const { toast, set, qc } = useProp(p.id);
  const [room, setRoom] = useState(ROOMS[0]);
  const fileRef = useRef<HTMLInputElement>(null);
  const checklist = p.checklist || {};
  const media = p.media || [];
  const upload = useMutation({
    mutationFn: (files: File[]) => api.properties.uploadMedia(p.id, files, room),
    onSuccess: (r) => { set(r); toast('Файлы загружены'); },
    onError: (e) => toast(errText(e), true),
  });
  const del = useMutation({ mutationFn: (mid: number) => api.properties.deleteMedia(p.id, mid), onSuccess: () => qc.invalidateQueries({ queryKey: ['property', p.id] }) });
  const check = useMutation({ mutationFn: (c: Record<string, boolean>) => api.properties.checklist(p.id, c), onSuccess: set });
  const submit = useMutation({ mutationFn: () => api.properties.submit(p.id), onSuccess: (r) => { set(r); toast('Отправлено оператору на проверку'); }, onError: (e) => toast(errText(e), true) });
  const done = ROOMS.filter((r) => checklist[r] || media.some((m) => m.room === r)).length;

  return (
    <div className="col">
      {p.status === 'returned' && p.review_comment && <div className="alert alert-warn">Оператор вернул: {p.review_comment}</div>}
      {p.shoot_at && <div className="muted">Съёмка: <b>{fmtDateTime(p.shoot_at)}</b></div>}
      {p.status === 'shoot_scheduled' && <DupCheck value={p.dup_check} />}
      {canEdit && (
        <div className="row row-wrap" style={{ alignItems: 'flex-end' }}>
          <Field label="Комната"><Select value={room} onChange={(e) => setRoom(e.target.value)} options={ROOMS.map((r) => ({ value: r, label: r }))} /></Field>
          <input ref={fileRef} type="file" accept="image/*,video/*" multiple hidden onChange={(e) => { const f = Array.from(e.target.files || []); if (f.length) upload.mutate(f); e.target.value = ''; }} />
          <Button loading={upload.isPending} onClick={() => fileRef.current?.click()}><Upload size={16} />Загрузить фото и видео</Button>
        </div>
      )}
      <MediaGrid p={p} onDelete={canEdit ? (id) => del.mutate(id) : undefined} />
      <div>
        <div className="row row-between"><span className="label">Чек-лист комнат</span><span className="muted num" style={{ fontWeight: 700 }}>{done}/{ROOMS.length}</span></div>
        <div className="row row-wrap gap-sm mt">
          {ROOMS.map((r) => {
            const on = !!checklist[r] || media.some((m) => m.room === r);
            return (
              <button key={r} type="button" className={`chip ${on ? 'active' : ''}`} disabled={!canEdit}
                onClick={() => check.mutate({ ...checklist, [r]: !checklist[r] })}>
                {on ? <CheckCircle2 size={14} /> : null}{r}
              </button>
            );
          })}
        </div>
      </div>
      {canEdit && (
        <Button size="lg" variant="ok" disabled={!media.length} loading={submit.isPending} onClick={() => submit.mutate()}>
          <CheckCircle2 size={18} />Фото и видео сняты · на проверку
        </Button>
      )}
    </div>
  );
}

type Shot = Pick<Media, 'kind' | 'room'> & { id: number | string; src: string };
const shotsOf = (p: Property): Shot[] => (p.media || []).map((m) => ({ id: m.id, kind: m.kind, room: m.room, src: m.url || m.path }));

function Lightbox({ items, index, onIndex, onClose }: { items: Shot[]; index: number; onIndex: (i: number) => void; onClose: () => void }) {
  const go = (d: number) => onIndex((index + d + items.length) % items.length);
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); if (e.key === 'ArrowLeft') go(-1); if (e.key === 'ArrowRight') go(1); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  });
  const m = items[index];
  return createPortal(
    <>
      <div className="overlay" onClick={onClose} />
      <div className="lightbox" role="dialog" aria-modal="true" onClick={onClose}>
        <div onClick={(e) => e.stopPropagation()}>
          {m.kind === 'video' ? <video key={m.src} src={mediaUrl(m.src)} controls autoPlay /> : <img src={mediaUrl(m.src)} alt={m.room || ''} />}
        </div>
        <div className="lightbox-bar" onClick={(e) => e.stopPropagation()}>
          {items.length > 1 && <Button variant="secondary" className="icon-btn" onClick={() => go(-1)} aria-label="Назад"><ChevronLeft size={18} /></Button>}
          <span className="badge">{index + 1} / {items.length}{m.room ? ` · ${m.room}` : ''}</span>
          {items.length > 1 && <Button variant="secondary" className="icon-btn" onClick={() => go(1)} aria-label="Вперёд"><ChevronRight size={18} /></Button>}
          <Button variant="secondary" className="icon-btn" onClick={onClose} aria-label="Закрыть"><X size={18} /></Button>
        </div>
      </div>
    </>,
    document.body,
  );
}

function MediaGrid({ p, items, onDelete }: { p?: Property; items?: Shot[]; onDelete?: (id: number) => void }) {
  const media = items || (p ? shotsOf(p) : []);
  const [open, setOpen] = useState<number | null>(null);
  if (!media.length) return <div className="muted" style={{ fontSize: 13 }}>Файлов пока нет</div>;
  return (
    <div className="photo-strip">
      {open !== null && <Lightbox items={media} index={open} onIndex={setOpen} onClose={() => setOpen(null)} />}
      {media.map((m, i) => (
        <div className="ph" key={m.id} onClick={() => setOpen(i)} style={{ cursor: 'zoom-in' }}>
          {m.kind === 'video' ? <video src={mediaUrl(m.src)} muted style={{ width: '100%', height: '100%', objectFit: 'cover' }} /> : <img src={mediaUrl(m.src)} alt={m.room || ''} loading="lazy" />}
          {m.room && <span className="room badge badge-blue">{m.room}</span>}
          {onDelete && <button className="del btn btn-danger icon-btn btn-sm" style={{ width: 28, height: 28 }} onClick={(e) => { e.stopPropagation(); onDelete(Number(m.id)); }} aria-label="Удалить"><Trash2 size={14} /></button>}
        </div>
      ))}
    </div>
  );
}

/** Own photos/videos, or the origin ad's photos until the shoot. */
function Gallery({ p }: { p: Property }) {
  const own = shotsOf(p);
  const fromAd = (p.listing?.photos || []).map((src, i): Shot => ({ id: `l${i}`, kind: 'photo', src }));
  return (
    <Card label="Фото и видео" title={own.length ? `Файлы объекта · ${own.length}` : fromAd.length ? 'Фото из объявления' : 'Галерея'}>
      <MediaGrid items={own.length ? own : fromAd} />
    </Card>
  );
}

const PUB_STATUS: Record<string, [string, 'ok' | 'grey' | 'warn']> = { published: ['опубликован', 'ok'], removed: ['снят', 'grey'], draft: ['черновик', 'warn'] };

/** «Где опубликован»: our posts per channel. Only the TG channel exists for now; site / IG / FB rows come later. */
function Publications({ p }: { p: Property }) {
  const pubs = (p.publications || []).filter((x) => x.channel === 'tg');
  return (
    <Card label="Каналы" title="Где опубликован">
      {!pubs.length ? <div className="muted" style={{ fontSize: 13 }}>Пока нигде не опубликован</div> : pubs.map((x) => (
        <div className="list-row" key={x.id}>
          <div className="grow">
            <div style={{ fontWeight: 700 }}>Telegram-канал</div>
            <div className="muted" style={{ fontSize: 12 }}>{fmtDateTime(x.status === 'removed' ? x.removed_at : x.published_at)}</div>
          </div>
          <Badge tone={PUB_STATUS[x.status]?.[1] || 'grey'}>{PUB_STATUS[x.status]?.[0] || x.status}</Badge>
          {x.url && <a className="btn btn-secondary btn-sm" href={x.url} target="_blank" rel="noreferrer"><ExternalLink size={14} />Пост</a>}
        </div>
      ))}
    </Card>
  );
}

/** «Объявления на других сайтах»: the outside ads of this flat. Links only for staff and the object's agent. */
function Ads({ p }: { p: Property }) {
  const ads = p.listings || [];
  return (
    <Card label="Дубли" title="Объявления на других сайтах">
      {!ads.length ? <div className="muted" style={{ fontSize: 13 }}>Других объявлений не найдено</div> : ads.map((a) => (
        <div className="list-row" key={a.listing_id}>
          <div className="grow">
            <div className="row gap-sm" style={{ fontWeight: 700 }}>{SOURCE_LABEL[a.source || ''] || a.source || '—'}{a.origin && <Badge tone="grey">первоисточник</Badge>}</div>
            <div className="muted" style={{ fontSize: 12 }}>{[money(a.price_usd), a.first_seen ? `с ${fmtDate(a.first_seen)}` : null].filter(Boolean).join(' · ')}</div>
          </div>
          {a.url ? <a className="btn btn-secondary btn-sm" href={a.url} target="_blank" rel="noreferrer"><ExternalLink size={14} />Открыть</a> : null}
        </div>
      ))}
    </Card>
  );
}

function Deals({ p, compact }: { p: Property; compact?: boolean }) {
  if (!p.deals?.length) return null;
  return (
    <Card label="Сделки" title={`Сделки по объекту · ${p.deals.length}`}>
      {p.deals.map((d) => (
        <Link className="list-row" key={d.id} to={compact ? `/m/deals/${d.id}` : `/deals/${d.id}`} style={{ color: 'inherit' }}>
          <div className="grow">
            <div style={{ fontWeight: 700 }}>Сделка #{d.id} · {money(d.price)}</div>
            <div className="muted" style={{ fontSize: 12 }}>{[d.agent, d.rent_from ? `${fmtDate(d.rent_from)} – ${fmtDate(d.rent_to)}` : fmtDate(d.created_at)].filter(Boolean).join(' · ')}</div>
          </div>
          <DealStatusBadge s={d.status} />
        </Link>
      ))}
    </Card>
  );
}

function ReviewStep({ p }: { p: Property }) {
  const { toast, set } = useProp(p.id);
  const [comment, setComment] = useState('');
  const missing = ROOMS.slice(0, 6).filter((r) => !(p.checklist || {})[r] && !(p.media || []).some((m) => m.room === r));
  const review = useMutation({
    mutationFn: (approve: boolean) => api.properties.review(p.id, approve, comment),
    onSuccess: (r) => { set(r); toast('Возвращено агенту'); },
    onError: (e) => toast(errText(e), true),
  });
  return (
    <div className="col">
      <span className="label">Автопроверки</span>
      <div className="col gap-sm">
        <div className="row gap-sm"><AiFlagBadges flags={p.ai_flags} />{!p.ai_flags?.owner && !p.ai_flags?.agent_suspect && !p.ai_flags?.duplicate && <span className="muted">Меток ИИ нет</span>}</div>
        <DupCheck value={p.dup_check} />
        {missing.length ? <div className="alert alert-warn">Не сняты: {missing.join(', ')}</div> : <div className="alert alert-ok">Все основные комнаты сняты</div>}
        {p.owner?.is_agent && <div className="alert alert-warn">Телефон собственника отмечен как агентский</div>}
      </div>
      <MediaGrid p={p} />
      <div className="muted" style={{ fontSize: 12 }}>Генератор откроется с полями, фото и видео объекта. После «Опубликовать в канал» объект сам станет «Опубликован», агенту придёт ссылка.</div>
      <Field label="Комментарий агенту"><Textarea value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Что переснять или поправить" /></Field>
      <div className="row row-wrap">
        {/* publishing goes through the agency generator (watermark, topics, owner info); it reports the post back */}
        <a className="btn btn-ok btn-lg" href={`/tools/post-generator?property_id=${p.id}`} target="_blank" rel="noreferrer"><CheckCircle2 size={18} />Одобрить → генератор постов</a>
        <Button variant="danger" size="lg" disabled={!comment.trim()} loading={review.isPending} onClick={() => review.mutate(false)}>Вернуть агенту</Button>
      </div>
    </div>
  );
}

function EditModal({ p, open, onClose }: { p: Property; open: boolean; onClose: () => void }) {
  const { toast, set } = useProp(p.id);
  type Form = Partial<Property> & { owner_name?: string; owner_phone?: string };
  const [f, setF] = useState<Form>({});
  const v: Form = { ...p, owner_name: p.owner?.name || '', owner_phone: p.owner?.phone || '', ...f };
  const upd = <K extends keyof Form>(k: K, val: Form[K]) => setF((x) => ({ ...x, [k]: val }));
  const save = useMutation({ mutationFn: () => api.properties.update(p.id, f), onSuccess: (r) => { set(r); toast('Сохранено'); onClose(); setF({}); }, onError: (e) => toast(errText(e), true) });
  const numOrNull = (s: string) => (s === '' ? null : Number(s));
  return (
    <Modal open={open} onClose={onClose} title="Поля объекта">
      <div className="form-grid">
        <Field label="Район"><Select value={v.district || ''} placeholder="—" onChange={(e) => upd('district', e.target.value)} options={DISTRICTS.map((d) => ({ value: d, label: d }))} /></Field>
        <Field label="Адрес"><Input value={v.address || ''} onChange={(e) => upd('address', e.target.value)} /></Field>
        <Field label="Цена, $"><Input type="number" value={v.price ?? ''} onChange={(e) => upd('price', numOrNull(e.target.value))} /></Field>
        <Field label="Спальни"><Input type="number" value={v.bedrooms ?? ''} onChange={(e) => upd('bedrooms', numOrNull(e.target.value))} /></Field>
        <Field label="Площадь, м²"><Input type="number" value={v.area ?? ''} onChange={(e) => upd('area', numOrNull(e.target.value))} /></Field>
        <Field label="Этаж"><Input type="number" value={v.floor ?? ''} onChange={(e) => upd('floor', numOrNull(e.target.value))} /></Field>
        <Field label="Собственник"><Input value={v.owner_name || ''} onChange={(e) => upd('owner_name', e.target.value)} /></Field>
        <Field label="Телефон собственника"><Input type="tel" value={v.owner_phone || ''} onChange={(e) => upd('owner_phone', e.target.value)} /></Field>
        <Field label="Кадастровый код" className="span2"><Input value={v.cadastral_code || ''} onChange={(e) => upd('cadastral_code', e.target.value)} /></Field>
        <Field label="Описание" className="span2"><Textarea value={v.description || ''} onChange={(e) => upd('description', e.target.value)} /></Field>
        <div className="span2"><Switch checked={!!v.is_exclusive} onChange={(x) => upd('is_exclusive', x)} label="Эксклюзив" /></div>
      </div>
      <div className="row mt-lg"><Button loading={save.isPending} onClick={() => save.mutate()}>Сохранить</Button><Button variant="ghost" onClick={onClose}>Отмена</Button></div>
    </Modal>
  );
}

export function PropertyDetail({ id, compact }: { id: number; compact?: boolean }) {
  const { user, isStaff } = useAuth();
  const { data: p, isLoading, error } = useQuery({ queryKey: ['property', id], queryFn: () => api.properties.get(id) });
  const hist = useQuery({ queryKey: ['property-history', id], queryFn: () => api.properties.history(id) });
  const { toast, set } = useProp(id);
  const [edit, setEdit] = useState(false);
  const archive = useMutation({ mutationFn: () => api.properties.archive(id), onSuccess: (r) => { set(r); toast('Объект в архиве'); } });
  const publish = useMutation({ mutationFn: () => api.properties.publish(id), onSuccess: (r) => { set(r); toast('Опубликовано'); }, onError: (e) => toast(errText(e), true) });
  const step = useMemo(() => (p ? stepOf(p) : 0), [p]);

  if (isLoading) return <Loading />;
  if (error || !p) return <ErrorBox error={error} />;
  const mine = p.agent_id === user?.id || p.agent?.id === user?.id;
  const canWork = mine || isStaff;
  const generator = `/tools/post-generator?property_id=${p.id}`;

  let action: React.ReactNode = null;
  if (['taken', 'call_failed'].includes(p.status)) action = canWork ? <CallStep p={p} /> : null;
  else if (p.status === 'owner_confirmed') action = canWork ? <ShootStep p={p} /> : null;
  else if (['shoot_scheduled', 'media_uploaded', 'returned'].includes(p.status)) action = <MediaStep p={p} canEdit={canWork} />;
  else if (p.status === 'review') action = isStaff ? <ReviewStep p={p} /> : <div className="alert alert-info">На проверке у оператора · обычно до 1 часа</div>;
  else if (p.status === 'agent_marked') action = <div className="alert alert-info">Ответил агент · объект остаётся в базе с пометкой «агент»</div>;
  else if (['published', 'rented'].includes(p.status)) action = (
    <div className="col">
      <div className="alert alert-ok">Опубликовано {fmtDateTime(p.published_at)}</div>
      <div className="row row-wrap">
        {p.post_url && <a className="btn" href={p.post_url} target="_blank" rel="noreferrer"><ExternalLink size={16} />Пост в канале</a>}
        {isStaff && <a className="btn btn-secondary" href={generator} target="_blank" rel="noreferrer"><Wand2 size={16} />Открыть генератор постов</a>}
      </div>
    </div>
  );
  // the media / review steps show the files with their own controls
  const mediaInAction = ['shoot_scheduled', 'media_uploaded', 'returned'].includes(p.status) || (p.status === 'review' && isStaff);
  const ownerVisible = !!(p.owner?.phone || p.owner_phone);

  return (
    <div className="col gap-lg">
      <div className="card card-pad">
        <div className="row row-between row-wrap" style={{ alignItems: 'flex-start' }}>
          <div>
            <div className="label">Объект #{p.id} · {SOURCE_LABEL[p.source_type || ''] || p.source_type || '—'}</div>
            <h1 style={{ fontSize: compact ? 20 : 24 }}>{p.address || 'Без адреса'}</h1>
            <div className="row row-wrap gap-sm mt">
              <PropertyStatusBadge status={p.status} />
              {p.is_exclusive && <Badge tone="solid">эксклюзив</Badge>}
              <AiFlagBadges flags={p.ai_flags} />
            </div>
          </div>
          <div className="price-col">
            <div className="price" style={{ fontSize: 24 }}>{money(p.price, p.currency || 'USD')}<span className="muted" style={{ fontSize: 12 }}>/мес</span></div>
            <div className="muted" style={{ fontSize: 13 }}>{[p.district, bedroomsLabel(p.bedrooms), p.area ? `${p.area} м²` : null].filter(Boolean).join(' · ')}</div>
          </div>
        </div>
        <div className="divider" />
        <Stepper current={step} />
      </div>

      <div className={compact ? 'col gap-lg' : 'split'}>
        <div className="col gap-lg">
          {action && (
            <Card label="Следующий шаг" title={STEPS[Math.min(step, STEPS.length - 1)]}>
              {action}
              {canWork && ['taken', 'call_failed', 'owner_confirmed', 'shoot_scheduled'].includes(p.status) && <OwnerOutcomes p={p} />}
            </Card>
          )}
          {isStaff && p.status === 'review' && (
            <div className="row row-wrap">
              <a className="btn btn-secondary" href={generator} target="_blank" rel="noreferrer"><Wand2 size={16} />Открыть генератор постов</a>
              <Button variant="ghost" loading={publish.isPending} onClick={() => publish.mutate()}>Опубликовать без генератора</Button>
            </div>
          )}
          {!mediaInAction && <Gallery p={p} />}
          <Publications p={p} />
          <Ads p={p} />
          <Deals p={p} compact={compact} />
          {!compact && <Card label="История" title="События объекта"><Timeline events={itemsOf(hist.data)} /></Card>}
        </div>
        <div className="col gap-lg">
          <Card label="Собственник" title={<>{p.owner?.name || '—'} {p.owner?.is_agent ? <Badge tone="warn">агент</Badge> : null}</>}>
            <KV items={[
              ['Телефон', <OwnerPhone p={p} />],
              ['Агент объекта', p.agent?.name || '—'],
            ]} />
            {!ownerVisible && p.owner && <div className="muted mt" style={{ fontSize: 12 }}>Контакты видны оператору и агенту объекта</div>}
          </Card>
          <Card label="Объект" title="Параметры" actions={canWork ? <Button size="sm" variant="secondary" onClick={() => setEdit(true)}><Pencil size={14} />Править</Button> : undefined}>
            <KV items={[
              ['Адрес', p.address || '—'],
              ['Район', p.district || '—'],
              ['Цена', money(p.price, p.currency || 'USD')],
              ['Спальни', bedroomsLabel(p.bedrooms) || '—'],
              ['Комнат', p.rooms ?? '—'],
              ['Площадь', p.area ? `${p.area} м²` : '—'],
              ['Этаж', p.floor ? `${p.floor}${p.total_floors ? ` из ${p.total_floors}` : ''}` : '—'],
              ['Кадастр', p.cadastral_code || '—'],
              ['Эксклюзив', p.is_exclusive ? 'да' : 'нет'],
              ['Дубль объекта', p.duplicate_of ? <Link to={compact ? `/m/properties/${p.duplicate_of}` : `/objects/${p.duplicate_of}`}>#{p.duplicate_of}</Link> : '—'],
              ['Создан', fmtDateTime(p.created_at)],
              ['Взят', fmtDateTime(p.taken_at)],
              ['Съёмка', fmtDateTime(p.shoot_at)],
              ['Опубликован', fmtDateTime(p.published_at)],
              ['Источник', p.source_url ? <a href={p.source_url} target="_blank" rel="noreferrer">открыть</a> : '—'],
            ]} />
            {p.description && <p className="muted mt" style={{ fontSize: 13 }}>{p.description}</p>}
            {isStaff && !['archived', 'rented'].includes(p.status) && (
              <Button variant="ghost" size="sm" className="mt" loading={archive.isPending} onClick={() => archive.mutate()}><Archive size={14} />В архив</Button>
            )}
          </Card>
          {compact && <Card label="История" title="События"><Timeline events={itemsOf(hist.data)} /></Card>}
        </div>
      </div>
      <EditModal p={p} open={edit} onClose={() => setEdit(false)} />
    </div>
  );
}
