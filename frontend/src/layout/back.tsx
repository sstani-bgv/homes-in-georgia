// «Назад» that returns where you were (David 2026-11-12: Archive → object → back lost the search and the scroll).
import { useLayoutEffect, type ReactNode } from 'react';
import { Link, useLocation, useNavigate, useNavigationType } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { pageScroller } from '../auth/AuthContext';

/** «‹ Объекты»: one step back in history when we came from inside the app (same tab, search, page, scroll),
 * else `to` (a link opened directly, e.g. from a Telegram notification). */
export function BackLink({ to, children }: { to: string; children: ReactNode }) {
  const navigate = useNavigate();
  const loc = useLocation();
  return (
    <Link to={to} className="row gap-sm label" style={{ marginBottom: 12, display: 'inline-flex' }}
      onClick={(e) => { if (loc.key !== 'default') { e.preventDefault(); navigate(-1); } }}>
      <ArrowLeft size={14} />{children}
    </Link>
  );
}

/** Scroll position per history entry (window, or #root inside Telegram): back / ‹ Telegram lands on the same spot,
 * a new page opens at the top. The list may render a moment later, so the restore retries for a second. */
export function useScrollMemory() {
  const loc = useLocation();
  const type = useNavigationType();
  useLayoutEffect(() => {
    if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
    const el = pageScroller();
    const key = `scroll:${loc.key}`;
    const top = () => (el instanceof Window ? el.scrollY : el.scrollTop);
    let stop = false;
    if (type === 'POP') {
      let want = 0;
      try { want = Number(sessionStorage.getItem(key)) || 0; } catch { /* private mode */ }
      const until = performance.now() + 1000;
      const restore = () => {
        if (stop || !want) return;
        el.scrollTo(0, want);
        if (Math.abs(top() - want) > 2 && performance.now() < until) requestAnimationFrame(restore);
      };
      restore();
    } else if (type === 'PUSH') el.scrollTo(0, 0);
    const save = () => { try { sessionStorage.setItem(key, String(Math.round(top()))); } catch { /* private mode */ } };
    const cancel = () => { stop = true; };  // the user scrolls himself: stop restoring
    el.addEventListener('scroll', save, { passive: true });
    el.addEventListener('wheel', cancel, { passive: true });
    el.addEventListener('touchstart', cancel, { passive: true });
    return () => {
      stop = true;
      el.removeEventListener('scroll', save);
      el.removeEventListener('wheel', cancel);
      el.removeEventListener('touchstart', cancel);
    };
  }, [loc.key, type]);
}
