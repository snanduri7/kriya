import { hasData, type Availability } from './availability';

/** Why a catalog read has nothing to show yet (loading, a typed error, an unavailable state), or null when it has data. Kriya's own words, verbatim. */
export function whyNot<T>(a: Availability<T>, what: string): string | null {
  switch (a.state) {
    case 'loading': return `Asking Kriya about ${what}…`;
    case 'error': return `${what} could not be read: ${a.message} (${a.code})`;
    case 'unavailable':
    case 'not_recorded': return a.detail;
    default: return hasData(a) ? null : `${what} could not be shown.`;
  }
}

/** The envelope's own "this folder's configuration could not be resolved" answer, shown instead of an empty list so it is never mistaken for "none". */
export function UnavailableConfiguration({ reason }: { reason: { code: string; message: string } | null }) {
  return <p className="muted" role="status">Kriya could not resolve this folder&apos;s configuration{reason ? `: ${reason.message} (${reason.code})` : ''}. Nothing is listed until it loads.</p>;
}

/** A field the backend cannot know is shown as such, with the reason, never as an empty value that looks like "none". */
export const NOT_KNOWN = 'Not known to Kriya without starting it';

export function UnknownFields({ names, explain }: { names: readonly string[]; explain: Record<string, string> }) {
  if (names.length === 0) return null;
  return (
    <p className="muted small" role="note">
      Not known: {names.map((n) => explain[n] ?? n).join('; ')}. Kriya does not guess these.
    </p>
  );
}
