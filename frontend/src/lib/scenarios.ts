// Molecula 90/10 deal scenarios (old CRM moleculeRows / MOLECULA_PARTICIPATE / moleculePeopleRule).
// Mirror of backend app/services/deals.py scenario_rows(): the server recomputes the real amounts, this is the preview.
export type Axis = 'make' | 'participate' | 'transfer' | 'create' | 'external';
export type Who = 'agent' | 'partner' | 'platform' | 'other';
export interface Scenario { axis: Axis; opt?: string; role?: string; lev?: number; n?: number }
export interface DistRow { who: Who; role: string; pct: number; amount?: number }

export const AXES: { v: Axis; t: string; s: string }[] = [
  { v: 'make', t: 'Делаю', s: 'свой объект и полный цикл' },
  { v: 'participate', t: 'Участвую', s: 'чужой объект или клиент' },
  { v: 'transfer', t: 'Передаю', s: 'обучение и поток сделок' },
  { v: 'create', t: 'Создаю', s: 'новое направление' },
  { v: 'external', t: 'Внешний контур', s: 'эксперимент с Verified Exclusive' },
];
export const PARTICIPATE: Record<string, [string, number, string]> = {
  object_recruit_joint: ['Объект рекрута · показ + договор', 20, 'Рекрут / участник'],
  object_recruit_sign: ['Объект рекрута · только подписание', 10, 'Рекрут / участник'],
  object_agent_joint: ['Объект агента · показ + договор', 20, 'Участник'],
  object_agent_sign: ['Объект агента · только подписание', 10, 'Участник'],
  client_joint_showing: ['Клиент · совместный показ', 20, 'Источник клиента'],
  client_transfer: ['Клиент · передача контакта', 10, 'Источник клиента'],
  client_external: ['Клиент со стороны · внешний источник', 30, 'Внешний источник'],
  take_exclusive: ['Чужой эксклюзив · полный цикл', 40, 'Куратор цикла'],
};
export const AXIS_NOTE: Record<Axis, string> = {
  make: 'Личная лестница: делаешь полный цикл сам.',
  participate: 'Участие в чужой сделке: оплачивается конкретный объём работы.',
  transfer: 'Передача способности и потока: это отдельная ось, не ступень личной лестницы.',
  create: 'Новое направление: сначала тест, потом доля создателя.',
  external: 'Экспериментальный контур. Целевая модель после подтверждения доверия: внешний агент 50% · внутренний куратор 25% · платформа 25%.',
};
export const defaultScenario = (axis: Axis): Scenario =>
  axis === 'participate' ? { axis, opt: 'object_recruit_joint' } : axis === 'transfer' ? { axis, role: 'mentor', lev: 50 } : axis === 'create' ? { axis, role: 'test', lev: 50 } : { axis };

/** The rows incl. the platform remainder. `level` is the agent's ladder level (a non-exclusive object pays 50). */
export function scenarioRows(sc: Scenario, level = 50, isExclusive = true): DistRow[] {
  const { axis, role = '' } = sc;
  const lev = sc.lev === 70 ? 70 : 50;
  const r = (who: Who, role: string, pct: number): DistRow => ({ who, role, pct });
  let rows: DistRow[];
  if (axis === 'make') rows = [r('agent', 'Агент · собственный цикл', isExclusive ? level : 50)];
  else if (axis === 'participate') {
    const d = PARTICIPATE[sc.opt || ''] || PARTICIPATE.object_recruit_joint;
    rows = [r('agent', 'Участник сделки', d[1]), r('partner', (sc.opt || '').startsWith('client') ? d[2] : 'Владелец объекта / агент', 50)];
  } else if (axis === 'transfer') {
    rows = role === 'stream' ? [r('partner', 'Передающий поток', lev === 70 ? 15 : 25), r('agent', 'Агент полного цикла', lev)]
      : role === 'keeper' ? [r('agent', 'Хранитель потока', Math.min(5, Math.max(1, Math.floor((sc.n || 0) / 100) || 1)))]
      : [r('agent', 'Наставник · полный цикл', 50)];
  } else if (axis === 'create') {
    rows = role === 'after' ? [r('partner', 'Создатель направления', lev === 70 ? 15 : 25), r('agent', 'Агент полного цикла', lev)] : [r('agent', 'Инициатор теста', 50)];
  } else rows = [r('agent', 'Внешний агент', 50), r('partner', 'Внутренний куратор', 25)];
  return [...rows, r('platform', 'Платформа', 100 - rows.reduce((s, x) => s + x.pct, 0))];
}

/** moleculePeopleRule: which participants the scenario needs. */
export function peopleRule(sc?: Scenario | null): 'agent' | 'either' | 'both' {
  if (!sc) return 'agent';
  if (sc.axis === 'participate') return (sc.opt || 'object_').startsWith('object_') ? 'both' : (sc.opt || '').startsWith('client_') ? 'either' : 'agent';
  return sc.axis === 'transfer' ? 'both' : 'agent';
}
export const PEOPLE_HINT = { both: 'Для этого сценария нужны агент и рекрут (второй участник).', either: 'Укажите агента или второго участника — достаточно одного.', agent: 'Нужен агент; второй участник необязателен.' };
