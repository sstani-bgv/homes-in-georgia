// Small brand component kit built on CSS variables (styles/app.css).
import { useEffect, useState, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from 'react';
import { createPortal } from 'react-dom';
import { Inbox, X } from 'lucide-react';
import { initials } from '../lib/format';

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
export const Input = (p: InputHTMLAttributes<HTMLInputElement>) => <input className="input" {...p} />;
export const Textarea = (p: TextareaHTMLAttributes<HTMLTextAreaElement>) => <textarea className="textarea" {...p} />;
export function Select({ options, placeholder, ...p }: SelectHTMLAttributes<HTMLSelectElement> & { options: { value: string | number; label: string }[]; placeholder?: string }) {
  return (
    <select className="select" {...p}>
      {placeholder !== undefined && <option value="">{placeholder}</option>}
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
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
  return <span className={`avatar ${size === 'lg' ? 'avatar-lg' : ''}`}>{src ? <img src={src} alt="" /> : initials(name)}</span>;
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
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
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
