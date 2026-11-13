import { useState } from 'react';
import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Building2, CalendarDays, Handshake, CircleUser, ClipboardList, Eye, FileText, Home, Inbox, KeyRound, LogOut, Menu, Plus, Search,
  Settings, Trophy, UsersRound, ShieldCheck,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { ADMIN_ROLES, STAFF_ROLES, atLeast } from '../api/types';
import { api } from '../api';
import { itemsOf, IS_MOCK } from '../api/client';
import type { Role } from '../api/types';
import { useAuth, useTgBackButton } from '../auth/AuthContext';
import { useScrollMemory } from './back';
import { Avatar, Badge, Drawer, Modal } from '../components/ui';
import { ROLE_LABEL } from '../lib/format';

interface NavItem { to: string; label: string; icon: LucideIcon; roles?: Role[] }
const STAFF = STAFF_ROLES;
const AGENT_UP: Role[] = [...STAFF_ROLES, 'agent'];  // recruits: no contracts or deals (Irakli 11-12)
export const NAV: NavItem[] = [
  { to: '/', label: 'Главная', icon: Home },
  { to: '/objects', label: 'Объекты', icon: Building2 },
  { to: '/leads', label: 'Заявки', icon: Inbox },
  { to: '/contracts', label: 'Договоры', icon: FileText, roles: AGENT_UP },
  { to: '/calendar', label: 'Календарь', icon: CalendarDays },
  { to: '/clients', label: 'Клиенты', icon: UsersRound, roles: STAFF },
  { to: '/owners', label: 'Собственники', icon: KeyRound, roles: STAFF },
  { to: '/ndas', label: 'NDA агентов', icon: ShieldCheck, roles: STAFF },
  { to: '/team', label: 'Команда', icon: Trophy },
  { to: '/reports', label: 'Отчёты', icon: ClipboardList },
  { to: '/admin', label: 'Управление', icon: Settings, roles: ADMIN_ROLES },
  { to: '/me', label: 'Кабинет', icon: CircleUser },
];

/** «+ Добавить» sheet from the old CRM (quickAction: object, deal, contract, report, showing, check). */
const QUICK: { to: string; title: string; sub: string; icon: LucideIcon; agent?: boolean }[] = [
  { to: '/objects?new=1', title: 'Объект', sub: 'Завести вручную', icon: Plus },
  { to: '/deals/new', title: 'Сделку', sub: 'Расчёт и День 0', icon: Handshake, agent: true },  // Irakli 2026-11-11: back in «Добавить»
  { to: '/contracts?new=1', title: 'Договор', sub: 'Создать договор', icon: FileText, agent: true },
  { to: '/reports?kind=general', title: 'Отчёт', sub: 'Общий отчёт', icon: ClipboardList },
  { to: '/reports?kind=showing', title: 'Показ', sub: 'Отчёт о показе', icon: Eye },
  { to: '/reports?tab=check', title: 'Проверку', sub: 'Найти дубликат', icon: Search },
];
export function QuickAdd({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const { user } = useAuth();
  return (
    <Modal open={open} onClose={onClose} title="Добавить">
      <div className="add-grid">
        {QUICK.filter((q) => !q.agent || atLeast(user, 'agent')).map((q) => (
          <button key={q.to} type="button" className="add-item" onClick={() => { onClose(); navigate(q.to); }}>
            <q.icon size={20} /><span><b>{q.title}</b><small>{q.sub}</small></span>
          </button>
        ))}
      </div>
    </Modal>
  );
}

export function BrandMark({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden>
      <path d="M10 34 32 14l22 20v18H40V40H24v12H10z" fill="#032D6A" />
      <rect x="42" y="8" width="12" height="7" rx="2" fill="#E3172D" />
    </svg>
  );
}

function Ticker() {
  const { data } = useQuery({ queryKey: ['ticker'], queryFn: api.ticker.list, refetchInterval: 60_000 });
  const items = itemsOf(data);
  if (!items.length) return <div className="grow" />;
  return (
    <div className="ticker" aria-label="Бегущая строка">
      <div className="ticker-track">
        {[...items, ...items].map((t, i) => <span key={`${t.id}-${i}`} className="ticker-item" aria-hidden={i >= items.length}>{t.text}</span>)}
      </div>
    </div>
  );
}

export function AppLayout() {
  const { user, signOut } = useAuth();
  const navigate = useNavigate();
  useTgBackButton();
  useScrollMemory();
  const [more, setMore] = useState(false);
  const [add, setAdd] = useState(false);
  const items = NAV.filter((n) => !n.roles || (user && n.roles.includes(user.role)));
  const bottom = items.slice(0, 4);
  const rest = items.slice(4);
  const logout = () => { signOut(); navigate('/login'); };

  return (
    <div className="shell">
      <aside className="sidebar">
        <Link to="/" className="brand" aria-label="Главная" title="Главная">
          <span className="brand-mark"><BrandMark /></span>
          <span className="brand-text">
            <div className="brand-name">Homes in Georgia</div>
            <div className="brand-sub">CRM{IS_MOCK ? ' · demo' : ''}</div>
          </span>
        </Link>
        <nav className="nav">
          {items.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === '/'} title={n.label}>
              <n.icon size={19} /><span>{n.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="me-chip">
            <Avatar name={user?.name} src={user?.photo_url} />
            <div className="me-text grow">
              <div style={{ fontWeight: 700 }} className="ellipsis">{user?.name}</div>
              <div style={{ fontSize: 11, opacity: .7 }}>{user ? ROLE_LABEL[user.role] : ''}{user?.level ? ` · ${user.level}%` : ''}</div>
            </div>
            <button className="btn btn-ghost icon-btn" style={{ color: '#fff' }} onClick={logout} title="Выйти" aria-label="Выйти"><LogOut size={17} /></button>
          </div>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <Link to="/" aria-label="Главная" title="Главная" className="only-mobile brand-mark" style={{ width: 30, height: 30, borderRadius: 8, background: 'var(--brand)', display: 'grid', placeItems: 'center' }}>
            <svg width="18" height="18" viewBox="0 0 64 64"><path d="M10 34 32 14l22 20v18H40V40H24v12H10z" fill="#fff" /><rect x="42" y="8" width="12" height="7" rx="2" fill="#E3172D" /></svg>
          </Link>
          <Ticker />
          {IS_MOCK && <Badge tone="warn">demo-данные</Badge>}
          <button className="btn btn-sm" onClick={() => setAdd(true)} aria-label="Добавить"><Plus size={16} /><span className="hide-mobile">Добавить</span></button>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
      <nav className="bottom-nav">
        {bottom.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.to === '/'}><n.icon size={20} />{n.label}</NavLink>
        ))}
        <button onClick={() => setMore(true)}><Menu size={20} />Ещё</button>
      </nav>
      <QuickAdd open={add} onClose={() => setAdd(false)} />
      <Drawer open={more} onClose={() => setMore(false)} title="Разделы">
        <div className="col">
          {rest.map((n) => (
            <NavLink key={n.to} to={n.to} onClick={() => setMore(false)} className="card card-pad row" style={{ color: 'var(--ink)', fontWeight: 700 }}>
              <n.icon size={20} color="var(--brand)" />{n.label}
            </NavLink>
          ))}
          <button className="btn btn-danger btn-block mt" onClick={logout}><LogOut size={16} />Выйти</button>
        </div>
      </Drawer>
    </div>
  );
}
