// «Сегодня» (services/daily_rent_auto.today): the manager's start screen — arrivals and departures, who lives now, stays
// without money (Airbnb reservations become stays by themselves, the sum comes later), today's cleanings, the payouts
// ready, the next 30 days' occupancy and what needs attention. The same text goes to Telegram every morning at 09:00.
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Badge, Card, Empty, ErrorBox, Loading, PageHead } from '../../components/ui';
import { BookingModal } from './Booking';
import { dm, dr, mix, nightsLabel, type TodayStay } from './api';

function StayRow({ s, open, when }: { s: TodayStay; open: (id: number) => void; when?: string }) {
  return (
    <div className="row row-between row-wrap dr-payout-row clickable" onClick={() => open(s.id)}>
      <span><b>{s.apartment}</b> · {s.guest || (s.code ? `гость ${s.platform} ${s.code}` : `гость ${s.platform}`)}
        <small className="muted"> · {when ?? `${dm(s.check_in)} – ${dm(s.check_out)}, ${nightsLabel(s.nights)}`}</small></span>
      <span className="row gap-sm">
        {s.auto && <Badge tone="grey" title="Создано автоматически из брони Airbnb">авто</Badge>}
        {s.no_money && <Badge tone="warn">без суммы</Badge>}
        {s.cancelled_on_platform && <Badge tone="red">отменена на Airbnb</Badge>}
        <Badge tone="grey">{s.platform}</Badge>
      </span>
    </div>
  );
}

function List({ title, rows, open, empty, when }: { title: string; rows: TodayStay[]; open: (id: number) => void; empty: string; when?: (s: TodayStay) => string }) {
  return (
    <Card title={`${title}${rows.length ? ` · ${rows.length}` : ''}`}>
      {rows.length === 0 ? <Empty text={empty} /> : (
        <div className="col gap-sm">{rows.map((s) => <StayRow key={s.id} s={s} open={open} when={when?.(s)} />)}</div>
      )}
    </Card>
  );
}

export function TodayPage() {
  const q = useQuery({ queryKey: ['dr', 'today'], queryFn: dr.today, refetchInterval: 60_000 });
  const [stay, setStay] = useState<number | null>(null);
  const nav = useNavigate();
  if (q.isLoading) return <Loading />;
  if (!q.data) return <ErrorBox error={q.error} />;
  const v = q.data;
  const d = new Date(v.date + 'T12:00:00').toLocaleDateString('ru-RU', { weekday: 'long', day: 'numeric', month: 'long' });
  return (
    <>
      <PageHead label="Посуточная аренда" title="Сегодня" actions={<span className="muted">{d}</span>} />
      {v.alerts.length > 0 && (
        <Card title="Обратите внимание">
          <div className="col gap-sm">{v.alerts.map((a, i) => (
            <div key={i} className="row gap-sm clickable" onClick={() => nav(a.kind === 'gap' ? '/daily-rent/calendar' : `/daily-rent/apartments/${a.apartment_id}`)}>
              <Badge tone={a.kind === 'gap' ? 'grey' : 'warn'}>{a.kind === 'gap' ? 'свободно' : a.kind === 'hidden_booked' ? 'скрыта' : 'отмена'}</Badge>
              <span>{a.text}</span>
            </div>
          ))}</div>
        </Card>
      )}
      <div className="grid g2">
        <List title="Заезжают сегодня" rows={v.arrivals} open={setStay} empty="Сегодня никто не заезжает" />
        <List title="Выезжают сегодня" rows={v.departures} open={setStay} empty="Сегодня никто не выезжает" />
      </div>
      <List title="Без суммы — внесите деньги" rows={v.no_money} open={setStay} empty="У всех проживаний есть сумма ✓" />
      <div className="grid g2">
        <List title="Живут сейчас" rows={v.living} open={setStay} empty="Сейчас никто не живёт" when={(s) => `до ${dm(s.check_out)}`} />
        <List title="Заезды на неделе" rows={v.upcoming} open={setStay} empty="На ближайшую неделю заездов нет" when={(s) => `заезд ${dm(s.check_in)}, ${nightsLabel(s.nights)}`} />
      </div>
      <div className="grid g2">
        <Card title="Уборки сегодня">
          {v.cleanings.length === 0 ? <Empty text="Уборок нет" /> : (
            <div className="col gap-sm">{v.cleanings.map((c) => (
              <div key={c.id} className="row row-between row-wrap clickable" onClick={() => nav('/daily-rent/cleaning')}>
                <span><b>{c.apartment}</b> · {c.cleaner || <span className="muted">уборщик не назначен</span>}<small className="muted"> · {c.notes}</small></span>
                <Badge tone={c.done ? 'ok' : 'warn'}>{c.done ? 'готово' : 'ждёт'}</Badge>
              </div>
            ))}</div>
          )}
        </Card>
        <Card title="Загрузка на 30 дней">
          <div className="col gap-sm">
            {v.load.map((l) => (
              <div key={l.apartment_id} className="row row-between clickable" onClick={() => nav(`/daily-rent/apartments/${l.apartment_id}`)}>
                <span>{l.apartment}</span><b>{l.occupancy}%<small className="muted"> · {l.busy} ноч.</small></b>
              </div>
            ))}
            {Object.keys(v.payouts_ready).length > 0 && (
              <div className="row row-between mt clickable" onClick={() => nav('/daily-rent/summary')}>
                <span>Можно выплатить собственникам</span><b>{mix(v.payouts_ready)}</b>
              </div>
            )}
          </div>
        </Card>
      </div>
      <BookingModal open={stay} onClose={() => { setStay(null); q.refetch(); }} />
    </>
  );
}
