// Owner autocomplete under the owner name / phone inputs: the agent starts typing a name or a number and picks the owner
// from the base (old Owner_Base contacts included) instead of retyping him; the pick links the object to that owner.
import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api';
import { itemsOf } from '../api/client';
import type { Owner } from '../api/types';

export function OwnerSuggest({ query, onPick }: { query: string; onPick: (o: Owner) => void }) {
  const [q, setQ] = useState('');
  const [hidden, setHidden] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => { setQ(query.trim()); setHidden(false); }, 300);
    return () => clearTimeout(t);
  }, [query]);
  const { data } = useQuery({ queryKey: ['owners', 'suggest', q], queryFn: () => api.owners.list(q, 'all'), enabled: q.length >= 2 });
  const items = itemsOf(data).filter((o) => !o.is_agent).slice(0, 5);
  if (hidden || q.length < 2 || !items.length) return null;
  return (
    <div className="card col" style={{ gap: 0, marginTop: 4, padding: 4 }}>
      <span className="faint" style={{ fontSize: 11, padding: '2px 8px' }}>Есть в базе собственников:</span>
      {items.map((o) => (
        <button key={o.id} type="button" className="list-row" style={{ textAlign: 'left', background: 'none', border: 0, cursor: 'pointer', padding: '6px 8px' }}
          onClick={() => { onPick(o); setHidden(true); }}>
          <span className="grow"><b>{o.name || '—'}</b> <span className="muted num">{o.phone || 'телефон скрыт'}</span>
            {o.address && <div className="faint" style={{ fontSize: 12 }}>{o.address}</div>}</span>
        </button>
      ))}
    </div>
  );
}
