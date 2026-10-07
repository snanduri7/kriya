import { useEffect, useRef, useState } from 'react';
import type { HostAdapter, HostSettings } from '../host/HostAdapter';

const DEFAULTS: HostSettings = { kriyaExecutable: null, configDirectory: null, workspacePath: null, editor: 'vscode' };
const FIELDS = [
  { key: 'kriyaExecutable', label: 'Kriya executable', help: 'Absolute executable path. Saving does not enable real-store access or lift matrix protection.' },
  { key: 'configDirectory', label: 'Configuration directory', help: 'Where Kriya discovers kriya.yaml. Leave blank to use your home directory.' },
  { key: 'workspacePath', label: 'Recovery workspace', help: 'Used for recovery assessment and opening files. Separate from the configuration directory; blank means no workspace.' },
] as const;

/** Settings use only the existing HostAdapter; saving never acquires a snapshot. Each setting saves independently. */
export function SettingsPanel({ host, onSaved, onClose }: { host: HostAdapter; onSaved: (key: keyof HostSettings) => void; onClose: () => void }) {
  const [values, setValues] = useState<HostSettings>(DEFAULTS);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<keyof HostSettings | null>(null);
  // The error names the field it belongs to, so that field can be marked invalid and described by the message.
  const [error, setError] = useState<{ key: keyof HostSettings; message: string } | null>(null);
  const [notice, setNotice] = useState('');
  const heading = useRef<HTMLHeadingElement>(null);
  // Initial focus: the dialog title, so the dialog is announced by name before the first control (the background is inert).
  useEffect(() => { heading.current?.focus(); }, []);
  const invalid = (key: keyof HostSettings) => error?.key === key;
  const describedBy = (key: keyof HostSettings, helpId: string | null) => [helpId, invalid(key) ? 'settings-error' : null].filter(Boolean).join(' ') || undefined;
  useEffect(() => {
    let active = true;
    void Promise.all([
      host.getSetting('kriyaExecutable'), host.getSetting('configDirectory'),
      host.getSetting('workspacePath'), host.getSetting('editor'),
    ]).then(([kriyaExecutable, configDirectory, workspacePath, editor]) => {
      if (active) { setValues({ kriyaExecutable: kriyaExecutable ?? null, configDirectory: configDirectory ?? null, workspacePath: workspacePath ?? null, editor: editor ?? 'vscode' }); setLoading(false); }
    }).catch((e: unknown) => { if (active) { setError({ key: 'kriyaExecutable', message: e instanceof Error ? e.message : String(e) }); setLoading(false); } });
    return () => { active = false; };
  }, [host]);

  async function save(key: keyof HostSettings) {
    setError(null); setNotice('');
    const value = values[key];
    if (key !== 'editor' && value !== null && (!/^(\/|[A-Za-z]:[\\/]|\\\\)/.test(value) || Array.from(value).some((c) => c.charCodeAt(0) < 32 || c.charCodeAt(0) === 127))) {
      setError({ key, message: 'Enter an absolute path without control characters, or leave the field blank.' }); return;
    }
    setSaving(key);
    try {
      await host.setSetting(key, value);
      onSaved(key);
      setNotice('Saved. No snapshot was acquired.');
    } catch (e) { setError({ key, message: e instanceof Error ? e.message : String(e) }); }
    finally { setSaving(null); }
  }

  return <section className="settings-panel" role="dialog" aria-modal="true" aria-labelledby="settings-heading" onKeyDown={(e) => {
    if (e.key === 'Escape' && saving === null) { e.preventDefault(); onClose(); }
    if (e.key === 'Tab') {
      const controls = Array.from(e.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled)'));
      const first = controls[0], last = controls[controls.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
    }
  }}>
    <div className="settings-heading"><h2 id="settings-heading" ref={heading} tabIndex={-1}>Settings</h2><button type="button" onClick={onClose} disabled={saving !== null}>Close settings</button></div>
    <p className="muted">Save each setting separately. Changing a path clears the displayed session. These settings do not change Kriya permissions.</p>
    {loading ? <p role="status">Loading settings…</p> : <fieldset disabled={saving !== null}>
      <legend className="sr-only">Connection and editor settings</legend>
      {FIELDS.map(({ key, label, help }) => <div className="settings-field" key={key}>
        <label htmlFor={`setting-${key}`}>{label}</label>
        <p id={`help-${key}`} className="muted">{help}</p>
        <div className="settings-input"><input id={`setting-${key}`} aria-describedby={describedBy(key, `help-${key}`)} aria-invalid={invalid(key) || undefined} type="text" value={values[key] ?? ''} onChange={(e) => setValues((v) => ({ ...v, [key]: e.target.value === '' ? null : e.target.value }))} autoComplete="off" spellCheck={false} />
          <button type="button" onClick={() => void save(key)}>Save {label.toLowerCase()}</button></div>
      </div>)}
      <div className="settings-field"><label htmlFor="setting-editor">Preferred editor</label>
        <div className="settings-input"><select id="setting-editor" aria-describedby={describedBy('editor', null)} aria-invalid={invalid('editor') || undefined} value={values.editor} onChange={(e) => setValues((v) => ({ ...v, editor: e.target.value as HostSettings['editor'] }))}>
          <option value="vscode">VS Code</option><option value="intellij">IntelliJ IDEA</option><option value="eclipse">Eclipse</option>
        </select><button type="button" onClick={() => void save('editor')}>Save preferred editor</button></div>
        <p className="muted">Availability and navigation support depend on the editor installed on your machine.</p>
      </div>
    </fieldset>}
    {error ? <p id="settings-error" role="alert">{error.message}</p> : null}
    <p role="status" aria-live="polite">{saving ? 'Saving…' : notice}</p>
  </section>;
}
