// Old CRM «Объекты» tab: current publications from the channels with hashtag filter and search
// (GET /api/channel-posts).
import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ExternalLink, Hash, Search } from 'lucide-react';
import { api } from '../../api';
import { itemsOf } from '../../api/client';
import type { ChannelPost } from '../../api/types';
import { Badge, Empty, ErrorBox, Input, Loading, Seg } from '../../components/ui';
import { fmtDate, money } from '../../lib/format';

function useDebounced<T>(v: T, ms = 350): T {
  const [d, setD] = useState(v);
  useEffect(() => { const t = setTimeout(() => setD(v), ms); return () => clearTimeout(t); }, [v, ms]);
  return d;
}

export function ChannelFeed() {
  const [q, setQ] = useState('');
  const [tag, setTag] = useState('');
  const [status, setStatus] = useState('active');
  const [source, setSource] = useState('');
  const dq = useDebounced(q);
  const dtag = useDebounced(tag);
  const { data, isLoading, error } = useQuery({
    queryKey: ['channel-posts', dq, dtag, status, source],
    queryFn: () => api.channelPosts({ q: dq || undefined, hashtag: dtag || undefined, status: status || undefined, source: source || undefined }),
  });
  const items = itemsOf(data);
  const total = (data && !Array.isArray(data) && typeof data.total === 'number') ? data.total : items.length;
  return (
    <div className="col gap-lg">
      <div className="form-grid">
        <div className="input-icon"><Search size={16} /><Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Адрес, район, агент, текст" aria-label="Поиск по публикациям" /></div>
        <div className="input-icon"><Hash size={16} /><Input value={tag} onChange={(e) => setTag(e.target.value)} placeholder="Хэштеги через пробел: Vake 2Bed" aria-label="Хэштеги" /></div>
      </div>
      <div className="row row-wrap row-between">
        <Seg value={status} onChange={setStatus} options={[{ value: 'active', label: 'Активные' }, { value: 'rented', label: 'Сданы' }, { value: 'archived', label: 'Архив' }, { value: '', label: 'Все' }]} />
        <Seg value={source} onChange={setSource} options={[{ value: '', label: 'Все источники' }, { value: 'channel', label: 'Канал' }, { value: 'topic', label: 'Рабочая группа' }]} />
      </div>
      {isLoading ? <Loading /> : error ? <ErrorBox error={error} /> : items.length === 0 ? <Empty text="По этому запросу ничего не найдено" /> : (
        <>
          <div className="muted" style={{ fontSize: 12 }}>Найдено: {total}{total > items.length ? `, показаны первые ${items.length}` : ''}</div>
          <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}>
            {items.map((p) => <PostCard key={p.id} p={p} />)}
          </div>
        </>
      )}
    </div>
  );
}

function PostCard({ p }: { p: ChannelPost }) {
  const title = [p.district, p.rooms, p.building].filter(Boolean).join(' · ') || `Публикация #${p.message_id ?? p.id}`;
  const text = (p.raw || '').replace(/\s+/g, ' ').trim();
  return (
    <article className="card card-pad col gap-sm">
      <div className="row row-between" style={{ alignItems: 'flex-start' }}>
        <b className="grow" style={{ fontSize: 14 }}>{title}</b>
        {p.price ? <span className="price nowrap" style={{ fontSize: 16 }}>{money(p.price)}</span> : null}
      </div>
      <div className="row row-wrap gap-sm">
        {p.is_exclusive && <Badge tone="solid">эксклюзив</Badge>}
        {p.status && p.status !== 'active' && <Badge tone="grey">{p.status === 'rented' ? 'сдан' : 'архив'}</Badge>}
        {p.agent_name && <Badge>{p.agent_name}</Badge>}
      </div>
      {text && <p className="muted" style={{ fontSize: 12, display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{text}</p>}
      <div className="row row-between" style={{ marginTop: 'auto' }}>
        <span className="faint" style={{ fontSize: 12 }}>{fmtDate(p.date)}</span>
        {p.link && <a href={p.link} target="_blank" rel="noopener noreferrer" className="row gap-sm" style={{ fontSize: 12, fontWeight: 700 }}>Открыть<ExternalLink size={13} /></a>}
      </div>
    </article>
  );
}
