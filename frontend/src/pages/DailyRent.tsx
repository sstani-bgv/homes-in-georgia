import { useQuery } from '@tanstack/react-query';
import { ExternalLink, RefreshCw } from 'lucide-react';
import { api } from '../api';
import { ApiError } from '../api/client';
import { Button, Empty, Loading, PageHead } from '../components/ui';

/** «Посуточная аренда» (Irakli 11-13): the Daily Rent app (ApartFlow, its own server and database) inside the CRM.
 * The CRM only lets superadmin / admin / the Daily Rent manager in and hands the frame a short-lived entry token;
 * apartments, stays, expenses and owner payouts are managed in the app itself, exactly as in the Telegram bot. */
export function DailyRentPage() {
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ['daily-rent', 'session'], queryFn: api.dailyRent.session,
    staleTime: 10 * 3600_000, refetchOnWindowFocus: false, retry: false,
  });
  const notReady = error instanceof ApiError && error.status === 503;
  return (
    <div className="col gap">
      <PageHead label="Daily Rent" title="Посуточная аренда" actions={data && (
        <div className="row gap-sm">
          <Button variant="secondary" size="sm" loading={isFetching} onClick={() => refetch()}><RefreshCw size={15} />Обновить</Button>
          <a className="btn btn-secondary btn-sm" href={data.url} target="_blank" rel="noreferrer"><ExternalLink size={15} />Отдельно</a>
        </div>
      )} />
      {isLoading && <Loading />}
      {error && <Empty text={notReady ? 'Посуточная аренда ещё не подключена к этой CRM' : 'Не удалось открыть посуточную аренду'} />}
      {data && (
        <iframe key={data.url} className="dr-frame" src={data.url} title="Посуточная аренда"
          allow="clipboard-read; clipboard-write" referrerPolicy="no-referrer" />
      )}
    </div>
  );
}
