/**
 * Defense-in-depth text rendering (P-32). Output is text, never HTML. Control
 * characters other than \n and \t are replaced by their visible escape so a
 * recorded value can neither hide content nor move the terminal/cursor.
 * Bidirectional controls (U+202A-202E, U+2066-2069, U+061C, U+200E/F) and zero-width characters (U+200B-200D, U+2060, U+FEFF) are escaped too: on the change-review
 * screen they could make displayed code read differently from what it is ("Trojan Source").
 */
// eslint-disable-next-line no-control-regex -- the sanitizer exists to match control characters (P-32)
const CONTROL = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F\u2028\u2029\u200B-\u200F\u202A-\u202E\u2060\u2066-\u2069\u061C\uFEFF]/g;

export function sanitizeText(value: unknown): string {
  const text = typeof value === 'string' ? value : safeStringify(value);
  return text.replace(CONTROL, (ch) => `\\u${ch.charCodeAt(0).toString(16).padStart(4, '0')}`);
}

export function safeStringify(value: unknown, indent = 2): string {
  if (value === undefined) return 'undefined';
  try {
    return JSON.stringify(value, null, indent) ?? 'null';
  } catch {
    return String(value);
  }
}

/** Bounded preview for very large payloads; the caller offers "show all" in chunks. */
export function previewText(text: string, limit: number): { text: string; truncated: boolean; total: number } {
  if (text.length <= limit) return { text, truncated: false, total: text.length };
  return { text: text.slice(0, limit), truncated: true, total: text.length };
}
