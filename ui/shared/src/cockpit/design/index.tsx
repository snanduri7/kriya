/** DesignSystem: tokens (cockpit.css) and the few primitives every module uses. No feature knowledge lives here. */
import type { ReactNode } from 'react';
import { availabilityTitle, type Availability } from '../core/availability';
import './cockpit.css';

export type Tone = 'ok' | 'bad' | 'warn' | 'info' | 'neutral';

export function Badge({ tone = 'neutral', children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return <span className={`ck-badge ${tone === 'neutral' ? '' : tone}`} title={title}>{children}</span>;
}

export function Panel({ title, children }: { title?: string; children: ReactNode }) {
  return <section className="ck-panel">{title ? <h3>{title}</h3> : null}{children}</section>;
}

export function KeyValue({ items }: { items: ReadonlyArray<readonly [string, ReactNode]> }) {
  return <dl className="ck-kv">{items.map(([k, v]) => <div key={k} style={{ display: 'contents' }}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>;
}

export interface TabItem { id: string; label: string }
export function TabBar({ tabs, active, onSelect, label }: { tabs: readonly TabItem[]; active: string; onSelect(id: string): void; label: string }) {
  return (
    <div className="ck-tabbar" role="tablist" aria-label={label}>
      {tabs.map((t) => <button key={t.id} role="tab" type="button" aria-selected={t.id === active} onClick={() => onSelect(t.id)}>{t.label}</button>)}
    </div>
  );
}

/** Renders every non-ready state with its own title and the reason Kriya gave; a ready state renders `children`. */
export function AvailabilityView<T>({ value, children }: { value: Availability<T>; children(data: T, notes: readonly string[]): ReactNode }) {
  switch (value.state) {
    case 'loading': return <div className="ck-state muted" role="status">Loading…</div>;
    case 'ready': return <>{children(value.data, [])}</>;
    case 'degraded': return <>{value.reasons.map((r) => <div key={r} className="ck-state" role="note"><strong>{availabilityTitle(value)}</strong>{r}</div>)}{children(value.data, value.reasons)}</>;
    case 'error': return <div className="ck-state" role="alert"><strong>{availabilityTitle(value)}</strong>{value.message}</div>;
    case 'not_recorded': return <div className="ck-state"><strong>{availabilityTitle(value)}</strong>{value.detail}</div>;
    case 'unavailable': return <div className="ck-state"><strong>{availabilityTitle(value)}</strong>{value.detail}</div>;
  }
}

/** Shown whenever the data does not come from Kriya, so fixture data can never be mistaken for evidence. */
export function ProviderBanner({ kind }: { kind: 'kriya' | 'fixture' }) {
  return kind === 'fixture' ? <div className="ck-provider-banner" role="note">FIXTURE DATA - not Kriya evidence (development and tests only)</div> : null;
}

/** A module that has a place in the shell but no implementation in this build: it says so, instead of showing invented content. */
export function PendingView({ title, needs }: { title: string; needs: string }) {
  return <div className="ck-state"><strong>{title}: not in this build yet</strong>Needs: {needs}</div>;
}
