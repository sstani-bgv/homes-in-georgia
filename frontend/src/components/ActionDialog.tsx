import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { useLocation } from 'react-router-dom';
import { t } from '../lib/i18n';
import { Button, Field, Input } from './ui';

type Prompt = { label: string; required?: boolean };
type Ask = (message: string, input?: Prompt, ok?: string) => Promise<string | null>;
type Request = { message: string; input?: Prompt; ok?: string; key: string; resolve: (answer: string | null) => void };
const ActionDialog = createContext<Ask | null>(null);

export function ActionDialogProvider({ children }: { children: ReactNode }) {
  const { key } = useLocation();
  const dialog = useRef<HTMLDialogElement>(null);
  const pending = useRef<Request | null>(null);
  const [request, setRequest] = useState<Request | null>(null);
  const [answer, setAnswer] = useState('');

  const ask = useCallback<Ask>((message, input, ok) => new Promise((resolve) => {
    if (pending.current) { resolve(null); return; }
    const next = { message, input, ok, key, resolve };
    pending.current = next;
    setAnswer('');
    setRequest(next);
  }), [key]);

  const finish = useCallback((answer: string | null) => {
    const active = pending.current;
    if (!active) return;
    pending.current = null;
    dialog.current?.close();
    setRequest(null);
    active.resolve(answer);
  }, []);

  useEffect(() => {
    if (!request) return;
    if (request.key !== key) finish(null);
    else if (!dialog.current?.open) dialog.current?.showModal();
  }, [finish, key, request]);

  const missingAnswer = request?.input?.required && !answer.trim();
  return (
    <ActionDialog.Provider value={ask}>
      {children}
      <dialog ref={dialog} className="modal action-dialog" aria-labelledby="action-dialog-title" aria-describedby="action-dialog-message"
        onCancel={(e) => { e.preventDefault(); finish(null); }}
        onKeyDown={(e) => { if (e.key === 'Escape') e.stopPropagation(); }}>
        <div className="modal-head"><h2 id="action-dialog-title">{t('Подтвердите действие')}</h2></div>
        {request && <form className="modal-body col" onSubmit={(e) => {
          e.preventDefault();
          if (!missingAnswer) finish(request.key === key ? answer : null);
        }}>
          <p id="action-dialog-message" className="action-dialog-message">{request.message}</p>
          {request.input && <Field label={request.input.label}><Input value={answer} onChange={(e) => setAnswer(e.target.value)} aria-required={request.input.required} /></Field>}
          <div className="row row-wrap action-dialog-buttons">
            <Button variant="secondary" onClick={() => finish(null)}>{t('Отмена')}</Button>
            <Button type="submit" disabled={missingAnswer}>{request.ok || t('Подтвердить')}</Button>
          </div>
        </form>}
      </dialog>
    </ActionDialog.Provider>
  );
}

export function useActionDialog(): Ask {
  const ask = useContext(ActionDialog);
  if (!ask) throw new Error('ActionDialogProvider is missing');
  return ask;
}

export function useConfirm() {
  const ask = useActionDialog();
  /** ok: the confirm button's label (default «Подтвердить») */
  return async (message: string, ok?: string) => (await ask(message, undefined, ok)) !== null;
}
