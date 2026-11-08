// Property page: stepper (C2–C4), operator review (B7a), the post generator + publish (B7b), and the full object card: gallery, parameters,
// owner, where it is published, its ads on other sites, deals, history. Used by /objects/:id and /m/properties/:id.
import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Archive, ArrowRightLeft, Camera, CheckCircle2, ChevronLeft, ChevronRight, Clock, ExternalLink, Home, Pencil, Phone, PhoneOff, RotateCcw, Send, Sparkles, Trash2, Upload, Wand2, X } from 'lucide-react';
import { api } from '../../api';
import { itemsOf, mediaUrl, thumbUrl } from '../../api/client';
import type { Media, PostFields, Property } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { AiFlagBadges, CopyPhone, DupCheck, ExclusiveConflictBadge, PropertyStatusBadge, Timeline } from '../../components/domain';
import { errText, useToast } from '../../components/toast';
import { Badge, Button, Card, ErrorBox, Field, Input, KV, Loading, Modal, Seg, Select, Switch, Textarea } from '../../components/ui';
import { ARCHIVE_REASON, DISTRICTS, SOURCE_LABEL, bedroomsLabel, demandLabel, fmtDate, fmtDateTime, isoToLocal, localToIso, money } from '../../lib/format';
import { DealStatusBadge } from '../../pages/Deals';
import { RequestRow } from './requests';

const REQUEST_SENT = 'Изменение ушло оператору на проверку';

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
    qc.invalidateQueries({ queryKey: ['property-post', id] });
    qc.invalidateQueries({ queryKey: ['properties'] });
  };
  return { qc, toast, set };
}

function OwnerPhone({ p }: { p: Property }) {
  const phone = p.owner?.phone || p.owner_phone;
  if (!phone) return <span className="muted">скрыт</span>;
  return <span className="row gap-sm" style={{ display: 'inline-flex' }}><a href={`tel:${phone.replace(/\s/g, '')}`} style={{ fontWeight: 700 }}>{phone}</a><CopyPhone phone={phone} /></span>;
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
  const submit = useMutation({ mutationFn: () => api.properties.submit(p.id), onSuccess: (r) => { set(r); toast('Отправлено оператору'); }, onError: (e) => toast(errText(e), true) });
  const done = ROOMS.filter((r) => checklist[r] || media.some((m) => m.room === r)).length;
  // mirrors backend properties.REQUIRED_FIELDS: the card goes to the operator only complete
  const missing = ([['address', 'адрес'], ['price', 'цена'], ['bedrooms', 'спальни'], ['area', 'площадь'], ['floor', 'этаж']] as const)
    .filter(([f]) => p[f] == null || p[f] === '').map(([, l]) => l);

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
        <>
          <div className="muted" style={{ fontSize: 12 }}>После отправки фото, видео и чек-лист закрываются: дальше объектом занимается оператор. Если нужно что-то переснять, он вернёт объект с комментарием.</div>
          {missing.length > 0 && <div className="alert alert-warn">Заполните параметры объекта («Править» в блоке «Параметры»): {missing.join(', ')}</div>}
          <Button size="lg" variant="ok" disabled={!media.length || missing.length > 0} loading={submit.isPending}
            onClick={() => { if (window.confirm('Отправить объект оператору? Фото, видео и чек-лист больше нельзя будет менять.')) submit.mutate(); }}>
            <Send size={18} />Готово — отправить оператору
          </Button>
        </>
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
          {m.kind === 'video' ? <video src={mediaUrl(m.src)} muted style={{ width: '100%', height: '100%', objectFit: 'cover' }} /> : <img src={thumbUrl(m.src)} alt={m.room || ''} loading="lazy" />}
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

const srcName = (s?: string | null) => SOURCE_LABEL[s || ''] || s || 'другой сайт';

/** Exclusive still advertised elsewhere: the agent asks the owner to take the ad down, the operator checks and presses
 * «Проверено: снято» (manual on purpose: ss.ge / myhome block server checks). Until then it can't go out as an exclusive. */
function ExclusiveConflict({ p }: { p: Property }) {
  const { isStaff } = useAuth();
  const { toast, set } = useProp(p.id);
  const removed = useMutation({
    mutationFn: (lid: string) => api.properties.adRemoved(p.id, lid),
    onSuccess: (r) => { set(r); toast('Отмечено: объявление снято'); },
    onError: (e) => toast(errText(e), true),
  });
  const ads = p.exclusive_conflict || [];
  if (!ads.length) return null;
  return (
    <Card label="Эксклюзив" title={`Эксклюзив висит на ${[...new Set(ads.map((a) => srcName(a.source)))].join(', ')}`}>
      <div className="alert alert-warn">Задача агенту: попросить собственника снять объявление. Публикация как эксклюзив закрыта, пока оператор не проверит, что объявление снято.</div>
      {ads.map((a) => (
        <div className="list-row" key={a.listing_id}>
          <div className="grow" style={{ fontWeight: 700 }}>{srcName(a.source)}</div>
          {a.url && <a className="btn btn-secondary btn-sm" href={a.url} target="_blank" rel="noreferrer"><ExternalLink size={14} />Открыть</a>}
          {isStaff && (
            <Button size="sm" loading={removed.isPending && removed.variables === a.listing_id}
              onClick={() => { if (window.confirm('Вы открыли ссылку и убедились, что объявление снято с сайта?')) removed.mutate(a.listing_id); }}>
              Проверено: снято
            </Button>
          )}
        </div>
      ))}
    </Card>
  );
}

/** Stage 7: an object typed in a deal form; its deal is confirmed only after the operator checks it here. */
function NeedsCheck({ p }: { p: Property }) {
  const { isStaff } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const m = useMutation({
    mutationFn: () => api.properties.verify(p.id),
    onSuccess: (r) => { qc.setQueryData(['property', p.id], r); qc.invalidateQueries({ queryKey: ['deal'] }); toast('Объект проверен · сделку можно подтвердить'); },
    onError: (e) => toast(errText(e), true),
  });
  return (
    <div className="alert alert-warn">
      <span className="grow">Новый объект из сделки — проверить: адрес, собственник и телефон, кадастр и ссылку. {isStaff ? 'Сделка по нему ждёт этой проверки.' : 'Оператор проверит его перед подтверждением сделки.'}</span>
      {isStaff && <Button size="sm" variant="ok" loading={m.isPending} onClick={() => m.mutate()}><CheckCircle2 size={14} />Объект проверен</Button>}
    </div>
  );
}

/** Stage 10: active clients this object fits; owner first: after the call «Показать объект клиентам»
 * opens it to clients with the ad's photos and sends it to them (at most 10, never twice to one client). */
function Demand({ p }: { p: Property }) {
  const { isStaff } = useAuth();
  const { qc, toast, set } = useProp(p.id);
  const { data: d } = useQuery({ queryKey: ['property-demand', p.id], queryFn: () => api.properties.demand(p.id) });
  const m = useMutation({
    mutationFn: () => api.properties.fastTrack(p.id),
    onSuccess: (r) => {
      set(r);
      qc.invalidateQueries({ queryKey: ['property-demand', p.id] });
      toast(r.sent ? `Объект отправлен клиентам: ${r.sent}` : 'Объект открыт клиентам · отправлять сейчас некому');
    },
    onError: (e) => toast(errText(e), true),
  });
  if (!d || (!d.count && !d.fast_track_at)) return null;
  return (
    <Card label="Спрос" title={d.count ? demandLabel(d.count) : 'Подходящих активных клиентов сейчас нет'}>
      <div className="muted" style={{ fontSize: 13 }}>По анкетам клиентов: район, бюджет, спальни, животные, срок. Активные: писали боту за {d.active_days} дней, сделки ещё нет.</div>
      {d.fast_track_at ? (
        <div className="alert alert-ok mt">Собственник согласен · объект показан клиентам {fmtDateTime(d.fast_track_at)} · отправлено: {d.pushed}</div>
      ) : d.can_fast_track && (
        <div className="col gap-sm mt">
          <Button variant="ok" loading={m.isPending} onClick={() => { if (window.confirm('Собственник согласен показать квартиру клиентам? Объект уйдёт подходящим клиентам в бот.')) m.mutate(); }}>
            <Send size={16} />Показать объект клиентам ({d.count})
          </Button>
          <div className="muted" style={{ fontSize: 12 }}>Нажимайте, когда собственник согласился на показы: объект уйдёт в бот подходящим клиентам с фото из объявления</div>
        </div>
      )}
      {d.clients.length > 0 && (
        <div className="mt">
          {d.clients.map((c, i) => {
            const name = isStaff && c.id ? c.name || (c.username ? `@${c.username}` : `Клиент #${c.id}`) : `Клиент ${i + 1}`;
            const body = (
              <>
                <div className="grow">
                  <div style={{ fontWeight: 600 }}>{name}</div>
                  <div className="muted" style={{ fontSize: 12 }}>{c.profile || 'анкета без условий'}</div>
                </div>
                {c.sent && <Badge tone="ok">отправлено</Badge>}
              </>
            );
            // client cards are staff only (/clients/:id); agents see anonymous rows
            return isStaff && c.id
              ? <Link className="list-row" key={c.id} to={`/clients/${c.id}`} style={{ color: 'inherit' }}>{body}</Link>
              : <div className="list-row" key={c.id ?? i}>{body}</div>;
          })}
        </div>
      )}
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

const CHANNEL_LABEL: Record<string, string> = { tg: 'Telegram-канал' };
const ALBUM_MAX = 10; // Telegram album limit
const CAPTION_MAX = 1024; // Telegram caption limit for a post with media

/** The post generator in the card (replaces the agency's HTML generator): the caption built by the backend from the
 * object (post_text), editable; the files to post, picked and ordered; the channel (TG only for now). */
function PostComposer({ p }: { p: Property }) {
  const { toast, set } = useProp(p.id);
  const draft = useQuery({ queryKey: ['property-post', p.id], queryFn: () => api.properties.post(p.id) });
  const [edited, setEdited] = useState<string | null>(null); // null = the generated text
  const files = p.media || [];
  const [order, setOrder] = useState<number[]>(() => files.map((m) => m.id));
  const [off, setOff] = useState<Set<number>>(() => new Set());
  const byId = new Map(files.map((m) => [m.id, m]));
  const ids = [...order.filter((id) => byId.has(id)), ...files.filter((m) => !order.includes(m.id)).map((m) => m.id)];
  const picked = ids.filter((id) => !off.has(id)).slice(0, ALBUM_MAX);
  const text = edited ?? draft.data?.text ?? '';
  const tags = [...new Set(text.match(/#[\p{L}\p{N}_]+/gu) || [])];
  const blocked = !!p.exclusive_conflict?.length;
  const publish = useMutation({
    mutationFn: () => api.properties.publish(p.id, { text, media_ids: picked, channel: 'tg' }),
    onSuccess: (r) => { set(r); toast('Опубликовано: агенту ушла ссылка на пост'); },
    onError: (e) => toast(errText(e), true),
  });
  const move = (i: number, d: number) => {
    const next = [...ids];
    [next[i], next[i + d]] = [next[i + d], next[i]];
    setOrder(next);
  };
  const toggle = (id: number) => setOff((o) => { const n = new Set(o); n.has(id) ? n.delete(id) : n.add(id); return n; });
  if (draft.isLoading) return <Loading />;
  if (draft.error) return <ErrorBox error={draft.error} />;
  return (
    <div className="col">
      <div className="row row-wrap gap-sm">
        <span className="label">Канал</span>
        {(draft.data?.channels || ['tg']).map((c) => <span key={c} className="chip active"><Send size={14} />{CHANNEL_LABEL[c] || c}</span>)}
      </div>
      <Field label="Текст поста" hint="Собран из полей объекта и полей поста (метро, удобства, животные, сроки — «Править» в «Параметрах»). 📍[адрес](ссылка) станет ссылкой на карту.">
        <Textarea value={text} onChange={(e) => setEdited(e.target.value)} rows={16} style={{ fontFamily: 'inherit', lineHeight: 1.45 }} />
      </Field>
      <div className="row row-between row-wrap gap-sm">
        <div className="row row-wrap gap-sm">
          {tags.map((t) => <Badge key={t} tone="grey">{t}</Badge>)}
          {edited !== null && <Button size="sm" variant="ghost" onClick={() => setEdited(null)}><RotateCcw size={14} />Исходный текст</Button>}
        </div>
        <span className={picked.length && text.length > CAPTION_MAX ? '' : 'faint'} style={{ fontSize: 12, fontWeight: 700, color: picked.length && text.length > CAPTION_MAX ? 'var(--red)' : undefined }}>
          {text.length}{picked.length ? ` / ${CAPTION_MAX}` : ''}
        </span>
      </div>
      {picked.length > 0 && text.length > CAPTION_MAX && <div className="alert alert-warn">Подпись к альбому длиннее {CAPTION_MAX} символов: Telegram обрежет конец.</div>}
      <div>
        <div className="row row-between"><span className="label">Фото и видео в посте</span><span className="muted num" style={{ fontWeight: 700 }}>{picked.length}/{Math.min(ids.length, ALBUM_MAX)}</span></div>
        <div className="muted" style={{ fontSize: 12, margin: '4px 0 8px' }}>Нажмите на файл, чтобы убрать его из поста; стрелками меняется порядок. Первый файл — обложка. На фото ставится водяной знак.</div>
        {!ids.length ? <div className="muted" style={{ fontSize: 13 }}>Файлов нет: уйдёт текстовый пост</div> : (
          <div className="photo-strip">
            {ids.map((id, i) => {
              const m = byId.get(id)!;
              const n = picked.indexOf(id);
              return (
                <div className="ph" key={id} onClick={() => (n >= 0 || picked.length < ALBUM_MAX) && toggle(id)}
                  style={{ cursor: 'pointer', opacity: n >= 0 ? 1 : 0.35, outline: n === 0 ? '3px solid var(--brand)' : undefined }}
                  role="checkbox" aria-checked={n >= 0} aria-label={`Файл ${i + 1}${m.room ? `, ${m.room}` : ''}`}>
                  {m.kind === 'video' ? <video src={mediaUrl(m.url || m.path)} muted style={{ width: '100%', height: '100%', objectFit: 'cover' }} /> : <img src={thumbUrl(m.url || m.path)} alt={m.room || ''} loading="lazy" />}
                  <span className="room badge badge-blue">{n >= 0 ? (n === 0 ? '1 · обложка' : n + 1) : 'не в посте'}</span>
                  <span className="row" style={{ position: 'absolute', right: 4, top: 4, gap: 4 }} onClick={(e) => e.stopPropagation()}>
                    {i > 0 && <button type="button" className="btn btn-secondary icon-btn btn-sm" style={{ width: 28, height: 28 }} onClick={() => move(i, -1)} aria-label="Раньше"><ChevronLeft size={14} /></button>}
                    {i < ids.length - 1 && <button type="button" className="btn btn-secondary icon-btn btn-sm" style={{ width: 28, height: 28 }} onClick={() => move(i, 1)} aria-label="Позже"><ChevronRight size={14} /></button>}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </div>
      {blocked && <div className="alert alert-warn">Эксклюзив ещё висит на других сайтах: публикация закрыта до «Проверено: снято».</div>}
      <Button variant="ok" size="lg" disabled={blocked || !text.trim()} loading={publish.isPending} onClick={() => publish.mutate()}>
        <Send size={18} />Опубликовать
      </Button>
    </div>
  );
}

function ReviewStep({ p }: { p: Property }) {
  const { toast, set } = useProp(p.id);
  const [comment, setComment] = useState('');
  const missing = ROOMS.slice(0, 6).filter((r) => !(p.checklist || {})[r] && !(p.media || []).some((m) => m.room === r));
  const back = useMutation({
    mutationFn: () => api.properties.review(p.id, false, comment),
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
      <div className="divider" />
      <PostComposer p={p} />
      <div className="divider" />
      <Field label="Вернуть агенту" hint="Агент получит комментарий в Telegram и снова сможет загрузить фото"><Textarea value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Что переснять или поправить" /></Field>
      <div className="row"><Button variant="danger" disabled={!comment.trim()} loading={back.isPending} onClick={() => back.mutate()}>Вернуть агенту</Button></div>
    </div>
  );
}

/** A published object: staff can post it again (price changed, new photos) with the same generator. */
function Republish({ p }: { p: Property }) {
  const [open, setOpen] = useState(false);
  return open ? <div className="col"><div className="divider" /><PostComposer p={p} /></div>
    : <Button variant="secondary" onClick={() => setOpen(true)}><Wand2 size={16} />Опубликовать повторно</Button>;
}

const agentLabel = (a?: { name: string; agent_tag?: string | null } | null) => (a ? `${a.name}${a.agent_tag ? ` · #${a.agent_tag}` : ''}` : '—');

/** «Сменить агента собственника…» (staff): the owner and his live objects move to another agent, with a history event. */
function OwnerTransfer({ p, request }: { p: Property; request?: boolean }) {
  const { toast, qc } = useProp(p.id);
  const [open, setOpen] = useState(false);
  const [to, setTo] = useState('');
  const [reason, setReason] = useState('');
  const agents = useQuery({ queryKey: ['users', 'field'], queryFn: () => api.users.list({ role: 'agent,recruit', active: true }), enabled: open });
  const m = useMutation({
    mutationFn: async () => { await (request ? api.properties.transferRequest(p.id, Number(to), reason) : api.owners.transfer(p.owner!.id, Number(to), reason)); },
    onSuccess: () => { ['property', 'property-history', 'property-post'].forEach((k) => qc.invalidateQueries({ queryKey: [k] })); toast(request ? REQUEST_SENT : 'Собственник передан агенту'); setOpen(false); },
    onError: (e) => toast(errText(e), true),
  });
  const options = itemsOf(agents.data).filter((u) => u.id !== p.owner?.agent?.id).map((u) => ({ value: u.id, label: agentLabel(u) }));
  return (
    <>
      <Button variant="secondary" size="sm" className="mt" onClick={() => setOpen(true)}><ArrowRightLeft size={14} />{request ? 'Запрос: сменить агента…' : 'Сменить агента собственника…'}</Button>
      <Modal open={open} onClose={() => setOpen(false)} title="Сменить агента собственника">
        <div className="col gap-lg">
          <div className="muted" style={{ fontSize: 13 }}>Сейчас: {agentLabel(p.owner?.agent)}. Вместе с собственником перейдут его объекты в работе у прежнего агента. Передача запишется в историю.</div>
          {request && <div className="alert alert-info" style={{ fontSize: 13 }}>Передачу проверит оператор: запрос уйдёт в «Запросы».</div>}
          <Field label="Новый агент"><Select value={to} placeholder="—" onChange={(e) => setTo(e.target.value)} options={options} /></Field>
          <Field label="Причина"><Input value={reason} placeholder="Например: агент ушёл из агентства" onChange={(e) => setReason(e.target.value)} /></Field>
          <Button block disabled={!to} loading={m.isPending} onClick={() => m.mutate()}>{request ? 'Отправить запрос' : 'Передать'}</Button>
        </div>
      </Modal>
    </>
  );
}

// the old generator's tag groups (backend services/properties.py POST_ONE / POST_MANY), stored without '#'
const opts = (o: Record<string, string>) => Object.entries(o).map(([value, label]) => ({ value, label }));
const POST_ONE: Record<string, [string, Record<string, string>]> = {
  prop_type: ['Тип', { Apartment: 'Квартира', House: 'Дом', Commercial: 'Коммерция', Duplex: 'Дуплекс', Townhouse: 'Таунхаус', Land: 'Земля', Retail: 'Ритейл', Business: 'Бизнес', Cafe: 'Кафе', Restaurant: 'Ресторан', BeautySalon: 'Салон красоты', Warehouses: 'Склад', Manufacture: 'Производство' }],
  metro: ['Метро', Object.fromEntries(['LibertySquare', 'Rustaveli', 'Marjanishvili', 'StationSquare', 'Tsereteli', 'Gotsiridze', 'Nadzaladevi', 'Didube', 'Grmagele', 'Guramishvili', 'Sarajishvili', 'AhmeteliTheatre', 'STUniversity', 'VazhaPshavela', 'Delisi', 'TCUniversity', 'MCUniversity', 'AvlabariMetro', 'IsaniMetro', '300Aragveli', 'SamgoriMetro', 'Varketili'].map((m) => [m, m]))],
  building: ['Дом', { NewBuilding: 'Новостройка', OldBuilding: 'Старый фонд', HistoricalBuilding: 'Исторический', Reconstruction: 'Реконструкция' }],
  design: ['Дизайн', { New: 'Новый', White: 'Белый', Grey: 'Серый', Yellow: 'Жёлтый', Mixed: 'Смешанный', Old: 'Старый', Retro: 'Ретро', UnderRepair: 'В ремонте' }],
  condition: ['Состояние (продажа)', { BlackFrame: 'Чёрный каркас', WhiteFrame: 'Белый каркас', GreenFrame: 'Зелёный каркас', FullyRenovated: 'С ремонтом' }],
  heating: ['Отопление', { CentralHeating: 'Центральное', GasHeating: 'Газовое', ElectricalHeating: 'Электрическое', ACHeating: 'Кондиционер', Fireplace: 'Камин' }],
  bathrooms: ['Санузлов', { '2Bathroom': '2', '3Bathroom': '3', '4Bathroom': '4' }],
  pets: ['Животные', { Allowed: 'Можно', NotAllowed: 'Нельзя', ByAgreement: 'По договорённости' }],
};
const POST_MANY: Record<string, [string, Record<string, string>]> = {
  amenities: ['Удобства', { Balcony: 'Балкон', Terrace: 'Терраса', Courtyard: 'Двор', WiFi: 'Wi-Fi', TV: 'ТВ', SmartTV: 'Smart TV', Bath: 'Ванна', Shower: 'Душ', Dishwasher: 'Посудомойка', Stove: 'Плита', Microwave: 'Микроволновка', WashingMachine: 'Стиральная машина', VacuumCleaner: 'Пылесос', Fireplace: 'Камин', Elevator: 'Лифт', Oven: 'Духовка', ParkingPlace: 'Парковка', Conditioner: 'Кондиционер' }],
  periods: ['Сроки аренды', { '1Day': '1 день', '1Month': '1 мес', '2Month': '2 мес', '3Month': '3 мес', '4Month': '4 мес', '5Month': '5 мес', '6Month': '6 мес', '9Month': '9 мес', '12Month': '12 мес' }],
};
const RENT_ONLY = ['pets', 'periods', 'tenants', 'deposit'];

/** «Параметры» rows for the filled post fields. */
function postFieldRows(f?: PostFields | null): [string, React.ReactNode][] {
  if (!f) return [];
  const rows: [string, React.ReactNode][] = [];
  if (f.deal) rows.push(['Сделка', f.deal === 'Sale' ? 'продажа' : 'аренда']);
  for (const [k, [label, o]] of Object.entries(POST_ONE)) { const x = (f as Record<string, string>)[k]; if (x) rows.push([label, o[x] || x]); }
  for (const [k, [label, o]] of Object.entries(POST_MANY)) { const x = (f as Record<string, string[]>)[k]; if (x?.length) rows.push([label, x.map((a) => o[a] || a).join(', ')]); }
  if (f.tenants) rows.push(['Жильцов', `до ${f.tenants}`]);
  if (f.deposit !== undefined) rows.push(['Залог', f.deposit ? money(f.deposit) : 'без залога']);
  if (f.sale_price) rows.push(['Цена продажи', money(f.sale_price)]);
  if (f.headline) rows.push(['Заголовок', f.headline]);
  return rows;
}

/** The post fields the agent fills (the old HTML generator's groups): one-of selects, amenity / term checkboxes, numbers. */
function PostFieldsForm({ value: f, onChange }: { value: PostFields; onChange: (f: PostFields) => void }) {
  const sale = f.deal === 'Sale';
  const set = (k: string, x: unknown) => onChange({ ...f, [k]: x === '' || x === null ? undefined : x });
  const num = (s: string) => (s === '' ? undefined : Number(s));
  const toggle = (k: string, a: string) => { const cur = (f as Record<string, string[]>)[k] || []; set(k, cur.includes(a) ? cur.filter((x) => x !== a) : [...cur, a]); };
  return (
    <div className="col gap-lg">
      <div className="row row-between row-wrap gap-sm">
        <span className="label">Поля поста</span>
        <Seg value={f.deal || 'Rent'} onChange={(d) => set('deal', d)} options={[{ value: 'Rent', label: 'Аренда' }, { value: 'Sale', label: 'Продажа' }]} />
      </div>
      <div className="form-grid">
        {Object.entries(POST_ONE).filter(([k]) => (sale ? !RENT_ONLY.includes(k) : k !== 'condition')).map(([k, [label, o]]) => (
          <Field key={k} label={label}><Select value={(f as Record<string, string>)[k] || ''} placeholder="—" onChange={(e) => set(k, e.target.value)} options={opts(o)} /></Field>
        ))}
        {sale ? <Field label="Цена продажи, $"><Input type="number" value={f.sale_price ?? ''} onChange={(e) => set('sale_price', num(e.target.value))} /></Field> : (
          <>
            <Field label="Жильцов, до"><Input type="number" min={1} value={f.tenants ?? ''} onChange={(e) => set('tenants', num(e.target.value))} /></Field>
            <Field label="Залог, $" hint="Пусто — как цена, 0 — без залога"><Input type="number" min={0} value={f.deposit ?? ''} onChange={(e) => set('deposit', num(e.target.value))} /></Field>
          </>
        )}
        <Field label="Заголовок" className="span2"><Input value={f.headline || ''} placeholder="Например: No one lived" onChange={(e) => set('headline', e.target.value)} /></Field>
      </div>
      {Object.entries(POST_MANY).filter(([k]) => !(sale && RENT_ONLY.includes(k))).map(([k, [label, o]]) => (
        <div key={k}>
          <span className="label">{label}</span>
          {k === 'amenities' && !sale && <div className="muted" style={{ fontSize: 12 }}>Лифт, духовка, парковка, кондиционер без галочки уйдут в пост с ✖️</div>}
          <div className="row row-wrap gap-sm mt">
            {opts(o).map(({ value: a, label: l }) => {
              const on = ((f as Record<string, string[]>)[k] || []).includes(a);
              return <button key={a} type="button" role="checkbox" aria-checked={on} className={`chip ${on ? 'active' : ''}`} onClick={() => toggle(k, a)}>{on ? <CheckCircle2 size={14} /> : null}{l}</button>;
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

function EditModal({ p, open, onClose }: { p: Property; open: boolean; onClose: () => void }) {
  const { toast, set } = useProp(p.id);
  type Form = Partial<Property> & { owner_name?: string; owner_phone?: string };
  const [f, setF] = useState<Form>({});
  const v: Form = { ...p, owner_name: p.owner?.name || '', owner_phone: p.owner?.phone || '', ...f };
  const upd = <K extends keyof Form>(k: K, val: Form[K]) => setF((x) => ({ ...x, [k]: val }));
  const [reason, setReason] = useState('');
  const save = useMutation({
    mutationFn: () => api.properties.update(p.id, { ...f, reason: reason || undefined }),
    onSuccess: (r) => { set(r); toast(r.change_request ? REQUEST_SENT : 'Сохранено'); onClose(); setF({}); setReason(''); },
    onError: (e) => toast(errText(e), true),
  });
  // after the operator's verification these fields go to «Запросы» (backend properties.LOCKED_AFTER_VERIFY)
  const locked = new Set(p.locked_fields || []);
  const lockHint = (k: string) => (locked.has(k) ? 'Изменение уйдёт оператору на проверку' : undefined);
  const numOrNull = (s: string) => (s === '' ? null : Number(s));
  const where = useQuery({ queryKey: ['property-elsewhere', p.id], queryFn: () => api.properties.elsewhere(p.id), enabled: open && !!v.is_exclusive });
  const found = where.data?.items || [];
  return (
    <Modal open={open} onClose={onClose} title="Поля объекта">
      <div className="form-grid">
        <Field label="Район"><Select value={v.district || ''} placeholder="—" onChange={(e) => upd('district', e.target.value)} options={DISTRICTS.map((d) => ({ value: d, label: d }))} /></Field>
        <Field label="Адрес" hint={lockHint('address')}><Input value={v.address || ''} onChange={(e) => upd('address', e.target.value)} /></Field>
        <Field label="Цена, $" hint={lockHint('price')}><Input type="number" value={v.price ?? ''} onChange={(e) => upd('price', numOrNull(e.target.value))} /></Field>
        <Field label="Спальни"><Input type="number" value={v.bedrooms ?? ''} onChange={(e) => upd('bedrooms', numOrNull(e.target.value))} /></Field>
        <Field label="Площадь, м²"><Input type="number" value={v.area ?? ''} onChange={(e) => upd('area', numOrNull(e.target.value))} /></Field>
        <Field label="Этаж"><Input type="number" value={v.floor ?? ''} onChange={(e) => upd('floor', numOrNull(e.target.value))} /></Field>
        <Field label="Этажей в доме"><Input type="number" value={v.total_floors ?? ''} onChange={(e) => upd('total_floors', numOrNull(e.target.value))} /></Field>
        <Field label="Собственник" hint={lockHint('owner_name')}><Input value={v.owner_name || ''} onChange={(e) => upd('owner_name', e.target.value)} /></Field>
        <Field label="Телефон собственника" hint={lockHint('owner_phone')}><Input type="tel" value={v.owner_phone || ''} onChange={(e) => upd('owner_phone', e.target.value)} /></Field>
        <Field label="Кадастровый код" className="span2" hint={lockHint('cadastral_code')}><Input value={v.cadastral_code || ''} onChange={(e) => upd('cadastral_code', e.target.value)} /></Field>
        <Field label="Описание" className="span2"><Textarea value={v.description || ''} onChange={(e) => upd('description', e.target.value)} /></Field>
        <div className="span2 col gap-sm">
          <Switch checked={!!v.is_exclusive} onChange={(x) => upd('is_exclusive', x)} label="Эксклюзив" />
          {locked.has('is_exclusive') && <span className="muted" style={{ fontSize: 12 }}>{lockHint('is_exclusive')}</span>}
          {v.is_exclusive && (where.isLoading ? <span className="muted" style={{ fontSize: 12 }}>Проверяем другие сайты…</span>
            : found.length ? (
              <div className="alert alert-warn col gap-sm" style={{ fontSize: 13 }}>
                <span><b>Этот объект найден ещё на: {[...new Set(found.map((a) => srcName(a.source)))].join(', ')}.</b> Попросите собственника снять объявления, иначе эксклюзив не опубликовать.</span>
                {found.map((a) => (
                  <span key={a.listing_id} className="muted">
                    {srcName(a.source)} · {a.verdict === 'linked' ? 'привязано к объекту' : a.verdict === 'same' ? 'тот же объект' : `похож: ${a.reasons.join(', ')}`}
                    {a.url && <> · <a href={a.url} target="_blank" rel="noreferrer">открыть</a></>}
                  </span>
                ))}
              </div>
            ) : where.data ? <div className="alert alert-ok" style={{ fontSize: 13 }}>На других сайтах этот объект не найден</div> : null)}
        </div>
      </div>
      <div className="divider" />
      <PostFieldsForm value={v.post_fields || {}} onChange={(pf) => upd('post_fields', pf)} />
      {locked.size > 0 && (
        <div className="col gap-sm mt-lg">
          <div className="alert alert-info" style={{ fontSize: 13 }}>Объект проверен оператором: собственник и телефон, адрес, кадастр, ссылка, эксклюзив, цена и залог меняются только через «Запрос оператору». Остальное сохранится сразу.</div>
          <Field label="Причина изменения" hint="Оператор увидит её в запросе"><Input value={reason} placeholder="Например: собственник сменил номер" onChange={(e) => setReason(e.target.value)} /></Field>
        </div>
      )}
      <div className="row mt-lg"><Button loading={save.isPending} onClick={() => save.mutate()}>Сохранить</Button><Button variant="ghost" onClick={onClose}>Отмена</Button></div>
    </Modal>
  );
}

/** «В архив» with a mandatory reason; objects are never deleted. After verification the agent's archive is a request. */
function ArchiveModal({ p, open, onClose }: { p: Property; open: boolean; onClose: () => void }) {
  const { toast, set } = useProp(p.id);
  const [reason, setReason] = useState('');
  const [comment, setComment] = useState('');
  const [dup, setDup] = useState('');
  const m = useMutation({
    mutationFn: () => api.properties.archive(p.id, { reason, comment: comment || undefined, duplicate_of: reason === 'duplicate' ? Number(dup.replace('#', '')) || null : null }),
    onSuccess: (r) => { set(r); toast(r.change_request ? REQUEST_SENT : 'Объект в архиве'); onClose(); },
    onError: (e) => toast(errText(e), true),
  });
  const ok = !!reason && (reason !== 'other' || !!comment.trim()) && (reason !== 'duplicate' || !!dup.trim());
  return (
    <Modal open={open} onClose={onClose} title="В архив">
      <div className="col gap-lg">
        <Field label="Причина"><Select value={reason} placeholder="—" onChange={(e) => setReason(e.target.value)} options={Object.entries(ARCHIVE_REASON).map(([value, label]) => ({ value, label }))} /></Field>
        {reason === 'duplicate' && <Field label="Дубль объекта №" hint="Номер объекта, который остаётся в базе"><Input inputMode="numeric" value={dup} placeholder="#123" onChange={(e) => setDup(e.target.value)} /></Field>}
        <Field label={reason === 'other' ? 'Комментарий (обязательно)' : 'Комментарий'}><Input value={comment} onChange={(e) => setComment(e.target.value)} /></Field>
        {(p.locked_fields || []).length > 0 && <div className="alert alert-info" style={{ fontSize: 13 }}>Объект проверен оператором: архивация уйдёт оператору на проверку.</div>}
        <Button block disabled={!ok} loading={m.isPending} onClick={() => m.mutate()}><Archive size={16} />{(p.locked_fields || []).length ? 'Отправить запрос' : 'В архив'}</Button>
      </div>
    </Modal>
  );
}

export function PropertyDetail({ id, compact }: { id: number; compact?: boolean }) {
  const { user, isStaff } = useAuth();
  const { data: p, isLoading, error } = useQuery({ queryKey: ['property', id], queryFn: () => api.properties.get(id) });
  const hist = useQuery({ queryKey: ['property-history', id], queryFn: () => api.properties.history(id) });
  const [edit, setEdit] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const step = useMemo(() => (p ? stepOf(p) : 0), [p]);

  if (isLoading) return <Loading />;
  if (error || !p) return <ErrorBox error={error} />;
  const mine = p.agent_id === user?.id || p.agent?.id === user?.id;
  const canWork = mine || isStaff;

  let action: React.ReactNode = null;
  if (['taken', 'call_failed'].includes(p.status)) action = canWork ? <CallStep p={p} /> : null;
  else if (p.status === 'owner_confirmed') action = canWork ? <ShootStep p={p} /> : null;
  else if (['shoot_scheduled', 'media_uploaded', 'returned'].includes(p.status)) action = <MediaStep p={p} canEdit={canWork} />;
  else if (p.status === 'review') action = isStaff ? <ReviewStep p={p} /> : <div className="alert alert-info">Отправлено оператору · фото и чек-лист закрыты. Оператор проверит и опубликует, обычно до 1 часа. Если нужно что-то переснять, объект вернётся к вам с комментарием.</div>;
  else if (p.status === 'agent_marked') action = <div className="alert alert-info">Ответил агент · объект остаётся в базе с пометкой «агент»</div>;
  else if (['published', 'rented'].includes(p.status)) action = (
    <div className="col">
      <div className="alert alert-ok">Опубликовано {fmtDateTime(p.published_at)}</div>
      <div className="row row-wrap">
        {p.post_url && <a className="btn" href={p.post_url} target="_blank" rel="noreferrer"><ExternalLink size={16} />Пост в канале</a>}
      </div>
      {isStaff && p.status === 'published' && <Republish p={p} />}
    </div>
  );
  // the media / review steps show the files with their own controls
  const mediaInAction = ['shoot_scheduled', 'media_uploaded', 'returned'].includes(p.status) || (p.status === 'review' && isStaff);
  const ownerVisible = !!(p.owner?.phone || p.owner_phone);
  const objLink = (oid: number) => (compact ? `/m/properties/${oid}` : `/objects/${oid}`);

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
              <ExclusiveConflictBadge p={p} />
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
          {p.needs_check && <NeedsCheck p={p} />}
          {action && (
            <Card label="Следующий шаг" title={STEPS[Math.min(step, STEPS.length - 1)]}>
              {action}
              {canWork && ['taken', 'call_failed', 'owner_confirmed', 'shoot_scheduled'].includes(p.status) && <OwnerOutcomes p={p} />}
            </Card>
          )}
          {!['archived', 'rented'].includes(p.status) && <Demand p={p} />}
          {p.status === 'archived' && (
            <div className="alert alert-warn">
              <span>
                В архиве{p.archived_at ? ` с ${fmtDate(p.archived_at)}` : ''} · <b>{ARCHIVE_REASON[p.archive_reason || ''] || p.archive_reason || 'причина не указана'}</b>
                {p.archive_reason === 'duplicate' && p.duplicate_of ? <> объекта <Link to={objLink(p.duplicate_of)}>#{p.duplicate_of}</Link></> : null}
                {p.archive_comment ? ` · ${p.archive_comment}` : ''}
              </span>
            </div>
          )}
          {!!p.change_requests?.length && (
            <Card label="Запрос оператору" title={`На проверке у оператора · ${p.change_requests.length}`}>
              {p.change_requests.map((r) => <RequestRow key={r.id} r={r} showObject={false} />)}
            </Card>
          )}
          <ExclusiveConflict p={p} />
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
              // one agent line: the owner's agent (stage 6.5 binding, backend deal_block); no owner binding yet → the object's agent
              ['Агент собственника', agentLabel(p.owner?.agent || p.agent)],
            ]} />
            {!ownerVisible && p.owner && <div className="muted mt" style={{ fontSize: 12 }}>Контакты видны оператору и агенту объекта</div>}
            {p.owner && !p.owner.is_agent && (isStaff ? <OwnerTransfer p={p} /> : (mine || p.owner.agent?.id === user?.id) && <OwnerTransfer p={p} request />)}
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
              ...postFieldRows(p.post_fields),
              ['Дубль объекта', p.duplicate_of ? <Link to={compact ? `/m/properties/${p.duplicate_of}` : `/objects/${p.duplicate_of}`}>#{p.duplicate_of}</Link> : '—'],
              ['Создан', fmtDateTime(p.created_at)],
              ['Взят', fmtDateTime(p.taken_at)],
              ['Съёмка', fmtDateTime(p.shoot_at)],
              ['Опубликован', fmtDateTime(p.published_at)],
              ['Проверен оператором', p.verified_at ? fmtDateTime(p.verified_at) : 'нет'],
              ...(p.archive_reason ? [['Причина архива', `${ARCHIVE_REASON[p.archive_reason] || p.archive_reason}${p.archive_comment ? ` · ${p.archive_comment}` : ''}`] as [string, React.ReactNode]] : []),
              ['Источник', p.source_url ? <a href={p.source_url} target="_blank" rel="noreferrer">открыть</a> : '—'],
            ]} />
            {p.description && <p className="muted mt" style={{ fontSize: 13 }}>{p.description}</p>}
            {canWork && !['archived', 'rented'].includes(p.status) && (
              <Button variant="ghost" size="sm" className="mt" onClick={() => setArchiving(true)}><Archive size={14} />В архив</Button>
            )}
          </Card>
          {compact && <Card label="История" title="События"><Timeline events={itemsOf(hist.data)} /></Card>}
        </div>
      </div>
      <EditModal p={p} open={edit} onClose={() => setEdit(false)} />
      <ArchiveModal p={p} open={archiving} onClose={() => setArchiving(false)} />
    </div>
  );
}
