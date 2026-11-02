import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { ReportKind } from '../api/types';
import { Card, PageHead, Tabs } from '../components/ui';
import { KIND_LABEL, REPORT_KINDS, ReportForm, ReportsList } from '../features/reports/ReportForms';
import { PropertyCheck } from './Tools';

export function ReportKindPicker({ value, onChange }: { value: ReportKind; onChange: (k: ReportKind) => void }) {
  return (
    <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))', marginBottom: 16 }}>
      {REPORT_KINDS.map((k) => (
        <button key={k.kind} type="button" className={`card card-pad col gap-sm ${value === k.kind ? 'card-blue' : ''}`}
          style={{ cursor: 'pointer', alignItems: 'flex-start', color: value === k.kind ? '#fff' : 'var(--brand)', padding: 14 }} onClick={() => onChange(k.kind)}>
          <k.icon size={20} />
          <span style={{ fontWeight: 800, fontSize: 13, color: value === k.kind ? '#fff' : 'var(--ink)' }}>{k.label}</span>
        </button>
      ))}
    </div>
  );
}

export function ReportsPage() {
  const [params] = useSearchParams();
  return <ReportsInner key={params.toString()} />;
}

function ReportsInner() {
  const [params] = useSearchParams();
  const qKind = params.get('kind') as ReportKind | null;
  const [tab, setTab] = useState<'new' | 'list' | 'check'>((params.get('tab') as 'new' | 'list' | 'check') || 'new');
  const [kind, setKind] = useState<ReportKind>(qKind && REPORT_KINDS.some((k) => k.kind === qKind) ? qKind : 'photo');
  const [filter, setFilter] = useState<string>('');
  return (
    <>
      <PageHead label="Работа команды" title="Отчёты" />
      <Tabs value={tab} onChange={setTab} tabs={[{ value: 'new', label: 'Новый отчёт' }, { value: 'list', label: 'Журнал' }, { value: 'check', label: 'Проверка объекта' }]} />
      {tab === 'check' ? <PropertyCheck /> : tab === 'new' ? (
        <div style={{ maxWidth: 760 }}>
          <ReportKindPicker value={kind} onChange={setKind} />
          <Card label="Отчёт" title={KIND_LABEL[kind]}><ReportForm key={kind} kind={kind} /></Card>
        </div>
      ) : (
        <>
          <select className="select" style={{ width: 'auto', marginBottom: 14 }} value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="">Все типы</option>
            {REPORT_KINDS.map((k) => <option key={k.kind} value={k.kind}>{k.label}</option>)}
          </select>
          <ReportsList kind={filter || undefined} />
        </>
      )}
    </>
  );
}
