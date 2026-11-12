// Typed API functions, straight from docs/ARCHITECTURE.md §7.
import { fetchBlob, http, uploadWithProgress } from './client';
import {
  normAgentStat, normAuth, normBoard, normCabinet, normClient, normCounter, normDashboard, normEvent, normLead, normListing, normPage,
  normProperty, normRecommend, normReport,
} from './normalize';
import type {
  Birthday, Invite, InviteInfo,
  AgentStat, AppConfig, AuthOut, Cabinet, Client, Contract, ContractCloseReason, ContractDraftIn, ContractIn, CounterRow, Dashboard, Deal, DealCalcIn, DealCalcOut,
  EventItem, Evaluation, Lead, LeadContract, LeadStatus, DealPrefill, Listing, NewsItem, Owner, Page, Property, RecommendedAgent, Report, ReportKind,
  Settings, Source, SourceSchedule, TickerMessage, UserbotStatus, User, ListingsStats, Elsewhere, PostDraft, ChangeRequest, DealPick, Demand, GeoHit,
} from './types';

type L<T> = Page<T> | T[];
const LIMIT = 200;
const then = <A, B>(p: Promise<A>, f: (a: A) => B) => p.then(f);

export const api = {
  config: () => http.get<AppConfig>('/config'),
  geo: {
    search: (q: string) => http.get<{ items: GeoHit[]; point: boolean; error?: boolean }>('/geo/search', { q }),
    point: (p: { lat: number; lng: number }) => http.get<{ district: string | null; metro: string | null; metro_m: number | null }>('/geo/point', p),
  },

  auth: {
    login: (login: string, password: string) => then(http.post<AuthOut>('/auth/login', { login, password }), normAuth),
    telegramWebApp: (init_data: string) => then(http.post<AuthOut>('/auth/telegram-webapp', { init_data }), normAuth),
    telegramLogin: (data: Record<string, unknown>) => then(http.post<AuthOut>('/auth/telegram-login', data), normAuth),
    inviteInfo: (token: string) => http.get<InviteInfo>(`/auth/invite/${encodeURIComponent(token)}`),
    acceptInvite: (token: string, data: Record<string, unknown>) => then(http.post<AuthOut>('/auth/invite', { ...data, token }), normAuth),
    me: () => http.get<User>('/auth/me'),
    /** the user's own profile (cabinet «Заполните профиль») */
    updateMe: (b: Pick<Partial<User>, 'name' | 'birth_date' | 'phone' | 'email'>) => http.patch<User>('/auth/me', b),
  },

  users: {
    list: (q?: { role?: string; active?: boolean | string }) => http.get<L<User>>('/users', q),
    create: (b: Partial<User> & { password?: string }) => http.post<User>('/users', b),
    update: (id: number, b: Partial<User> & { password?: string }) => http.patch<User>(`/users/${id}`, b),
    promote: (id: number) => http.post<User>(`/users/${id}/promote`),
    stats: (id: number) => http.get<Record<string, unknown>>(`/users/${id}/stats`),
    /** #Zaza, or #ArturB when another agent has that first name (without '#') */
    suggestTag: (name: string, uid?: number) => http.get<{ tag: string }>('/users/agent-tag-suggestion', { name, uid }),
  },

  invites: {
    list: () => http.get<{ items: Invite[] }>('/invites'),
    create: (b: Pick<Invite, 'role' | 'level' | 'agent_tag' | 'districts' | 'works_as_agent' | 'note'>) => http.post<Invite>('/invites', b),
    revoke: (id: number) => http.post<Invite>(`/invites/${id}/revoke`),
  },
  birthdays: () => http.get<{ items: Birthday[] }>('/birthdays'),

  /** staff: any agent's cabinet (user_id); the backend refuses another user's for everyone else */
  cabinet: (userId?: number) => then(http.get<Cabinet>('/me/cabinet', { user_id: userId }), normCabinet),

  listings: {
    new: (q?: ListingFilter, limit = 50) => then(http.get<L<Listing> & { known_total?: number }>('/listings/new', { ...q, limit }),
      (d) => ({ ...normPage(d, normListing), known_total: Array.isArray(d) ? 0 : d.known_total ?? 0 })),
    /** «Тот же объект» (same) / «Другой» on a «Похож на объект #N» card */
    decideMatch: (id: string, pid: number, same: boolean) => http.post<{ ok: boolean; status: string }>(`/listings/${id}/matches/${pid}`, { same }),
    dismiss: (id: string) => http.post<{ ok: boolean }>(`/listings/${id}/dismiss`),
    agent: (id: string, body: { owner_name?: string; owner_phone?: string }) => http.post<Property>(`/listings/${id}/agent`, body),
    facets: () => http.get<Record<'sources' | 'districts' | 'metros', { value: string; count: number }[]>>('/listings/new/facets'),
    get: (id: string) => then(http.get<Listing>(`/listings/${id}`), normListing),
  },

  properties: {
    list: (q?: { status?: string; agent_id?: number; q?: string; mine?: number; base?: number; rented?: number; limit?: number; offset?: number }) => then(http.get<L<Property>>('/properties', { limit: LIMIT, ...q }), (d) => normPage(d, normProperty)),
    counts: () => http.get<Record<string, number>>('/properties/counts'),
    board: () => then(http.get<Record<string, Property[]>>('/properties/board'), (d) => normBoard(d, normProperty)),
    boardHide: (status: 'published' | 'rented') => http.post<{ hidden: number }>('/properties/board/hide', { status }),
    take: (listing_id: string) => http.post<Property>('/properties', { listing_id }),
    create: (b: Partial<Property> & { owner_name?: string }) => http.post<Property>('/properties', b),
    get: (id: number) => then(http.get<Property>(`/properties/${id}`), normProperty),
    update: (id: number, b: Partial<Property> & { owner_name?: string; owner_phone?: string; reason?: string }) => http.patch<Property>(`/properties/${id}`, b),
    call: (id: number, reached: boolean) => http.post<Property>(`/properties/${id}/call`, { reached }),
    owner: (id: number, b: { is_owner: boolean; owner_name?: string; owner_phone?: string }) =>
      http.post<Property>(`/properties/${id}/owner`, b),
    shoot: (id: number, shoot_at: string) =>
      http.post<Property & { dup_check?: 'added' | 'exists' }>(`/properties/${id}/shoot`, { shoot_at }),
    uploadMedia: (id: number, files: File[], room?: string) => {
      const fd = new FormData();
      files.forEach((f) => fd.append('files', f));
      if (room) fd.append('room', room);
      return http.post<Property>(`/properties/${id}/media`, fd);
    },
    uploadContract: (id: number, file: File) => {
      const fd = new FormData();
      fd.append('file', file);
      return http.post<Property>(`/properties/${id}/exclusive-contract`, fd);
    },
    uploadOwnerDoc: (id: number, kind: 'owner_id_doc' | 'registry_extract', file: File) => {
      const fd = new FormData();
      fd.append('file', file);
      return http.post<Property>(`/properties/${id}/owner-docs/${kind}`, fd);
    },
    uploadMediaProgress: (id: number, file: File, onProgress: (f: number) => void) => {
      const fd = new FormData();
      fd.append('files', file);
      return uploadWithProgress<Property & { uploaded_ids?: number[] }>(`/properties/${id}/media`, fd, onProgress);
    },
    mediaOrder: (id: number, ids: number[]) => http.put<Property>(`/properties/${id}/media/order`, { ids }),
    deleteMedia: (id: number, mid: number) => http.del<unknown>(`/properties/${id}/media/${mid}`),
    mediaRoom: (id: number, mid: number, room: string | null) => http.patch<{ id: number; room: string | null }>(`/properties/${id}/media/${mid}`, { room }),
    checklist: (id: number, checklist: Record<string, boolean>) => http.post<Property>(`/properties/${id}/checklist`, { checklist }),
    submit: (id: number) => http.post<Property>(`/properties/${id}/submit`),
    review: (id: number, approve: boolean, comment?: string) => http.post<Property>(`/properties/${id}/review`, { approve, comment }),
    post: (id: number) => http.get<PostDraft>(`/properties/${id}/post`),
    publish: (id: number, b?: { text?: string; media_ids?: number[]; channel?: string; comment?: string }) => then(http.post<Property>(`/properties/${id}/publish`, b), normProperty),
    /** reason: ARCHIVE_REASON key; a verified object's agent gets change_request back instead */
    archive: (id: number, b: { reason: string; comment?: string; duplicate_of?: number | null }) => http.post<Property>(`/properties/${id}/archive`, b),
    /** agent's «Запрос оператору»: move the owner to another agent (staff: moved right away, change_request null) */
    transferRequest: (id: number, agent_id: number, reason?: string) => http.post<{ change_request: ChangeRequest | null }>(`/properties/${id}/transfer-request`, { agent_id, reason }),
    elsewhere: (id: number) => http.get<{ items: Elsewhere[] }>(`/properties/${id}/elsewhere`),
    adRemoved: (id: number, listingId: string) => then(http.post<Property>(`/properties/${id}/listings/${listingId}/removed`), normProperty),
    refused: (id: number, comment?: string) => http.post<Property>(`/properties/${id}/refused`, { comment }),
    thinking: (id: number, until: string, comment?: string) => http.post<Property>(`/properties/${id}/thinking`, { until, comment }),
    resume: (id: number) => http.post<Property>(`/properties/${id}/resume`),
    rentedSelf: (id: number, comment?: string) => http.post<Property>(`/properties/${id}/rented-self`, { comment }),
    history: (id: number) => then(http.get<L<EventItem>>(`/properties/${id}/history`), (d) => normPage(d, normEvent)),
    dealSearch: (q: { q?: string; pid?: number; deal_type?: string; lead_id?: number }) => http.get<{ items: DealPick[] }>('/properties/deal-search', q),
    verify: (id: number) => then(http.post<Property>(`/properties/${id}/verify`), normProperty),
    demand: (id: number) => http.get<Demand>(`/properties/${id}/demand`),
    fastTrack: (id: number) => http.post<Property & { sent: number }>(`/properties/${id}/fast-track`),
    checkDuplicate: (b: { phone?: string; address?: string; url?: string; cadastral?: string }) =>
      http.post<{ status?: string; matches?: unknown[]; [k: string]: unknown }>('/properties/check-duplicate', b),
  },

  changeRequests: {
    list: (status = 'pending') => http.get<Page<ChangeRequest>>('/change-requests', { status }),
    decide: (id: number, approve: boolean, comment?: string) => http.post<ChangeRequest>(`/change-requests/${id}/decide`, { approve, comment }),
  },

  owners: {
    list: (q?: string, scope?: 'base' | 'unlinked' | 'agents' | 'all') => http.get<L<Owner>>('/owners', { q, scope }),
    create: (b: Partial<Owner>) => http.post<Owner>('/owners', b),
    update: (id: number, b: Partial<Owner>) => http.patch<Owner>(`/owners/${id}`, b),
    /** «Передать собственника агенту…» (staff): the owner and his live objects move to the agent */
    transfer: (id: number, agent_id: number, reason?: string) => http.post<Owner>(`/owners/${id}/transfer`, { agent_id, reason }),
  },

  leads: {
    list: (q?: { status?: string; agent_id?: number; operator_id?: number; q?: string; archived?: boolean }) => then(http.get<L<Lead>>('/leads', { ...q, limit: LIMIT }), (d) => normPage(d, normLead)),
    board: () => then(http.get<Record<string, Lead[]>>('/leads/board'), (d) => normBoard(d, normLead)),
    get: (id: number) => then(http.get<Lead>(`/leads/${id}`), normLead),
    take: (id: number) => http.post<Lead>(`/leads/${id}/take`),
    recommend: (id: number) => then(http.get<L<RecommendedAgent>>(`/leads/${id}/recommend-agents`), (d) => normPage(d, normRecommend)),
    assign: (id: number, agent_id: number) => http.post<Lead>(`/leads/${id}/assign`, { agent_id }),
    slots: (id: number, starts: string[]) => http.post<Lead>(`/leads/${id}/slots`, { starts }),
    reschedule: (id: number) => http.post<Lead>(`/leads/${id}/reschedule`),
    outcome: (id: number, outcome: string, reason?: string) => http.post<Lead>(`/leads/${id}/outcome`, { outcome, reason }),
    cancel: (id: number, reason: string) => http.post<Lead>(`/leads/${id}/cancel`, { reason }),
    archive: (id: number, archived = true) => http.post<Lead>(`/leads/${id}/archive`, { archived }),
    contract: (id: number) => http.get<LeadContract>(`/leads/${id}/contract`),
    counts: () => http.get<Partial<Record<LeadStatus, number>>>('/leads/counts'),
    startContract: (id: number) => http.post<Lead>(`/leads/${id}/start-contract`),
    dealPrefill: (id: number) => http.get<DealPrefill>(`/leads/${id}/deal-prefill`),
  },

  clients: {
    list: (q?: string) => http.get<L<Client>>('/clients', { q }),
    get: (id: number) => then(http.get<Client>(`/clients/${id}`), normClient),
  },

  /** Mini App: the generated contract file sent to the user's Telegram chat by the bot (webviews block downloads) */
  contracts: {
    send: (filename: string, html: string) => http.post<{ ok: boolean }>('/contracts/send', { filename, html }),
    /** status: active (draft + signed, default) | draft | signed | deal | closed_no_deal | all */
    list: (q?: { status?: string; lead_id?: number; deal_id?: number; agent_id?: number; q?: string }) => http.get<L<Contract>>('/contracts', { ...q, limit: LIMIT }),
    /** the wizard's debounced autosave: the deal's draft (no PDF), or `id`'s data */
    autosave: (b: ContractDraftIn) => http.post<Contract>('/contracts/draft', b),
    get: (id: number) => http.get<Contract>(`/contracts/${id}`),
    create: (b: ContractIn) => http.post<Contract>('/contracts', b),
    update: (id: number, b: ContractIn) => http.put<Contract>(`/contracts/${id}`, b),
    sign: (id: number, file?: File | null) => {
      const fd = new FormData();
      if (file) fd.append('file', file);
      return http.post<Contract>(`/contracts/${id}/sign`, fd);
    },
    close: (id: number, reason: ContractCloseReason, comment?: string) => http.post<Contract>(`/contracts/${id}/close`, { reason, comment }),
    share: (id: number) => http.post<Contract>(`/contracts/${id}/share`),
    sendPdf: (id: number) => http.post<{ ok: boolean }>(`/contracts/${id}/send`),
    pdf: (id: number, signed = false, version?: number) => fetchBlob(`/contracts/${id}/pdf${version != null ? `?version=${version}` : signed ? '?signed=1' : ''}`),
  },
  deals: {
    calc: (b: DealCalcIn) => http.post<DealCalcOut>('/deals/calc', b),
    list: (q?: { month?: string; agent_id?: number; status?: string }) => http.get<L<Deal>>('/deals', { ...q, limit: LIMIT }),
    create: (b: Partial<Deal>) => http.post<Deal>('/deals', b),
    get: (id: number) => http.get<Deal>(`/deals/${id}`),
    confirm: (id: number) => http.post<Deal>(`/deals/${id}/confirm`),
    cancel: (id: number) => http.post<Deal>(`/deals/${id}/cancel`),
    expiring: (days = 30) => http.get<L<Deal>>('/deals/expiring', { days }),
    find: (cadastral: string) => http.get<L<Deal>>('/deals/find', { cadastral }),
  },

  evaluations: {
    create: (b: Partial<Evaluation>) => http.post<Evaluation>('/evaluations', b),
    list: (deal_id: number) => http.get<L<Evaluation>>('/evaluations', { deal_id }),
  },

  reports: {
    create: (kind: ReportKind, b: Record<string, unknown>) => then(http.post<Report>(`/reports/${kind}`, b), normReport),
    list: (q?: { kind?: string; author_id?: number; month?: string }) => then(http.get<L<Report>>('/reports', { ...q, limit: LIMIT }), (d) => normPage(d, normReport)),
    counters: (month?: string) => then(http.get<L<CounterRow>>('/counters', { month }), (d) => normPage(d, normCounter)),
  },

  analytics: {
    dashboard: () => then(http.get<Dashboard>('/analytics/dashboard'), normDashboard),
    funnel: (from?: string, to?: string) => http.get<Record<string, unknown>>('/analytics/funnel', { from, to }),
    listings: () => http.get<ListingsStats>('/analytics/listings'),
    agents: () => then(http.get<L<AgentStat>>('/analytics/agents'), (d) => normPage(d, normAgentStat)),
  },

  ticker: {
    list: () => http.get<L<TickerMessage>>('/ticker'),
    create: (text: string) => http.post<TickerMessage>('/ticker', { text }),
    remove: (id: number) => http.del<unknown>(`/ticker/${id}`),
  },
  news: () => http.get<L<NewsItem>>('/news'),

  settings: {
    get: () => http.get<Settings>('/settings'),
    put: (b: Settings) => http.put<Settings>('/settings', b),
  },

  userbot: {
    status: () => http.get<UserbotStatus>('/userbot'),
    login: () => http.post<UserbotStatus>('/userbot/login', {}),
    password: (password: string) => http.post<UserbotStatus>('/userbot/password', { password }),
    logout: () => http.post<UserbotStatus>('/userbot/logout', {}),
  },
  sources: {
    list: () => http.get<L<Source>>('/sources'),
    create: (b: Partial<Source>) => http.post<Source>('/sources', b),
    update: (id: number | string, b: Partial<Source>) => http.patch<Source>(`/sources/${id}`, b),
    schedule: () => http.get<SourceSchedule[]>('/sources/schedule'),
    setSchedule: (b: Record<string, number>) => http.put<SourceSchedule[]>('/sources/schedule', b),
  },

  moderation: {
    list: () => then(http.get<L<Listing>>('/moderation', { limit: LIMIT }), (d) => normPage(d, normListing)),
    decide: (listing_id: string, approve: boolean) => http.post<unknown>(`/moderation/${listing_id}`, { approve }),
  },

  addresses: () => http.get<L<{ id: number; district: string; address: string; ad_link?: string }>>('/addresses'),
};

export type ListingFilter = { q?: string; source?: string; flag?: string; district?: string; metro?: string; bedrooms?: string; price_min?: string; price_max?: string; known?: string };
