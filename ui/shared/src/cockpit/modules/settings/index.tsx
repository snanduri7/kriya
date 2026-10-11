import { useEffect, useState } from 'react';
import { KeyValue, Panel, useProvider, type ModuleDefinition } from '../../core';

// The workspace is not a setting: it is chosen with Open Folder (the host only accepts folders the person chose).
const FIELDS = [['configDirectory', 'Configuration directory'], ['kriyaExecutable', 'Kriya executable']] as const;

/** Host-owned values are DISPLAYED only (the host refuses renderer writes to them); the one preference written here is the editor. Host settings only; the Kriya-owned content-visibility authority is never editable here (a host may only narrow it). */
function Settings() {
  const provider = useProvider();
  const [values, setValues] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState<string | null>(null);
  const [editor, setEditor] = useState<'vscode' | 'intellij' | 'eclipse'>('vscode');
  useEffect(() => {
    let live = true;
    void Promise.all(FIELDS.map(async ([key]) => [key, (await provider.getSetting(key)) ?? ''] as const)).then((rows) => { if (live) setValues(Object.fromEntries(rows)); });
    void provider.getSetting('editor').then((v) => { if (live && v) setEditor(v); });
    return () => { live = false; };
  }, [provider]);
  return (
    <Panel title="Settings">
      <KeyValue items={FIELDS.map(([key, label]) => [label, (
        <span key={key} aria-label={label}>
          <span className="mono">{values[key] || 'default'}</span> <span className="muted small">Managed by the host / operator; not editable here.</span>
        </span>
      )] as const)} />
      <KeyValue items={[['Editor for Open in editor', (
        <select key="editor" aria-label="Editor" value={editor} onChange={(e) => { const v = e.target.value as 'vscode' | 'intellij' | 'eclipse'; setEditor(v); void provider.setSetting('editor', v).then(() => setSaved('Editor')); }}>
          <option value="vscode">VS Code</option><option value="intellij">IntelliJ IDEA</option><option value="eclipse">Eclipse</option>
        </select>
      )] as const]} />
      {saved ? <p role="status" className="muted">{saved} saved.</p> : null}
    </Panel>
  );
}

export const settingsModule: ModuleDefinition = {
  id: 'settings',
  placement: { page: 'settings', surface: 'page', label: 'Settings', order: 10 },
  requiredCapabilities: [], optionalCapabilities: [], selectionDependencies: [],
  render: Settings,
};
