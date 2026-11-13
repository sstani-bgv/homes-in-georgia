// Small brand component kit built on CSS variables (styles/app.css).
import { useEffect, useRef, useState, type ButtonHTMLAttributes, type ChangeEvent, type CSSProperties, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from 'react';
import { createPortal } from 'react-dom';
import { CalendarDays, ChevronDown, ChevronLeft, ChevronRight, Inbox, X } from 'lucide-react';
import { DISTRICT_LIST, DISTRICTS, districtLabel, initials, phoneBlur, phoneFocus, pluralForm } from '../lib/format';
import { mediaUrl } from '../api/client';

type BtnVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'ok';
export function Button({ variant = 'primary', size, block, loading, children, className = '', ...rest }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: BtnVariant; size?: 'sm' | 'lg'; block?: boolean; loading?: boolean }) {
  const cls = ['btn', variant !== 'primary' && `btn-${variant}`, size && `btn-${size}`, block && 'btn-block', className].filter(Boolean).join(' ');
  return (
    <button type="button" className={cls} disabled={loading || rest.disabled} {...rest}>
      {loading ? <span className="spinner" style={{ width: 16, height: 16, borderWidth: 2 }} /> : null}
      {children}
    </button>
  );
}

export function Card({ title, label, actions, children, className = '', pad = true }:
  { title?: ReactNode; label?: string; actions?: ReactNode; children?: ReactNode; className?: string; pad?: boolean }) {
  return (
    <section className={`card ${className}`}>
      {(title || label || actions) && (
        <div className="card-head">
          <div>
            {label && <div className="label">{label}</div>}
            {title && <h3>{title}</h3>}
          </div>
          {actions && <div className="row gap-sm">{actions}</div>}
        </div>
      )}
      <div className={pad ? 'card-body' : ''}>{children}</div>
    </section>
  );
}

export function Badge({ tone = 'blue', children, title }: { tone?: 'blue' | 'red' | 'redsoft' | 'ok' | 'warn' | 'grey' | 'solid'; children: ReactNode; title?: string }) {
  const cls = tone === 'blue' ? 'badge' : tone === 'solid' ? 'badge badge-blue' : `badge badge-${tone}`;
  return <span className={cls} title={title}>{children}</span>;
}

export function Stat({ label, value, sub, blue, icon }: { label: string; value: ReactNode; sub?: ReactNode; blue?: boolean; icon?: ReactNode }) {
  return (
    <div className={`card stat ${blue ? 'card-blue' : ''}`}>
      <div className="row row-between"><span className="label">{label}</span>{icon}</div>
      <div className="stat-value">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

export function Field({ label, children, className = '', hint }: { label: string; children: ReactNode; className?: string; hint?: ReactNode }) {
  return (
    <label className={`field ${className}`}>
      <span className="label">{label}</span>
      {children}
      {hint && <span className="faint" style={{ fontSize: 12 }}>{hint}</span>}
    </label>
  );
}
/** inputMode="tel" (the phoneInput fields): «+995 » appears on focus, a bare one is cleared on blur */
const MONTHS = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
const WEEK = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
const pad = (n: number) => String(n).padStart(2, '0');
const isoToday = () => { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };

/** A dropdown under (or above) a trigger: a portal at fixed coordinates, so cards / drawers around the field can't clip
 * it or paint over it; closes on an outside tap or Escape. Shared by the date picker and MultiSelect. */
function usePopover(open: boolean, setOpen: (v: boolean) => void, height = 380) {
  const [pos, setPos] = useState<{ left: number; top?: number; bottom?: number }>({ left: 0 });
  const box = useRef<HTMLButtonElement>(null), pop = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const place = () => {
      const r = box.current!.getBoundingClientRect(), below = innerHeight - r.bottom > height || r.top < height;
      setPos({ left: Math.max(8, Math.min(r.left, innerWidth - 304)), ...(below ? { top: r.bottom + 6 } : { bottom: innerHeight - r.top + 6 }) });
    };
    place();
    const out = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node) && !pop.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', out);
    document.addEventListener('keydown', esc);
    addEventListener('scroll', place, true);
    addEventListener('resize', place);
    return () => {
      document.removeEventListener('mousedown', out); document.removeEventListener('keydown', esc);
      removeEventListener('scroll', place, true); removeEventListener('resize', place);
    };
  }, [open]);
  return { pos, box, pop };
}

/** The brand date picker behind every <Input type="date">: same value (YYYY-MM-DD) and onChange(e.target.value)
 * contract as the native input, whose browser calendar can't be styled. Month + year selects for birth dates. */
function DateInput({ value, onChange, min, max, disabled, placeholder = 'Выберите дату', name, style, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  const v = typeof value === 'string' && /^\d{4}-\d\d-\d\d$/.test(value) ? value : '';
  const lo = typeof min === 'string' ? min : '', hi = typeof max === 'string' ? max : '';
  const [open, setOpen] = useState(false);
  const [view, setView] = useState('');  // YYYY-MM shown
  const { pos, box, pop } = usePopover(open, setOpen);
  const emit = (s: string) => {
    onChange?.({ target: { value: s, name }, currentTarget: { value: s, name } } as unknown as ChangeEvent<HTMLInputElement>);
    setOpen(false);
  };
  const toggle = () => {
    const t = isoToday();
    setView((v || (hi && hi < t ? hi : lo && lo > t ? lo : t)).slice(0, 7));
    setOpen(!open);
  };
  const [y, m] = view.split('-').map(Number);
  const shift = (n: number) => { const d = new Date(y, m - 1 + n, 1); setView(`${d.getFullYear()}-${pad(d.getMonth() + 1)}`); };
  const y0 = lo ? Number(lo.slice(0, 4)) : 1930, y1 = hi ? Number(hi.slice(0, 4)) : new Date().getFullYear() + 10;
  const off = (new Date(y, m - 1, 1).getDay() + 6) % 7, days = new Date(y, m, 0).getDate();
  const iso = (d: number) => `${y}-${pad(m)}-${pad(d)}`;
  const out = (s: string) => (!!lo && s < lo) || (!!hi && s > hi);
  const today = isoToday();
  const label = v ? new Date(`${v}T00:00`).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' }).replace(/\s?г\.$/, '') : '';
  return (
    <div className="dp">
      <button type="button" ref={box} className={`input dp-trigger ${v ? '' : 'dp-empty'}`} disabled={disabled} onClick={toggle} style={style}
        aria-invalid={rest['aria-invalid']} aria-haspopup="dialog" aria-expanded={open}>
        <CalendarDays size={16} /><span>{label || placeholder}</span>
      </button>
      {open && view && createPortal(
        <div className="dp-pop" role="dialog" ref={pop} style={pos}>
          <div className="dp-head">
            <button type="button" className="dp-nav" aria-label="Предыдущий месяц" onClick={() => shift(-1)}><ChevronLeft size={16} /></button>
            <select className="dp-sel" value={m} aria-label="Месяц" onChange={(e) => setView(`${y}-${pad(Number(e.target.value))}`)}>
              {MONTHS.map((n, i) => <option key={n} value={i + 1}>{n}</option>)}
            </select>
            <select className="dp-sel" value={y} aria-label="Год" onChange={(e) => setView(`${e.target.value}-${pad(m)}`)}>
              {Array.from({ length: Math.max(y1, y) - Math.min(y0, y) + 1 }, (_, i) => Math.max(y1, y) - i).map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
            <button type="button" className="dp-nav" aria-label="Следующий месяц" onClick={() => shift(1)}><ChevronRight size={16} /></button>
          </div>
          <div className="dp-grid">
            {WEEK.map((w) => <span key={w} className="dp-wd">{w}</span>)}
            {Array.from({ length: off }, (_, i) => <span key={`b${i}`} />)}
            {Array.from({ length: days }, (_, i) => {
              const s = iso(i + 1);
              return <button key={s} type="button" disabled={out(s)} onClick={() => emit(s)}
                className={`dp-day${s === v ? ' sel' : ''}${s === today ? ' today' : ''}`}>{i + 1}</button>;
            })}
          </div>
          <div className="dp-foot">
            {!out(today) && <button type="button" className="dp-link" onClick={() => emit(today)}>Сегодня</button>}
            {v && <button type="button" className="dp-link dp-clear" onClick={() => emit('')}>Очистить</button>}
          </div>
        </div>, document.body,
      )}
    </div>
  );
}

export const Input = (p: InputHTMLAttributes<HTMLInputElement>) => p.type === 'date' ? <DateInput {...p} /> : <input className="input" {...p}
  {...(p.inputMode === 'tel' ? { onFocus: (e) => { phoneFocus(e); p.onFocus?.(e); }, onBlur: (e) => { phoneBlur(e); p.onBlur?.(e); } } : {})} />;
export const Textarea = (p: TextareaHTMLAttributes<HTMLTextAreaElement>) => <textarea className="textarea" {...p} />;
export function Select({ options, placeholder, ...p }: SelectHTMLAttributes<HTMLSelectElement> & { options: { value: string | number; label: string }[]; placeholder?: string }) {
  return (
    <select className="select" {...p}>
      {placeholder !== undefined && <option value="">{placeholder}</option>}
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}
/** Several values from a list with counts (David 11-12: «4 источника и 4 района сразу»). Closed: «ss.ge, myhome.ge +2»,
 * or «3 района» when `many` gives the noun's forms (1 / 2-4 / 5+). A filter box appears on long lists. */
export function MultiSelect({ value, onChange, options, placeholder, many, label: aria, style }: {
  value: string[]; onChange: (v: string[]) => void; options: { value: string; label: string; count?: number }[];
  placeholder: string; many?: [string, string, string]; label?: string; style?: CSSProperties;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const { pos, box, pop } = usePopover(open, setOpen, 420);
  const picked = new Set(value);
  const name = (v: string) => options.find((o) => o.value === v)?.label ?? v;
  const text = !value.length ? placeholder : value.length === 1 ? name(value[0])
    : many ? `${value.length} ${many[pluralForm(value.length)]}`
    : `${value.slice(0, 2).map(name).join(', ')}${value.length > 2 ? ` +${value.length - 2}` : ''}`;
  const toggle = (v: string) => onChange(picked.has(v) ? value.filter((x) => x !== v) : [...value, v]);
  const shown = q ? options.filter((o) => o.label.toLowerCase().includes(q.toLowerCase())) : options;
  return (
    <div className="dp" style={style}>
      <button type="button" ref={box} className={`input dp-trigger ms-trigger ${value.length ? '' : 'dp-empty'}`} onClick={() => setOpen(!open)}
        aria-label={aria} aria-haspopup="listbox" aria-expanded={open}>
        <span>{text}</span><ChevronDown size={16} />
      </button>
      {open && createPortal(
        <div className="dp-pop ms-pop" ref={pop} style={pos}>
          {options.length > 10 && <input className="input ms-q" placeholder="Найти" value={q} onChange={(e) => setQ(e.target.value)} />}
          <div className="ms-list" role="listbox" aria-multiselectable="true" aria-label={aria}>
            {shown.map((o) => (
              <label key={o.value} className={`ms-opt${picked.has(o.value) ? ' sel' : ''}`}>
                <input type="checkbox" checked={picked.has(o.value)} onChange={() => toggle(o.value)} />
                <span className="ms-name">{o.label}</span>
                {o.count !== undefined && <span className="ms-count">{o.count}</span>}
              </label>
            ))}
            {!shown.length && <div className="faint" style={{ fontSize: 13, padding: 8 }}>Ничего не найдено</div>}
          </div>
          <div className="dp-foot">
            {!!value.length && <button type="button" className="dp-link dp-clear" style={{ marginLeft: 0 }} onClick={() => onChange([])}>Сбросить</button>}
            <button type="button" className="dp-link" style={{ marginLeft: 'auto' }} onClick={() => setOpen(false)}>Готово</button>
          </div>
        </div>, document.body,
      )}
    </div>
  );
}
/** The channel's districts in groups (native select: the phone's own picker, type-to-find on desktop). An old value that
 * has no tag today («Старый город») stays as the first option, so the card shows it until someone picks a new one. */
export function DistrictSelect({ value, onChange, placeholder = '—', ...p }: Omit<SelectHTMLAttributes<HTMLSelectElement>, 'value' | 'onChange'> & { value?: string | null; onChange: (v: string) => void; placeholder?: string }) {
  const v = districtLabel(value);
  const groups = [...new Set(DISTRICT_LIST.map((d) => d[2]))];
  return (
    <select className="select" value={v} onChange={(e) => onChange(e.target.value)} {...p}>
      <option value="">{placeholder}</option>
      {v && !DISTRICTS.includes(v) && <option value={v}>{v}</option>}
      {groups.map((g) => <optgroup key={g} label={g}>{DISTRICT_LIST.filter((d) => d[2] === g).map((d) => <option key={d[0]} value={d[1]}>{d[1]}</option>)}</optgroup>)}
    </select>
  );
}

export function Seg<T extends string | number>({ value, options, onChange }: { value: T; options: { value: T; label: ReactNode }[]; onChange: (v: T) => void }) {
  return (
    <div className="seg" role="radiogroup">
      {options.map((o) => (
        <button key={String(o.value)} type="button" role="radio" aria-checked={o.value === value} className={o.value === value ? 'active' : ''} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Switch({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label?: ReactNode }) {
  return (
    <label className="row" style={{ cursor: 'pointer' }}>
      <span className="switch">
        <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
        <span />
      </span>
      {label && <span style={{ fontWeight: 600 }}>{label}</span>}
    </label>
  );
}

export function Tabs<T extends string>({ value, onChange, tabs }: { value: T; onChange: (v: T) => void; tabs: { value: T; label: ReactNode; count?: number }[] }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.value} role="tab" aria-selected={t.value === value} className={t.value === value ? 'active' : ''} onClick={() => onChange(t.value)}>
          {t.label}
          {t.count !== undefined && <span className="tab-count">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function Avatar({ name, src, size }: { name?: string | null; src?: string | null; size?: 'lg' }) {
  return <span className={`avatar ${size === 'lg' ? 'avatar-lg' : ''}`}>{src ? <img src={mediaUrl(src)} alt="" /> : initials(name)}</span>;
}

export function Progress({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.min(100, Math.round((value / max) * 100)) : 0;
  return <div className={`progress ${pct >= 100 ? 'done' : ''}`}><div style={{ width: `${pct}%` }} /></div>;
}

export function Empty({ text = 'Пока пусто', icon, children }: { text?: string; icon?: ReactNode; children?: ReactNode }) {
  return <div className="empty">{icon ?? <Inbox size={32} />}<div>{text}</div>{children}</div>;
}
export function Loading() { return <div className="loading"><span className="spinner" /></div>; }
export function ErrorBox({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : 'Не удалось загрузить данные';
  return <div className="alert alert-warn">{msg}</div>;
}

export function PageHead({ label, title, actions }: { label?: string; title: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        {label && <span className="label">{label}</span>}
        <h1>{title}</h1>
      </div>
      {actions && <div className="row row-wrap">{actions}</div>}
    </div>
  );
}

/** No page scroll under an open modal/drawer. */
function useLockScroll(open: boolean) {
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = prev; };
  }, [open]);
}

function useEsc(onClose: () => void) {
  useEffect(() => {
    // Esc over a photo viewer closes the viewer only, not the modal under it
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape' && !document.querySelector('.lightbox')) onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);
}

export function Drawer({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode }) {
  useEsc(onClose);
  useLockScroll(open);
  if (!open) return null;
  return createPortal(
    <>
      <div className="overlay" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true">
        <div className="drawer-head"><h2>{title}</h2><Button variant="ghost" className="icon-btn" onClick={onClose} aria-label="Закрыть"><X size={18} /></Button></div>
        <div className="drawer-body">{children}</div>
      </aside>
    </>,
    document.body,
  );
}

export function Modal({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; wide?: boolean }) {
  useEsc(onClose);
  useLockScroll(open);
  if (!open) return null;
  return createPortal(
    <>
      <div className="overlay" onClick={onClose} />
      <div className={`modal ${wide ? 'modal-wide' : ''}`} role="dialog" aria-modal="true">
        <div className="modal-head"><h2>{title}</h2><Button variant="ghost" className="icon-btn" onClick={onClose} aria-label="Закрыть"><X size={18} /></Button></div>
        <div className="modal-body">{children}</div>
      </div>
    </>,
    document.body,
  );
}

/** Ticks every `ms` — for live SLA timers. */
export function useNow(ms = 1000): number {
  const [n, setN] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setN(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return n;
}

export function KV({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="kv">
      {items.map(([k, v]) => (
        <div key={k} style={{ display: 'contents' }}><dt>{k}</dt><dd>{v ?? '—'}</dd></div>
      ))}
    </dl>
  );
}

/** Full-screen photo / video viewer over everything (a modal too): ← → Esc. */
export function Lightbox({ items, index, onIndex, onClose }: { items: { kind?: string; room?: string | null; src: string }[]; index: number; onIndex: (i: number) => void; onClose: () => void }) {
  const go = (d: number) => onIndex((index + d + items.length) % items.length);
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); if (e.key === 'ArrowLeft') go(-1); if (e.key === 'ArrowRight') go(1); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  });
  const m = items[index];
  return createPortal(
    <>
      <div className="overlay" style={{ zIndex: 62 }} onClick={onClose} />
      <div className="lightbox" style={{ zIndex: 63 }} role="dialog" aria-modal="true" onClick={onClose}>
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
