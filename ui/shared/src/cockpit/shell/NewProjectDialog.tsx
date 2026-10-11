import { useEffect, useRef, useState } from 'react';
import { useWorkspace, type NewProjectDraft } from '../core';

/** Kriya's own refusal reasons for creating a project folder, in plain words. The code and Kriya's message always remain visible; this only adds what to do next. */
const NEXT: Record<string, string> = {
  already_exists: 'Choose a different name, or open that folder with Open Folder.',
  invalid_name: 'Use a plain folder name: no slash or colon, no leading dot, no leading or trailing space, at most 100 characters.',
  permission_denied: 'Kriya cannot write in that location. Choose another parent folder.',
  parent_not_found: 'The parent folder is gone. Choose it again.',
  create_failed: 'The folder could not be created. Try another location.',
};

/** New Project: where it will be created (chosen in the native chooser), a name, and Create. Kriya validates the name and makes the one empty folder; nothing else is written. */
export function NewProjectDialog() {
  const draft = useWorkspace().project;
  // keyed on the parent choice: choosing another parent starts a fresh form (an empty name) without any state-reset effect
  return draft ? <ProjectForm key={draft.token} draft={draft} /> : null;
}

function ProjectForm({ draft }: { draft: NewProjectDraft }) {
  const ws = useWorkspace();
  const [name, setName] = useState('');
  const input = useRef<HTMLInputElement | null>(null);
  useEffect(() => { input.current?.focus(); }, []);
  const empty = name.length === 0;
  const submit = () => { if (!empty && !draft.busy) void ws.createProject(name); };
  return (
    <div className="ck-modal-backdrop">
      <div className="ck-modal" role="dialog" aria-modal="true" aria-label="New Project" onKeyDown={(e) => { if (e.key === 'Escape' && !draft.busy) { e.stopPropagation(); ws.cancelProject(); } }}>
        <h2>New Project</h2>
        <p className="muted small">Kriya creates a new empty folder for the project. Nothing else is written; your goal drives what gets generated.</p>
        <div>Create in <span className="mono" title={draft.parent}>{draft.parent}</span>{' '}
          <button type="button" className="ck-quiet" disabled={draft.busy} onClick={() => void ws.beginProject()}>Change…</button>
        </div>
        <label htmlFor="ck-project-name" className="ck-label">Project name</label>
        <input id="ck-project-name" ref={input} value={name} maxLength={100} autoComplete="off" spellCheck={false} disabled={draft.busy}
          aria-invalid={draft.error ? true : undefined} aria-describedby={draft.error ? 'ck-project-error' : undefined}
          onChange={(e) => setName(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); submit(); } }} />
        {draft.error ? (
          <div id="ck-project-error" className="ck-state" role="alert">
            <strong>The project was not created</strong>
            {draft.error.message} <span className="muted small">({draft.error.code}{draft.error.reason ? `: ${draft.error.reason}` : ''})</span>
            {draft.error.reason && NEXT[draft.error.reason] ? <div className="muted small">{NEXT[draft.error.reason]}</div> : null}
            {draft.error.code === 'PARENT_EXPIRED' ? <div className="muted small">Choose the parent folder again.</div> : null}
          </div>
        ) : null}
        <div className="ck-modal-actions">
          <button type="button" className="ck-primary" disabled={empty || draft.busy} onClick={submit}>{draft.busy ? 'Creating…' : 'Create project'}</button>
          <button type="button" disabled={draft.busy} onClick={ws.cancelProject}>Cancel</button>
        </div>
      </div>
    </div>
  );
}
