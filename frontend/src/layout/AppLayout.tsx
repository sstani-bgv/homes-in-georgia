import { useState } from 'react';
import { Link, Navigate, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  BedDouble, Building2, CalendarDays, ChartLine, Handshake, CircleUser, ClipboardList, Eye, FileText, Home, Inbox, LogOut, Menu, Plus, Search,
  Car, Settings, Trophy, UsersRound,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { ADMIN_ROLES, CARS_ONLY, CARS_ROLES, DAILY_RENT_ONLY, DAILY_RENT_ROLES, STAFF_ROLES, SUPER_ROLES, atLeast, can, type PermSection } from '../api/types';
import { api } from '../api';
import { itemsOf, IS_MOCK } from '../api/client';
import type { Role } from '../api/types';
import { useAuth, useTgBackButton } from '../auth/AuthContext';
import { useScrollMemory } from './back';
import { Avatar, Badge, Drawer, Modal, Seg } from '../components/ui';
import { ROLE_LABEL } from '../lib/format';
import { LANG, LANGS, setLang, t } from '../lib/i18n';

interface NavItem { to: string; label: string; icon: LucideIcon; roles?: Role[]; perm?: PermSection; also?: string[] }  // perm: «Права» read  // also: sibling pages of one item
const STAFF = STAFF_ROLES;
export const NAV: NavItem[] = [
  { to: '/', label: t('Главная'), icon: Home },
  { to: '/objects', label: t('Объекты'), icon: Building2 },
  { to: '/leads', label: t('Заявки'), icon: Inbox },
  { to: '/contracts', label: t('Договоры'), icon: FileText, perm: 'contracts' },  // «Права»; default: recruits too (Irakli 11-22)
  { to: '/calendar', label: t('Календарь'), icon: CalendarDays },
  // hge.ge's cars (sales are objects: «Объекты» → Продажа, Stanislav 11-15)
  { to: '/cars', label: t('Авто'), icon: Car, roles: CARS_ROLES },
  { to: '/clients', label: t('Люди'), icon: UsersRound, roles: STAFF, also: ['/owners'] },
  { to: '/team', label: t('Команда'), icon: Trophy },
  { to: '/reports', label: t('Отчёты'), icon: ClipboardList },
  { to: '/analytics', label: t('Аналитика'), icon: ChartLine, roles: SUPER_ROLES },  // hge.ge counter, superadmins only
  { to: '/daily-rent', label: t('Посуточная аренда'), icon: BedDouble, roles: DAILY_RENT_ROLES },
  { to: '/admin', label: t('Управление'), icon: Settings, roles: ADMIN_ROLES },
  { to: '/me', label: t('Кабинет'), icon: CircleUser },
];

/** «+ Добавить» sheet from the old CRM (quickAction: object, deal, contract, report, showing, check). */
const QUICK: { to: string; title: string; sub: string; icon: LucideIcon; agent?: boolean; perm?: PermSection }[] = [  // perm: «Права» create
  { to: '/objects?new=1', title: t('Объект'), sub: t('Завести вручную'), icon: Plus },
  { to: '/deals/new', title: t('Сделку'), sub: t('Расчёт и День 0'), icon: Handshake, agent: true },  // Irakli 2026-11-11: back in «Добавить»
  { to: '/contracts?new=1', title: t('Договор'), sub: t('Создать договор'), icon: FileText, perm: 'contracts' },
  { to: '/reports?kind=general', title: t('Отчёт'), sub: t('Общий отчёт'), icon: ClipboardList },
  { to: '/reports?kind=showing', title: t('Показ'), sub: t('Отчёт о показе'), icon: Eye },
  { to: '/reports?tab=check', title: t('Проверку'), sub: t('Найти дубликат'), icon: Search },
];
export function QuickAdd({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const { user } = useAuth();
  return (
    <Modal open={open} onClose={onClose} title={t('Добавить')}>
      <div className="add-grid">
        {QUICK.filter((q) => (!q.agent || atLeast(user, 'agent')) && (!q.perm || can(user, q.perm, 'c'))).map((q) => (
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
    <div className="ticker" aria-label={t('Бегущая строка')}>
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
  const loc = useLocation();
  // The Daily Rent manager sees only «Посуточная аренда» (Irakli 11-13); the backend refuses everything else too
  // «Оператор авто» likewise only «Авто» (Stanislav 11-16)
  const only = user?.role === DAILY_RENT_ONLY ? '/daily-rent' : user?.role === CARS_ONLY ? '/cars' : null;
  const items = only ? NAV.filter((n) => n.to === only)
    : NAV.filter((n) => (!n.roles || (user && n.roles.includes(user.role))) && (!n.perm || can(user, n.perm)));
  if (only && !loc.pathname.startsWith(only)) return <Navigate to={only} replace />;
  const bottom = items.slice(0, 4);
  const rest = items.slice(4);
  const logout = () => { signOut(); navigate('/login'); };
  const onSibling = (n: NavItem) => !!n.also?.some((p) => loc.pathname.startsWith(p));

  return (
    <div className="shell">
      <aside className="sidebar">
        <Link to={only || '/'} className="brand" aria-label={t('Главная')} title={t('Главная')}>
          <span className="brand-mark"><BrandMark /></span>
          <span className="brand-text">
            <div className="brand-name">Homes in Georgia</div>
            <div className="brand-sub">CRM{IS_MOCK ? ' · demo' : ''}</div>
          </span>
        </Link>
        <nav className="nav">
          {items.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === '/'} title={n.label} className={({ isActive }) => (isActive || onSibling(n) ? 'active' : '')}>
              <n.icon size={19} /><span>{n.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <LangSwitch dark />
          <div className="me-chip">
            <Avatar name={user?.name} src={user?.photo_url} />
            <div className="me-text grow">
              <div style={{ fontWeight: 700 }} className="ellipsis">{user?.name}</div>
              <div style={{ fontSize: 11, opacity: .7 }}>{user ? ROLE_LABEL[user.role] : ''}{user?.level ? ` · ${user.level}%` : ''}</div>
            </div>
            <button className="btn btn-ghost icon-btn" style={{ color: '#fff' }} onClick={logout} title={t('Выйти')} aria-label={t('Выйти')}><LogOut size={17} /></button>
          </div>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <Link to={only || '/'} aria-label={t('Главная')} title={t('Главная')} className="only-mobile brand-mark" style={{ width: 30, height: 30, borderRadius: 8, background: 'var(--brand)', display: 'grid', placeItems: 'center' }}>
            <svg width="18" height="18" viewBox="0 0 64 64"><path d="M10 34 32 14l22 20v18H40V40H24v12H10z" fill="#fff" /><rect x="42" y="8" width="12" height="7" rx="2" fill="#E3172D" /></svg>
          </Link>
          {only ? <div className="grow" style={{ fontWeight: 700 }}>{only === '/cars' ? t('Авто') : t('Посуточная аренда')}</div> : <Ticker />}
          {IS_MOCK && <Badge tone="warn">{t('demo-данные')}</Badge>}
          {!only && <button className="btn btn-sm" onClick={() => setAdd(true)} aria-label={t('Добавить')}><Plus size={16} /><span className="hide-mobile">{t('Добавить')}</span></button>}
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
      <nav className="bottom-nav">
        {bottom.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.to === '/'} className={({ isActive }) => (isActive || onSibling(n) ? 'active' : '')}><n.icon size={20} />{n.label}</NavLink>
        ))}
        <button onClick={() => setMore(true)}><Menu size={20} />{t('Ещё')}</button>
      </nav>
      <QuickAdd open={add} onClose={() => setAdd(false)} />
      <Drawer open={more} onClose={() => setMore(false)} title={t('Разделы')}>
        <div className="col">
          {rest.map((n) => (
            <NavLink key={n.to} to={n.to} onClick={() => setMore(false)} className="card card-pad row" style={{ color: 'var(--ink)', fontWeight: 700 }}>
              <n.icon size={20} color="var(--brand)" />{n.label}
            </NavLink>
          ))}
          <LangSwitch />
          <button className="btn btn-danger btn-block mt" onClick={logout}><LogOut size={16} />{t('Выйти')}</button>
        </div>
      </Drawer>
    </div>
  );
}

/** The switch between the sibling pages of one menu item (Продажа | Авто, Клиенты | Собственники). */
export function SubNav({ items }: { items: { to: string; label: string }[] }) {
  const loc = useLocation();
  const navigate = useNavigate();
  const cur = items.find((i) => loc.pathname.startsWith(i.to))?.to ?? items[0].to;
  return <Seg value={cur} options={items.map((i) => ({ value: i.to, label: i.label }))} onChange={(to) => navigate(to)} />;
}

/** RU | KA | EN — in «Ещё» on mobile and the sidebar foot on desktop; remembered in this browser. */
export function LangSwitch({ dark }: { dark?: boolean }) {
  return (
    <div className={`lang-switch${dark ? ' lang-switch-dark' : ''}`} role="group" aria-label={t('Язык')}>
      {LANGS.map((l) => (
        <button key={l.value} type="button" className={l.value === LANG ? 'active' : ''} aria-pressed={l.value === LANG}
          onClick={() => l.value !== LANG && setLang(l.value)}>{l.label}</button>
      ))}
    </div>
  );
}
