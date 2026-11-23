// «Похожие объекты» (Stanislav 11-17): one flat is never worked twice. For an ad or an object, every candidate across our
// base (the archive too), ss / myhome / livo / korter, Facebook / Telegram and the competitors, with why it looks the
// same; the agent says «Дубль» / «Не дубль» (backend services/similar.py).
import { useConfirm } from './ActionDialog';
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { ExternalLink } from 'lucide-react';
import { api } from '../api';
import type { PropertyStatus, SimilarItem } from '../api/types';
import { Badge, Button, ErrorBox, Loading, Modal } from './ui';
import { CompareSide, ListingSide, PhotoStrip, PropertyStatusBadge } from './domain';
import { useAuth } from '../auth/AuthContext';
import { errText, useToast } from './toast';
import { t } from '../lib/i18n';
import { PROPERTY_STATUS, bedroomsLabel, districtLabel, fmtDateTime, money } from '../lib/format';

/** subject: «l:<ad id>» / «p:<object id>». intro: the warning line above the list (the review step, the take check). */
export function SimilarList({ subject, intro, emptyText, onLinked }: { subject: string; intro?: string; emptyText?: string; onLinked?: (pid: number) => void }) {
  const [kind, id] = [subject.slice(0, 1), subject.slice(2)];
  const qk = ['similar', subject];
  const { data, isLoading, error } = useQuery({
    queryKey: qk,
    queryFn: () => (kind === 'p' ? api.properties.similar(Number(id)) : api.listings.similar(id)),
  });
  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  const items = data?.items || [];
  if (!items.length) return emptyText ? <div className="muted" style={{ fontSize: 13 }}>{emptyText}</div> : null;
  return (
    <div className="col gap-sm">
      {intro && <div className="alert alert-warn" style={{ fontSize: 13 }}>{intro}</div>}
      {items.map((x) => <SimilarRow key={x.key} subject={subject} x={x} qk={qk} onLinked={onLinked} />)}
    </div>
  );
}

type Verdict = 'same' | 'different' | 'review';

function useDecide(subject: string, x: SimilarItem, qk: unknown[], onLinked?: (pid: number) => void, after?: () => void) {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (verdict: Verdict) => api.similar.decide(subject, x.key, verdict),
    onSuccess: (r, verdict) => {
      qc.invalidateQueries({ queryKey: qk });
      qc.invalidateQueries({ queryKey: ['listings'] });
      qc.invalidateQueries({ queryKey: ['property'] });
      if (r.revived) { toast(t('Объект #{id} снова в работе у вас', { id: r.revived })); onLinked?.(r.revived); }
      else if (r.owner_taken) toast(t('Объявление привязано, но объект в архиве и его собственник закреплён за {agent} — попросите его или оператора передать собственника', { agent: r.owner_taken }), true);
      else if (r.linked_to) { toast(t('Объявление привязано к объекту #{id}', { id: r.linked_to })); onLinked?.(r.linked_to); }
      else toast(verdict === 'same' ? t('Отмечено: дубль') : verdict === 'review' ? t('Отмечено: нужна проверка') : t('Отмечено: другой объект'));
      after?.();
    },
    onError: (e) => toast(errText(e), true),
  });
}

const paramLine = (x: { district?: string | null; bedrooms?: number | null; area?: number | null; floor?: number | null; price_usd?: number | null }) =>
  [districtLabel(x.district), bedroomsLabel(x.bedrooms), x.area ? `${x.area} ${t('м²')}` : null,
    x.floor != null ? t('этаж {n}', { n: x.floor }) : null, x.price_usd ? money(x.price_usd, 'USD') : null].filter(Boolean).join(' · ');

function SimilarRow({ subject, x, qk, onLinked }: { subject: string; x: SimilarItem; qk: unknown[]; onLinked?: (pid: number) => void }) {
  const loc = useLocation();
  const [cmp, setCmp] = useState(false);
  const decide = useDecide(subject, x, qk, onLinked);
  const confirm = useConfirm();
  const { isStaff } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const pid = subject.startsWith('p:') ? Number(subject.slice(2)) : null;
  // «Попросить снять» (the agent's task) / «Проверено: снято» (the operator opened it and saw it gone) of a site ad
  const ad = useMutation({
    mutationFn: (what: 'ask' | 'removed') => (what === 'ask' ? api.properties.adTakedown : api.properties.adRemoved)(pid!, x.listing_id!),
    onSuccess: (r, what) => {
      qc.setQueryData(['property', pid], r);
      qc.invalidateQueries({ queryKey: qk });
      toast(what === 'ask' ? t('Задача агенту: попросить снять') : t('Отмечено: объявление снято'));
    },
    onError: (e) => toast(errText(e), true),
  });
  const site = !!pid && !!x.site && !!x.listing_id;
  const obj = x.kind === 'object';
  const title = obj ? t('Объект #{id}', { id: x.property_id ?? '' }) : x.where;
  const params = paramLine(x);
  const objLink = obj && x.can_open ? `${loc.pathname.startsWith('/m/') ? '/m/properties' : '/objects'}/${x.property_id}` : null;
  return (
    <div className="card" style={{ padding: 12, borderColor: x.level === 'same' && !x.linked ? 'var(--red, #d33)' : undefined }}>
      <div className="col gap-sm">
        <div className="row row-wrap gap-sm" style={{ alignItems: 'center' }}>
          {x.linked ? <Badge tone="grey">{t('привязано')}</Badge>
            : <Badge tone={x.level === 'same' ? 'red' : 'warn'}>{x.pct != null ? `${x.pct}% · ` : ''}{x.level === 'same' ? t('дубль / почти точно') : t('вероятный, нужна проверка')}</Badge>}
          <strong>{title}</strong>
          {x.who && <Badge tone="grey">{t(x.who)}</Badge>}
          {obj && x.status && <PropertyStatusBadge status={x.status as PropertyStatus} />}
          {!obj && !x.linked && <Badge tone={x.competitor ? 'redsoft' : 'grey'}>{x.competitor ? t('конкурент') : t('объявление')}</Badge>}
          {x.removed && <Badge tone="ok">{t('проверено: снято')}</Badge>}
          {obj && x.is_exclusive && <Badge tone="solid">{t('эксклюзив')}</Badge>}
          {x.decided === 'same' && <Badge tone="red">{t('отмечен дублем')}</Badge>}
          {x.decided === 'review' && <Badge tone="warn">{t('нужна проверка')}</Badge>}
        </div>
        <div style={{ fontSize: 14 }}>{x.address || t('Адрес не указан')}</div>
        {params && <div className="muted" style={{ fontSize: 13 }}>{params}</div>}
        {obj && <div className="faint" style={{ fontSize: 12 }}>{x.agent ? t('Ведёт: {name}', { name: x.agent }) : t('Без агента')}</div>}
        <div style={{ fontSize: 13 }}><span className="label">{t('Почему: ')}</span>{x.reasons.join(' · ')}</div>
        {x.asked && <div className="muted" style={{ fontSize: 12 }}>{t('Попросили снять {d}', { d: fmtDateTime(x.asked.at) })}{x.asked.by ? ` · ${x.asked.by}` : ''}</div>}
        {!!x.differs?.length && <div style={{ fontSize: 13 }}><span className="label">{t('Отличается: ')}</span>{x.differs.join(' · ')}</div>}
        {!!x.matched_photos?.length && <div className="faint" style={{ fontSize: 12 }}>{t('Совпавшие фото: {n} — первыми в ленте', { n: x.matched_photos.length })}</div>}
        <PhotoStrip photos={x.photos} />
        {cmp && <CompareModal subject={subject} x={x} qk={qk} onLinked={onLinked} onClose={() => setCmp(false)} />}
        <div className="row row-wrap gap-sm">
          <Button size="sm" variant="secondary" onClick={() => setCmp(true)}>{t('Сравнить')}</Button>
          {objLink && <Link className="btn btn-secondary btn-sm" to={objLink}><ExternalLink size={14} />{t('Открыть объект')}</Link>}
          {x.url && <a className="btn btn-secondary btn-sm" href={x.url} target="_blank" rel="noreferrer"><ExternalLink size={14} />{obj ? t('Пост в канале') : t('Открыть источник')}</a>}
          {!x.linked && x.decided !== 'same' && (
            <Button size="sm" loading={decide.isPending && decide.variables === 'same'} onClick={() => decide.mutate('same')}>
              {subject.startsWith('l:') && obj ? t('Дубль: это он') : t('Дубль')}
            </Button>
          )}
          {!x.linked && <Button size="sm" variant="secondary" loading={decide.isPending && decide.variables === 'different'} onClick={() => decide.mutate('different')}>{t('Не дубль')}</Button>}
          {!x.linked && x.decided !== 'review' && x.decided !== 'same' && (
            <Button size="sm" variant="secondary" loading={decide.isPending && decide.variables === 'review'} onClick={() => decide.mutate('review')}>{t('Нужна проверка')}</Button>
          )}
          {site && !x.removed && !x.asked && (
            <Button size="sm" variant="secondary" loading={ad.isPending && ad.variables === 'ask'} onClick={() => ad.mutate('ask')}>{t('Попросить снять')}</Button>
          )}
          {site && !x.removed && isStaff && (
            <Button size="sm" loading={ad.isPending && ad.variables === 'removed'}
              onClick={async () => { if (await confirm(t('Вы открыли ссылку и убедились, что объявление снято с сайта?'))) ad.mutate('removed'); }}>
              {t('Проверено: снято')}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

/** «Беру в работу» answered possible_duplicate: the objects of our base this ad may be. «Дубль: это он» links the ad to
 * that object (nothing new is made); «Это другой объект — беру» takes it anyway, those objects are noted as different. */
export function TakeCheck({ listingId, items, taking, onTake, onClose }: { listingId: string; items: SimilarItem[]; taking: boolean; onTake: () => void; onClose: () => void }) {
  return (
    <div className="col gap-lg">
      <div className="alert alert-warn">{t('Похоже, эта квартира уже есть в нашей базе. Проверьте, чтобы не работать с одним объектом дважды: если это он — нажмите «Дубль: это он».')}</div>
      <div className="col gap-sm">{items.map((x) => <SimilarRow key={x.key} subject={`l:${listingId}`} x={x} qk={['similar', `l:${listingId}`]} onLinked={onClose} />)}</div>
      <div className="modal-actions">
        <Button block loading={taking} onClick={onTake}>{t('Это другой объект — беру в работу')}</Button>
        <Button block variant="secondary" onClick={onClose}>{t('Отмена')}</Button>
      </div>
    </div>
  );
}

/** Side by side, as «Сравнить по фото» in the feed: this ad / object on the left, the candidate on the right; «Дубль» /
 * «Не дубль», and for two objects the operator merges them right here: the other one goes «В архив → Дубль» (its leads,
 * deals, contracts and exclusive contract move to the one that stays, backend properties._merge_duplicate). */
function CompareModal({ subject, x, qk, onLinked, onClose }: { subject: string; x: SimilarItem; qk: unknown[]; onLinked?: (pid: number) => void; onClose: () => void }) {
  const confirm = useConfirm();
  const isAd = subject.startsWith('l:');
  const id = subject.slice(2);
  const { isStaff } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const loc = useLocation();
  const ad = useQuery({ queryKey: ['listing', id], queryFn: () => api.listings.get(id), enabled: isAd });
  const me = useQuery({ queryKey: ['property', Number(id)], queryFn: () => api.properties.get(Number(id)), enabled: !isAd });
  const decide = useDecide(subject, x, qk, onLinked, onClose);
  const obj = x.kind === 'object';
  const merge = useMutation({
    mutationFn: ({ drop, keep }: { drop: number; keep: number }) => api.properties.archive(drop, { reason: 'duplicate', duplicate_of: keep }),
    onSuccess: (_, { drop, keep }) => {
      qc.invalidateQueries({ queryKey: ['similar'] });
      qc.invalidateQueries({ queryKey: ['properties'] });
      qc.invalidateQueries({ queryKey: ['property'] });
      toast(t('Объединено: #{drop} удалён как дубль, всё перенесено в #{keep}', { drop, keep }));
      onClose();
      if (drop === Number(id)) navigate(`${loc.pathname.startsWith('/m/') ? '/m/properties' : '/objects'}/${keep}`);
    },
    onError: (e) => toast(errText(e), true),
  });
  const ask = async (drop: number, keep: number) => {
    if (await confirm(t('Объект #{drop} будет удалён как дубль: его заявки, показы, сделки и договор перейдут в #{keep}. Продолжить?', { drop, keep }))) merge.mutate({ drop, keep });
  };
  const p = me.data;
  const own = (p?.media || []).filter((m) => m.kind === 'photo').map((m) => m.url || m.path);
  const left = isAd
    ? (ad.data ? <ListingSide l={ad.data} /> : <Loading />)
    : (p ? <CompareSide label={t('Этот объект #{id}', { id: p.id })} photos={own.length ? own : p.photos || []} address={p.address} rows={[
        [t('Цена'), money(p.price, p.currency || 'USD')], [t('Район'), districtLabel(p.district) || '—'], [t('Спальни'), bedroomsLabel(p.bedrooms) || '—'],
        [t('Площадь'), p.area ? `${p.area} ${t('м²')}` : '—'], [t('Этаж'), p.floor ?? '—'],
      ]} /> : <Loading />);
  const right = (
    <CompareSide label={obj ? `${t('Объект #{id}', { id: x.property_id ?? '' })}${x.status ? ` · ${PROPERTY_STATUS[x.status as PropertyStatus] || x.status}` : ''}${x.agent ? ` · ${x.agent}` : ''}` : x.where}
      photos={x.photos} address={x.address} rows={[
        [t('Цена'), money(x.price_usd ?? null, 'USD')], [t('Район'), districtLabel(x.district) || '—'], [t('Спальни'), bedroomsLabel(x.bedrooms) || '—'],
        [t('Площадь'), x.area ? `${x.area} ${t('м²')}` : '—'], [t('Этаж'), x.floor ?? '—'],
      ]}
      link={obj && x.can_open ? { href: `${loc.pathname.startsWith('/m/') ? '/m/properties' : '/objects'}/${x.property_id}`, text: t('Открыть объект') }
        : x.url ? { href: x.url, text: obj ? t('Пост в канале') : t('Открыть источник') } : undefined} />
  );
  const both = !isAd && obj && x.property_id;
  return (
    <Modal open wide onClose={onClose} title={t('Это та же квартира?')}>
      <div className="col gap-lg">
        <div className={`alert ${x.level === 'same' ? 'alert-warn' : 'alert-info'}`} style={{ fontSize: 13 }}>
          {t('Почему похож: {why}. Сравните фото (нажмите, чтобы открыть крупно) и решите сами.', { why: x.why || x.reasons.join('; ') })}
        </div>
        <div className="cmp">{left}{right}</div>
        <div className="row row-wrap gap-sm">
          {x.decided !== 'same' && !both && (
            <Button loading={decide.isPending && decide.variables === 'same'} onClick={() => decide.mutate('same')}>{isAd && obj ? t('Дубль: это он') : t('Дубль')}</Button>
          )}
          {both && isStaff && (
            <>
              <Button loading={merge.isPending} onClick={() => ask(Number(id), x.property_id!)}>{t('Объединить: оставить #{id}', { id: x.property_id! })}</Button>
              <Button variant="secondary" loading={merge.isPending} onClick={() => ask(x.property_id!, Number(id))}>{t('Объединить: оставить #{id}', { id })}</Button>
            </>
          )}
          {both && !isStaff && x.decided !== 'same' && (
            <Button loading={decide.isPending && decide.variables === 'same'} onClick={() => decide.mutate('same')}>{t('Дубль — сообщить оператору')}</Button>
          )}
          <Button variant="secondary" loading={decide.isPending && decide.variables === 'different'} onClick={() => decide.mutate('different')}>{t('Не дубль')}</Button>
          {x.decided !== 'review' && x.decided !== 'same' && (
            <Button variant="secondary" loading={decide.isPending && decide.variables === 'review'} onClick={() => decide.mutate('review')}>{t('Нужна проверка')}</Button>
          )}
        </div>
      </div>
    </Modal>
  );
}
