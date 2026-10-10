import { useEffect, useState } from 'react';
import { KeyValue, Panel, useProvider, type ModuleDefinition } from '../../core';

const FIELDS = [['workspacePath', 'Workspace'], ['configDirectory', 'Configuration directory'], ['kriyaExecutable', 'Kriya executable']] as const;

/** Host settings only; the Kriya-owned content-visibility authority is never editable here (a host may only narrow it). */
function Settings() {
  const provider = useProvider();
  const [values, setValues] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    void Promise.all(FIELDS.map(async ([key]) => [key, (await provider.getSetting(key)) ?? ''] as const)).then((rows) => { if (live) setValues(Object.fromEntries(rows)); });
    return () => { live = false; };
  }, [provider]);
  return (
    <Panel title="Settings">
      <KeyValue items={FIELDS.map(([key, label]) => [label, (
        <span key={key}>
          <input aria-label={label} value={values[key] ?? ''} onChange={(e) => setValues({ ...values, [key]: e.target.value })} />{' '}
          <button type="button" onClick={() => void provider.setSetting(key, values[key] ? values[key]! : null as never).then(() => setSaved(label))}>Save</button>
        </span>
      )] as const)} />
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
