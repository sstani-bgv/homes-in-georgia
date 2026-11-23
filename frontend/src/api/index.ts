// Typed API functions, straight from docs/ARCHITECTURE.md §7.
import { fetchBlob, http, uploadWithProgress } from './client';
import {
  normAgentStat, normAuth, normBoard, normCabinet, normClient, normCounter, normDashboard, normEvent, normLead, normListing, normPage,
  normProperty, normRecommend, normReport,
} from './normalize';
import type { IntegrationKey, KeyCheck, PermChange, PermMatrix, Role, Spend,
  Birthday, Invite, InviteInfo,
  AgentStat, AppConfig, AuthOut, Cabinet, Client, Contract, ContractCloseReason, ContractDraftIn, ContractIn, CounterRow, Dashboard, Deal, DealCalcIn, DealCalcOut,
  EventItem, Evaluation, Lead, LeadContract, LeadStatus, DealPrefill, Listing, NewsItem, Owner, Page, Property, RecommendedAgent, Report, ReportKind,
  Offer, OfferKind, OfferStatus, PhoneRow,
  Settings, SiteReport, Source, SourceSchedule, TickerMessage, UserbotStatus, User, ListingsStats, Elsewhere, PostDraft, ChangeRequest, DealPick, Demand, GeoHit, SimilarItem,
} from './types';

type L<T> = Page<T> | T[];
const LIMIT = 200;
const then = <A, B>(p: Promise<A>, f: (a: A) => B) => p.then(f);

/** «Собственник сдал»: the owner's own lease (price optional, goes to the archive comment) */
export interface OwnerLease { rent_from: string; rent_to: string; price?: string }

export type PublishProgress = { step?: 'files' | 'originals' | 'video' | 'photos' | 'post' | 'site'; n?: number; of?: number; chat?: string };

export const api = {
  config: () => http.get<AppConfig>('/config'),
  geo: {
    search: (q: string) => http.get<{ items: GeoHit[]; point: boolean; error?: boolean }>('/geo/search', { q }),
    point: (p: { lat: number; lng: number }) => http.get<{ district: string | null; address: string | null; metro: string | null; metro_m: number | null }>('/geo/point', p, { timeoutMs: 15_000 }),  // a hung OSM lookup ends (Irakli 11-19)
  },

  auth: {
    login: (login: string, password: string) => then(http.post<AuthOut>('/auth/login', { login, password }), normAuth),
    telegramWebApp: (init_data: string) => then(http.post<AuthOut>('/auth/telegram-webapp', { init_data }), normAuth),
    telegramLogin: (data: Record<string, unknown>) => then(http.post<AuthOut>('/auth/telegram-login', data), normAuth),
    inviteInfo: (token: string) => http.get<InviteInfo>(`/auth/invite/${encodeURIComponent(token)}`),
    acceptInvite: (token: string, data: Record<string, unknown>) => then(http.post<AuthOut>('/auth/invite', { ...data, token }), normAuth),
    me: () => http.get<User>('/auth/me'),
    /** the user's own profile (cabinet «Заполните профиль») */
    updateMe: (b: Pick<Partial<User>, 'first_name' | 'last_name' | 'birth_date' | 'phone' | 'email' | 'ie_number'>) => http.patch<User>('/auth/me', b),
  },

  users: {
    list: (q?: { role?: string; active?: boolean | string }) => http.get<L<User>>('/users', q),
    create: (b: Partial<User> & { password?: string }) => http.post<User>('/users', b),
    update: (id: number, b: Partial<User> & { password?: string }) => http.patch<User>(`/users/${id}`, b),
    promote: (id: number) => http.post<User>(`/users/${id}/promote`),
    stats: (id: number) => http.get<Record<string, unknown>>(`/users/${id}/stats`),
    photo: (id: number, file: File) => { const fd = new FormData(); fd.append('file', file); return http.post<User>(`/users/${id}/photo`, fd); },
    /** an inactive user: hidden everywhere, his objects → «Платформа» (11-16) */
    remove: (id: number) => http.del<{ ok: boolean; objects_to_platform: number }>(`/users/${id}`),
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
    agent: (id: string, body: { owner_name?: string; owner_phone?: string }) => http.post<{ ok: boolean }>(`/listings/${id}/agent`, body),
    facets: (competitor?: boolean) => http.get<Record<'sources' | 'districts' | 'metros', { value: string; count: number; label?: string }[]>>('/listings/new/facets', competitor ? { competitor: 1 } : undefined),
    get: (id: string) => then(http.get<Listing>(`/listings/${id}`), normListing),
    /** «Похожие объекты»: our objects (the archive too) and the live ads of every site */
    similar: (id: string) => http.get<{ items: SimilarItem[] }>(`/listings/${id}/similar`),
  },

  similar: {
    /** «Дубль» / «Не дубль» on a pair («l:<ad>» / «p:<object>»); an ad called the same as an object is linked to it */
    /** verdict review: «Нужна проверка» — the pair stays listed, flagged for the operator (David 11-19) */
    decide: (a: string, b: string, verdict: 'same' | 'different' | 'review') => http.post<{ ok: boolean; verdict: string; linked_to: number | null; revived?: number | null; owner_taken?: string | null }>('/similar/decide', { a, b, verdict }),
  },

  properties: {
    myhomeCopies: (id: number) => http.post<{ ok: boolean }>(`/properties/${id}/myhome-copies`, {}),
    compareLink: (id: number, url: string) => http.post<{ listing_id: string; url: string; address: string | null; area: number | null; floor: number | null; bedrooms: number | null; price_usd: number | null; photos: string[]; pct: number | null; level: 'same' | 'maybe' | null; why: string | null }>(`/properties/${id}/compare-link`, { url }),
    originals: (id: number) => fetchBlob(`/properties/${id}/originals`),
    list: (q?: PropertyScope & { q?: string; rented?: number; freeing?: number; limit?: number; offset?: number; source?: string[]; district?: string[]; metro?: string; bedrooms?: string; price_min?: string; price_max?: string; archive_reason?: string }) => then(http.get<L<Property>>('/properties', { limit: LIMIT, ...q }), (d) => normPage(d, normProperty)),
    /** the base tabs' filter options with counts (Stanislav 11-13) */
    facets: (q: PropertyScope) => http.get<Record<'sources' | 'districts' | 'metros', { value: string; count: number }[]>>('/properties/facets', q),
    counts: (deal?: 'rent' | 'sale') => http.get<Record<string, number>>('/properties/counts', { deal }),
    board: (deal?: 'rent' | 'sale') => then(http.get<Record<string, Property[]>>('/properties/board', { deal }), (d) => normBoard(d, normProperty)),
    boardHide: (status: 'published' | 'rented') => http.post<{ hidden: number }>('/properties/board/hide', { status }),
    /** force: «Это другой объект — беру» after a possible_duplicate answer (its data.items: SimilarItem[]) */
    take: (listing_id: string, force?: boolean) => http.post<Property>('/properties', force ? { listing_id, force } : { listing_id }),
    create: (b: Partial<Property> & { owner_name?: string; owner_phone?: string; owner_phones?: PhoneRow[] }) => http.post<Property>('/properties', b),
    get: (id: number) => then(http.get<Property>(`/properties/${id}`), normProperty),
    update: (id: number, b: Partial<Property> & { owner_name?: string; owner_phone?: string; owner_phones?: PhoneRow[]; owner_separate?: boolean; reason?: string }) => http.patch<Property>(`/properties/${id}`, b),
    call: (id: number, reached: boolean) => http.post<Property>(`/properties/${id}/call`, { reached }),
    owner: (id: number, b: { is_owner: boolean; owner_name?: string; owner_phone?: string; owner_phones?: PhoneRow[] }) =>
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
    noEsign: (id: number, reason: string, file?: File | null) => {
      const fd = new FormData();
      fd.append('reason', reason);
      if (file) fd.append('file', file);
      return http.post<Property>(`/properties/${id}/exclusive-no-esign`, fd);
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
    /** One part of a big file (backend properties.upload_part): {received} until the last part, then the card. */
    uploadMediaPart: (id: number, part: Blob, f: { upload_id: string; name: string; offset: number; total: number }, onProgress: (f: number) => void) => {
      const fd = new FormData();
      fd.append('file', part, f.name);
      Object.entries(f).forEach(([k, v]) => fd.append(k, String(v)));
      return uploadWithProgress<{ received: number } | (Property & { uploaded_ids?: number[] })>(`/properties/${id}/media/part`, fd, onProgress);
    },
    mediaOrder: (id: number, ids: number[]) => http.put<Property>(`/properties/${id}/media/order`, { ids }),
    deleteMedia: (id: number, mid: number) => http.del<unknown>(`/properties/${id}/media/${mid}`),
    mediaRoom: (id: number, mid: number, room: string | null) => http.patch<{ id: number; room: string | null }>(`/properties/${id}/media/${mid}`, { room }),
    checklist: (id: number, checklist: Record<string, boolean>) => http.post<Property>(`/properties/${id}/checklist`, { checklist }),
    submit: (id: number, otherFlat = false) => http.post<Property>(`/properties/${id}/submit${otherFlat ? '?other_flat=1' : ''}`),
    review: (id: number, approve: boolean, comment?: string) => http.post<Property>(`/properties/${id}/review`, { approve, comment }),
    post: (id: number) => http.get<PostDraft>(`/properties/${id}/post`),
    site: (id: number, on: boolean) => then(http.post<Property>(`/properties/${id}/site`, { on }), normProperty),
    publish: (id: number, b?: { text?: string; media_ids?: number[]; channel?: string; comment?: string; tg?: boolean; site?: boolean }) => then(http.post<Property>(`/properties/${id}/publish`, b), normProperty),
    /** what the running publish does now (backend app/progress.py); {} when none runs */
    publishProgress: (id: number) => http.get<PublishProgress>(`/properties/${id}/publish/progress`),
    /** reason: ARCHIVE_REASON key; a verified object's agent gets change_request back instead */
    archive: (id: number, b: { reason: string; comment?: string; duplicate_of?: number | null } & Partial<OwnerLease>) => http.post<Property>(`/properties/${id}/archive`, b),
    /** agent's «Запрос оператору»: move the owner to another agent (staff: moved right away, change_request null) */
    transferRequest: (id: number, agent_id: number, reason?: string) => http.post<{ change_request: ChangeRequest | null }>(`/properties/${id}/transfer-request`, { agent_id, reason }),
    elsewhere: (id: number) => http.get<{ items: Elsewhere[] }>(`/properties/${id}/elsewhere`),
    similar: (id: number) => http.get<{ items: SimilarItem[] }>(`/properties/${id}/similar`),
    adRemoved: (id: number, listingId: string) => then(http.post<Property>(`/properties/${id}/listings/${listingId}/removed`), normProperty),
    refused: (id: number, comment?: string) => http.post<Property>(`/properties/${id}/refused`, { comment }),
    thinking: (id: number, until: string, comment?: string) => http.post<Property>(`/properties/${id}/thinking`, { until, comment }),
    resume: (id: number) => http.post<Property>(`/properties/${id}/resume`),
    /** «Собственник сдал»: the owner's lease dates are required (Stanislav 11-16) */
    rentedSelf: (id: number, b: OwnerLease & { comment?: string }) => http.post<Property>(`/properties/${id}/rented-self`, b),
    history: (id: number) => then(http.get<L<EventItem>>(`/properties/${id}/history`), (d) => normPage(d, normEvent)),
    dealSearch: (q: { q?: string; pid?: number; deal_type?: string; lead_id?: number }) => http.get<{ items: DealPick[] }>('/properties/deal-search', q),
    /** the contract wizard's prefill from the object card («Оформить договор аренды», 11-15) */
    contract: (id: number) => http.get<Omit<LeadContract, 'lead_id' | 'client' | 'contract'> & { property_id: number }>(`/properties/${id}/contract`),
    verify: (id: number) => then(http.post<Property>(`/properties/${id}/verify`), normProperty),
    demand: (id: number) => http.get<Demand>(`/properties/${id}/demand`),
    fastTrack: (id: number) => http.post<Property & { sent: number }>(`/properties/${id}/fast-track`),
    checkDuplicate: (b: { phone?: string; address?: string; url?: string; cadastral?: string }) =>
      http.post<{ status?: string; matches?: unknown[]; [k: string]: unknown }>('/properties/check-duplicate', b),
  },

  /** sale objects / cars of the site hge.ge */
  offers: {
    list: (kind: OfferKind, q?: { status?: OfferStatus; q?: string }) => http.get<Page<Offer>>(`/offers/${kind}`, { ...q, limit: 500 }),
    get: (kind: OfferKind, id: number) => http.get<Offer>(`/offers/${kind}/${id}`),
    create: (kind: OfferKind, b: Record<string, unknown>) => http.post<Offer>(`/offers/${kind}`, b),
    update: (kind: OfferKind, id: number, b: Record<string, unknown>) => http.patch<Offer>(`/offers/${kind}/${id}`, b),
    setStatus: (kind: OfferKind, id: number, status: OfferStatus) => http.post<Offer>(`/offers/${kind}/${id}/status`, { status }),
    upload: (kind: OfferKind, id: number, file: File, onProgress: (f: number) => void) => {
      const fd = new FormData();
      fd.append('files', file);
      return uploadWithProgress<Offer & { uploaded_keys: string[] }>(`/offers/${kind}/${id}/media`, fd, onProgress);
    },
    order: (kind: OfferKind, id: number, keys: string[]) => http.put<Offer>(`/offers/${kind}/${id}/media/order`, { keys }),
    deleteFile: (kind: OfferKind, id: number, key: string) => http.del<Offer>(`/offers/${kind}/${id}/media?key=${encodeURIComponent(key)}`),
    post: (kind: OfferKind, id: number) => http.get<{ text: string }>(`/offers/${kind}/${id}/post`),
    publish: (kind: OfferKind, id: number, b: { text?: string; tg: boolean; site: boolean; keys?: string[] }) => http.post<Offer>(`/offers/${kind}/${id}/publish`, b),
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
    list: (q?: { status?: string; lead_id?: number; deal_id?: number; property_id?: number; agent_id?: number; q?: string }) => http.get<L<Contract>>('/contracts', { ...q, limit: LIMIT }),
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
    /** «Привязать к заявке»: a contract created without a lead / deal (11-13) */
    link: (id: number, b: { lead_id?: number; deal_id?: number }) => http.post<Contract>(`/contracts/${id}/link`, b),
    /** «Оформить сделку»: the contract's lead for the deal form, made from the contract when it has none (11-18) */
    dealLead: (id: number) => http.post<Contract>(`/contracts/${id}/deal-lead`, {}),
    close: (id: number, reason: ContractCloseReason, comment?: string) => http.post<Contract>(`/contracts/${id}/close`, { reason, comment }),
    share: (id: number) => http.post<Contract>(`/contracts/${id}/share`),
    esign: (id: number) => http.post<Contract>(`/contracts/${id}/esign`),
    esignCancel: (id: number) => http.post<Contract>(`/contracts/${id}/esign/cancel`),
    sendPdf: (id: number) => http.post<{ ok: boolean }>(`/contracts/${id}/send`),
    sendClient: (id: number, to?: string) => http.post<Contract>(`/contracts/${id}/send-client`, { to: to || null }),
    pdf: (id: number, signed = false, version?: number) => fetchBlob(`/contracts/${id}/pdf${version != null ? `?version=${version}` : signed ? '?signed=1' : ''}`),
    extract: (id: number) => fetchBlob(`/contracts/${id}/extract`),
  },
  deals: {
    calc: (b: DealCalcIn) => http.post<DealCalcOut>('/deals/calc', b),
    list: (q?: { month?: string; agent_id?: number; status?: string }) => http.get<L<Deal>>('/deals', { ...q, limit: LIMIT }),
    create: (b: Partial<Deal>) => http.post<Deal>('/deals', b),
    get: (id: number) => http.get<Deal>(`/deals/${id}`),
    confirm: (id: number) => http.post<Deal>(`/deals/${id}/confirm`),
    cancel: (id: number, reason?: string) => http.post<Deal>(`/deals/${id}/cancel`, { reason }),
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
    /** «Посты в канал»: posts per agent over [from, to] (YYYY-MM-DD, Tbilisi days) */
    channelPosts: (from: string, to: string) => http.get<{ total: number; items: { agent_id: number | null; name: string; posts: number; exclusive: number; last: string | null }[] }>('/analytics/channel-posts', { from, to }),
    /** hge.ge visitors and contacts (superadmins only); fresh = skip the 2-minute server cache. */
    site: (days: number, fresh?: boolean) => http.get<SiteReport>('/analytics/site', { days, fresh: fresh || undefined }),
  },

  ticker: {
    list: () => http.get<L<TickerMessage>>('/ticker'),
    create: (text: string) => http.post<TickerMessage>('/ticker', { text }),
    remove: (id: number) => http.del<unknown>(`/ticker/${id}`),
  },
  news: () => http.get<L<NewsItem>>('/news'),

  keys: {  // Управление → Ключи (admin+): settings in full, secrets masked; reveal is logged on the server
    list: () => http.get<{ items: IntegrationKey[] }>('/keys'),
    put: (name: string, value: string) => http.put<{ items: IntegrationKey[] }>(`/keys/${encodeURIComponent(name)}`, { value }),
    remove: (name: string) => http.del<{ items: IntegrationKey[] }>(`/keys/${encodeURIComponent(name)}`),
    reveal: (name: string) => http.get<{ name: string; value: string }>(`/keys/${encodeURIComponent(name)}/value`),
    check: (name: string) => http.post<{ results: KeyCheck[] }>(`/keys/${encodeURIComponent(name)}/check`),
  },
  perms: {  // Управление → Права (superadmin): role × section Ч/С/И/У, backend services/perms.py
    get: () => http.get<PermMatrix>('/admin/permissions'),
    put: (m: Record<string, Record<string, string>>) => http.put<PermMatrix>('/admin/permissions', m),
    history: () => http.get<PermChange[]>('/admin/permissions/history'),
    createRole: (label: string, base: Role) => http.post<PermMatrix>('/admin/permissions/roles', { label, base }),
    deleteRole: (id: number) => http.del<PermMatrix>(`/admin/permissions/roles/${id}`),
  },
  spend: () => http.get<Spend>('/spend'),  // Управление → Ключи → Расходы: Apify + AI
  settings: {
    get: () => http.get<Settings>('/settings'),
    put: (b: Settings) => http.put<Settings>('/settings', b),
  },

  userbot: {
    status: () => http.get<UserbotStatus>('/userbot'),
    login: () => http.post<UserbotStatus>('/userbot/login', {}),
    password: (password: string) => http.post<UserbotStatus>('/userbot/password', { password }),
    phone: (phone: string) => http.post<UserbotStatus>('/userbot/phone', { phone }),
    code: (code: string) => http.post<UserbotStatus>('/userbot/code', { code }),
    logout: () => http.post<UserbotStatus>('/userbot/logout', {}),
  },
  reader: {
    status: () => http.get<UserbotStatus>('/reader'),
    login: () => http.post<UserbotStatus>('/reader/login', {}),
    password: (password: string) => http.post<UserbotStatus>('/reader/password', { password }),
    phone: (phone: string) => http.post<UserbotStatus>('/reader/phone', { phone }),
    code: (code: string) => http.post<UserbotStatus>('/reader/code', { code }),
    logout: () => http.post<UserbotStatus>('/reader/logout', {}),
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

export type PropertyScope = { status?: string; agent_id?: number; mine?: number; base?: number; deal?: 'rent' | 'sale' };
export type ListingFilter = { q?: string; source?: string[]; flag?: string; district?: string[]; metro?: string; bedrooms?: string; price_min?: string; price_max?: string; known?: string; competitor?: string; prop_type?: string };
