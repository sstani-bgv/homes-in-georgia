import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { BookOpen, CalendarClock, CalendarDays, FileText } from 'lucide-react';
import { api } from '../api';
import { useAuth } from '../auth/AuthContext';
import { PropertyStatusBadge } from '../components/domain';
import { Avatar, Badge, Card, Empty, ErrorBox, Loading, PageHead, Progress } from '../components/ui';
import { ROLE_LABEL, fmtDate, fmtSlot, money, monthLabel } from '../lib/format';
import { DealStatusBadge } from './Deals';
import { LevelProgress } from './Team';

const CNT: [string, string][] = [['photo', 'Фото'], ['showing', 'Показы'], ['stickers', 'Расклейка'], ['rental', 'Сдачи']];

export function CabinetView({ base = '' }: { base?: string }) {
  const { user } = useAuth();
  const { data, isLoading, error } = useQuery({ queryKey: ['cabinet'], queryFn: api.cabinet });
  if (isLoading) return <Loading />;
  if (error || !data) return <ErrorBox error={error} />;
  const u = data.user || user;
  const lvl = data.level;
  const leadLink = (id: number) => (base ? `${base}/leads/${id}` : `/leads/${id}`);
  const propLink = (id: number) => (base ? `${base}/properties/${id}` : `/objects/${id}`);
  return (
    <div className="col gap-lg">
      <div className="card card-blue card-pad">
        <div className="row">
          <Avatar size="lg" name={u?.name} src={u?.photo_url} />
          <div className="grow">
            <div className="label">{u ? ROLE_LABEL[u.role] : ''}</div>
            <h2 style={{ color: '#fff', fontSize: 22, fontWeight: 900 }}>{u?.name}</h2>
          </div>
          {lvl && <div style={{ textAlign: 'right' }}><div className="stat-value" style={{ marginTop: 0 }}>{lvl.level}%</div><div className="label">уровень</div></div>}
        </div>
        {lvl && <div className="mt-lg"><LevelProgress deals={lvl.exclusive_deals} level={lvl.level} /><div className="label mt">{lvl.exclusive_deals} эксклюзивных сделок</div></div>}
      </div>

      {data.counters && (
        <Card label={data.month ? monthLabel(data.month) : 'Этот месяц'} title="Мои счётчики">
          <div className="grid g4">
            {CNT.map(([k, label]) => {
              const c = data.counters?.[k as 'photo'];
              if (!c) return null;
              return (
                <div key={k} className="col gap-sm">
                  <div className="row row-between"><span className="label">{label}</span><b className="num">{c.done}/{c.plan}</b></div>
                  <Progress value={c.done} max={c.plan} />
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {(() => {
        const ch = (data as unknown as { channel_listings?: { active?: number; last_7_days?: number } }).channel_listings;
        const reps = ((data as unknown as { reports?: { id: number; kind: string; date?: string | null; created_at?: string }[] }).reports || []);
        return (
          <div className="grid g2">
            <Card label="Инструменты" title="Быстрый доступ">
              <div className="add-grid">
                <Link to={`${base}/contracts`} className="add-item"><FileText size={20} /><span><b>Договор</b><small>Аренда или продажа</small></span></Link>
                <Link to={`${base}/calendar`} className="add-item"><CalendarDays size={20} /><span><b>Календарь</b><small>Показы и съёмки</small></span></Link>
                {!base && <Link to="/team?tab=standards" className="add-item"><BookOpen size={20} /><span><b>Стандарты</b><small>Лестница и викторина</small></span></Link>}
              </div>
            </Card>
            <Card label="Канал" title="Мои публикации">
              {ch ? (
                <div className="grid g2">
                  <div><div className="label">Активных</div><div className="stat-value">{ch.active ?? 0}</div></div>
                  <div><div className="label">За 7 дней</div><div className="stat-value">{ch.last_7_days ?? 0}</div></div>
                </div>
              ) : <div className="muted">Нет данных</div>}
              {reps.length > 0 && <div className="muted mt" style={{ fontSize: 12 }}>Отчётов с моим участием: {reps.length}, последний {fmtDate(reps[0].date || reps[0].created_at)}</div>}
            </Card>
          </div>
        );
      })()}

      <div className="grid g2">
        <Card label="Ближайшие" title="Просмотры">
          {(data.viewings || []).length === 0 ? <Empty text="Просмотров не запланировано" icon={<CalendarClock size={28} />} /> : (data.viewings || []).map((l) => (
            <Link key={l.id} to={leadLink(l.id)} className="list-row" style={{ color: 'inherit', textDecoration: 'none' }}>
              <Badge tone="solid">{l.viewing_at ? fmtSlot(l.viewing_at) : '—'}</Badge>
              <span className="grow ellipsis" style={{ fontWeight: 600 }}>{l.listing?.title}</span>
            </Link>
          ))}
        </Card>
        <Card label="Договоры" title="Скоро заканчиваются">
          {(data.expiring || []).length === 0 ? <Empty text="Нет договоров на продление" /> : (data.expiring || []).map((d) => (
            <div key={d.id} className="list-row"><span className="grow"><b>{d.address}</b><div className="muted" style={{ fontSize: 12 }}>до {fmtDate(d.rent_to)}</div></span><Badge tone="warn">{d.client_name}</Badge></div>
          ))}
        </Card>
      </div>

      <div className="grid g2">
        <Card title="Мои объекты">
          {(data.properties || []).length === 0 ? <Empty text="Объектов нет" /> : (data.properties || []).slice(0, 8).map((p) => (
            <Link key={p.id} to={propLink(p.id)} className="list-row" style={{ color: 'inherit', textDecoration: 'none' }}>
              <span className="grow ellipsis"><b>{p.address}</b> <span className="muted">{p.district}</span></span><PropertyStatusBadge status={p.status} />
            </Link>
          ))}
        </Card>
        <Card label="Сделки" title="Мои сделки">
          {(data.deals || []).length === 0 ? <Empty text="Сделок нет" /> : (data.deals || []).slice(0, 8).map((d) => (
            <div key={d.id} className="list-row">
              <span className="grow ellipsis"><b>{d.address || `#${d.id}`}</b><div className="muted" style={{ fontSize: 12 }}>{fmtDate(d.created_at)} · моя доля {money(d.agent_amount)}</div></span>
              <DealStatusBadge s={d.status} />
            </div>
          ))}
        </Card>
      </div>
    </div>
  );
}

export function CabinetPage() {
  return (<><PageHead label="Личный кабинет" title="Кабинет" /><CabinetView /></>);
}
