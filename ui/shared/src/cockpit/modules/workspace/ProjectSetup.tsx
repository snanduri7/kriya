import { useState } from 'react';
import { Badge, useExecution } from '../../core';

const REASON_WORDS: Record<string, string> = {
  already_configured: 'This folder already has a Kriya configuration, so nothing was changed. Kriya never overwrites one.',
  unwritable: 'Kriya could not write to this folder. Check that you can create files in it, then try again.',
};

/**
 * Project Setup (recommended, never required): a folder with no configuration file already runs on Kriya's built-in defaults, so this is an offer, not a gate. It writes
 * nothing itself - "Create Project Configuration" asks the host, which asks Kriya, which writes its OWN recommended profile (the request carries no setting). Everything the
 * card says about models comes from what Kriya reports it would use (the readiness data); nothing is invented. The settings it creates are operator-controlled, so the
 * ordinary configuration approval follows: this card never approves anything.
 */
export function ProjectSetupCard({ model, fallbacks, onCreated }: { model: string | null; fallbacks: readonly string[]; onCreated(): void }) {
  const ex = useExecution();
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<{ code: string; message: string; reason: string | null } | null>(null);
  const create = async () => {
    if (busy) return;
    setBusy(true);
    setFailure(null);
    const result = await ex.initializeProject();
    setBusy(false);
    if (result.initialized) { onCreated(); return; }
    setFailure({ code: result.code ?? 'HOST_ERROR', message: result.message ?? 'Kriya did not create the configuration.', reason: result.reason });
  };
  return (
    <section className="ck-state ck-setup-card" aria-label="Project setup" data-testid="project-setup">
      <div className="ck-action-head"><strong>Set up this project</strong><Badge tone="info">Recommended</Badge></div>
      <p style={{ margin: '4px 0' }}>Kriya can already work in this folder with its built-in defaults. Setting it up applies the configuration Kriya recommends for local models.</p>
      <dl className="ck-setup-facts" aria-label="What setup applies">
        <div><dt className="muted small">Model</dt><dd>{model ?? 'the default model'}</dd></div>
        <div><dt className="muted small">Fallback</dt><dd>{fallbacks.length ? fallbacks.join(', ') : 'none'}</dd></div>
        <div><dt className="muted small">Execution</dt><dd>Local</dd></div>
        <div><dt className="muted small">Network</dt><dd>Local only; live web lookup off</dd></div>
        <div><dt className="muted small">Changes</dt><dd>Held for my approval</dd></div>
      </dl>
      <details className="ck-tech">
        <summary>Advanced configuration</summary>
        <p className="muted small" data-testid="setup-advanced">Setup creates a <span className="mono">kriya.yaml</span> in this folder: the governed workflow is switched on, changes wait for your approval, live web lookup is off, and the model settings are Kriya&apos;s own defaults. You will review these settings and approve them before Kriya uses them. You can edit the file afterwards; changing a protected setting asks for approval again.</p>
      </details>
      {failure ? <div className="ck-state" role="alert"><strong>The project was not set up</strong> {failure.reason && REASON_WORDS[failure.reason] ? REASON_WORDS[failure.reason] : failure.message} <span className="muted small">({failure.code}{failure.reason ? `: ${failure.reason}` : ''})</span></div> : null}
      <div className="ck-runbar-head">
        <button type="button" className="ck-primary" disabled={busy} onClick={() => { void create(); }}>{busy ? 'Creating…' : 'Create Project Configuration'}</button>
      </div>
    </section>
  );
}
