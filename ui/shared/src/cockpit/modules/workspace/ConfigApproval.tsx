import { useEffect, useRef, useState } from 'react';
import { Badge, KeyValue, toAvailability, unavailable, useAsync, useExecution, useProvider, useWorkspace, useWorkspacePath, type Availability } from '../../core';
import type { ConfigurationApprovalStatus } from '../../../model/kup';

/** The blocker code Kriya's preflight raises while operator-controlled configuration has no current approval. */
export const CONFIG_APPROVAL_BLOCKER = 'CONFIG_AUTHORITY_REFUSED';
export const STALE_MESSAGE = 'Configuration changed while you were reviewing it. Review the updated configuration before approving.';

const REASON_HELP: Record<string, string> = {
  trust_file_in_effect: 'A trust file is in effect for Kriya, so an approval given here would not be consulted. An operator must manage it outside this application.',
  trust_file_inside_workspace: 'Kriya refuses a trust file inside the project folder. An operator must fix its location outside this application.',
  store_inside_workspace: 'The approval store is configured inside the project folder, which Kriya refuses. An operator must fix its location outside this application.',
  store_unwritable: 'Kriya could not save the approval. Check that the approval location is writable, then try again.',
};

/** What Kriya says about this folder's configuration approval, asked afresh whenever `nonce` or the folder changes (an answer for an earlier key is never shown). */
export function useApprovalStatus(workspace: string | null, nonce: number, enabled: boolean): Availability<ConfigurationApprovalStatus> {
  const provider = useProvider();
  return useAsync<ConfigurationApprovalStatus>(workspace === null || !enabled ? null : `approval:${workspace}:${nonce}`,
    async (signal) => toAvailability(await provider.configurationApprovalStatus(workspace!, signal)),
    unavailable<ConfigurationApprovalStatus>('unknown_run', 'No workspace is open.'));
}

/** Where Kriya says a protected setting came from, in plain words (the raw source name stays in Technical details). */
const SOURCE_WORDS: Record<string, string> = {
  auto_discovered_cwd: 'set by this project\'s kriya.yaml',
  auto_discovered_install_dir: 'set by the Kriya installation\'s kriya.yaml',
  explicit_config_path_inside_workspace: 'set by a configuration file inside this project',
  explicit_config_path_outside_workspace: 'set by an explicitly named configuration file',
  runtime_profile_override: 'implied by the configured runtime profile',
};

const short = (fingerprint: string | null) => (fingerprint === null ? '' : fingerprint.replace(/^sha256:/, '').slice(0, 12));

/**
 * The review surface for configuration approval. It shows what Kriya reports - the NAMES of the operator-controlled settings and a fingerprint of that exact
 * configuration, never a value - and one explicit button. It holds no authority: Approve Configuration sends only the fingerprint it displayed; Kriya re-resolves
 * the configuration and refuses (APPROVAL_STALE) if it differs. Nothing here approves on open, on typing, on Enter or on Start Run: focus opens on Cancel.
 */
function ReviewDialog({ onClose, onApproved }: { onClose(): void; onApproved(): void }) {
  const ex = useExecution();
  const ws = useWorkspace();
  const workspace = useWorkspacePath();
  const [nonce, setNonce] = useState(0);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [failure, setFailure] = useState<{ code: string; message: string; reason: string | null } | null>(null);
  const cancel = useRef<HTMLButtonElement | null>(null);
  useEffect(() => { cancel.current?.focus(); }, []);
  const state = useApprovalStatus(workspace, nonce, true);
  const status = state.state === 'ready' ? state.data : null;
  const canApprove = status !== null && status.status === 'required' && status.can_approve && status.fingerprint !== null && !busy;
  const approve = async () => {
    if (!canApprove || status.fingerprint === null) return;
    setBusy(true);
    setFailure(null);
    setNotice(null);
    const result = await ex.approveConfiguration(status.fingerprint);
    setBusy(false);
    if (result.approved) { onApproved(); return; }
    if (result.code === 'APPROVAL_STALE') { setNotice(STALE_MESSAGE); setNonce((n) => n + 1); return; } // reload what is now there; approving it needs another explicit click
    setFailure({ code: result.code ?? 'HOST_ERROR', message: result.message ?? 'Kriya did not approve the configuration.', reason: result.reason });
  };
  const name = ws.current?.facts.name ?? '';
  return (
    <div className="ck-modal-backdrop">
      <div className="ck-modal" role="dialog" aria-modal="true" aria-label="Configuration approval" onKeyDown={(e) => { if (e.key === 'Escape' && !busy) { e.stopPropagation(); onClose(); } }}>
        <h2>Configuration approval</h2>
        <div>Project: <strong>{name}</strong>{workspace ? <> <span className="muted mono small" title={workspace}>{workspace}</span></> : null}</div>
        <p>Kriya requires approval for operator-controlled configuration used by this project.</p>
        <p className="muted small" data-testid="approval-meaning">These settings come from configuration files, not from Kriya&apos;s defaults. Approving lets Kriya use them for runs in this project, including the model server address and any tools or programs they name. You are the operator: approve only if you trust this configuration.</p>
        {state.state === 'loading' ? <p className="muted" role="status">Reading the configuration…</p> : null}
        {state.state !== 'loading' && state.state !== 'ready' ? (
          <div className="ck-state" role="alert"><strong>The configuration could not be read</strong>{state.state === 'error' ? `${state.message} (${state.code})` : 'Kriya did not answer.'}
            <div><button type="button" onClick={() => setNonce((n) => n + 1)}>Try again</button></div></div>
        ) : null}
        {notice ? <div className="ck-state" role="alert"><strong>{notice}</strong></div> : null}
        {status ? (
          <>
            {status.status === 'approved' ? <div className="ck-state" role="status"><strong>This configuration is already approved.</strong> Close this window to continue.</div> : null}
            {status.status === 'not_required' ? <div className="ck-state" role="status"><strong>Nothing in this configuration needs approval.</strong></div> : null}
            {status.status === 'unavailable' ? <div className="ck-state" role="alert"><strong>An approval cannot be given from this application.</strong>{' '}{status.blocking_reason}{status.reason && REASON_HELP[status.reason] ? <div className="muted small">{REASON_HELP[status.reason]}</div> : null}</div> : null}
            {status.protected_fields.length ? (
              <>
                <h3 className="ck-action-sub">Protected settings</h3>
                <ul aria-label="Protected settings" style={{ margin: '2px 0' }}>{status.protected_fields.map((f) => <li key={f.field}><span className="mono">{f.field}</span> <span className="muted small">· {SOURCE_WORDS[f.source] ?? f.source}</span></li>)}</ul>
                <div className="muted small">Only the names of the settings are shown; their values are not displayed. A name does not describe what a setting does, so open the project&apos;s kriya.yaml in your editor if you want to read the values first.</div>
                <div>Configuration fingerprint: <span className="mono" title={status.fingerprint ?? ''}>{short(status.fingerprint)}</span></div>
              </>
            ) : null}
            <p data-testid="approval-scope">This approval applies only to the current protected configuration. If those settings change, Kriya will require approval again.</p>
            <details>
              <summary>Technical details</summary>
              <KeyValue items={[
                ['Full fingerprint', <span key="f" className="mono">{status.fingerprint ?? 'none'}</span>],
                ['Status', `${status.status}${status.reason ? ` (${status.reason})` : ''}`],
                ['Scope', status.scope],
                ['Project identity', <span key="i" className="mono">{status.identity}</span>],
                ...status.protected_fields.map((f): [string, string] => [f.field, `${f.classification} · source ${f.source}`]),
              ]} />
              <p className="muted small">Kriya records this approval exactly as the command-line approval does, in its own store outside the project folder.</p>
            </details>
          </>
        ) : null}
        {failure ? (
          <div className="ck-state" role="alert">
            <strong>The configuration was not approved</strong> {failure.message} <span className="muted small">({failure.code}{failure.reason ? `: ${failure.reason}` : ''})</span>
            {failure.reason && REASON_HELP[failure.reason] ? <div className="muted small">{REASON_HELP[failure.reason]}</div> : null}
          </div>
        ) : null}
        <div className="ck-modal-actions">
          <button type="button" className="ck-primary" disabled={!canApprove} onClick={() => { void approve(); }}>{busy ? 'Approving…' : 'Approve Configuration'}</button>
          <button type="button" ref={cancel} disabled={busy} onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

/**
 * ACTION REQUIRED: Kriya will not run until this folder's operator-controlled configuration is approved. The card replaces the generic blocker for that one code and
 * offers the review; the approval itself is Kriya's (see ReviewDialog). `onApproved` lets the page keep a short confirmation after the card is gone.
 */
export function ConfigApprovalCard({ onDetails, onApproved }: { onDetails(): void; onApproved(): void }) {
  const ex = useExecution();
  const workspace = useWorkspacePath();
  const [open, setOpen] = useState(false);
  const [justApproved, setJustApproved] = useState(false);
  const count = useApprovalStatus(workspace, 0, true);
  const settings = count.state === 'ready' && count.data.protected_fields.length ? count.data.protected_fields.length : null;
  if (justApproved && ex.preflightStale) {
    return <section className="ck-state ck-action" aria-label="Configuration approval"><div role="status"><strong>✓ Configuration approved</strong> <span className="muted">Checking that this project is ready…</span></div></section>;
  }
  return (
    <section className="ck-state ck-action" aria-label="Configuration approval">
      <div className="ck-action-head"><span className="ck-action-flag">ACTION REQUIRED</span><strong>Configuration approval required</strong></div>
      <p style={{ margin: '4px 0' }}>This project&apos;s configuration uses {settings === null ? 'operator-controlled settings' : `${settings} operator-controlled setting${settings === 1 ? '' : 's'}`}. Review and approve the current configuration before starting Kriya.</p>
      <div className="ck-runbar-head">
        <button type="button" className="ck-primary ck-cta" onClick={() => setOpen(true)}>Review &amp; Approve</button>
        <button type="button" className="ck-link" onClick={onDetails}>View configuration</button>
        <Badge tone="warn">Needs your approval</Badge>
      </div>
      {open ? <ReviewDialog onClose={() => { setOpen(false); ex.refreshPreflight(); }} onApproved={() => { setOpen(false); setJustApproved(true); onApproved(); }} /> : null}
    </section>
  );
}
