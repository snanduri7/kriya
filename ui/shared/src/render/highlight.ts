/** A small, dependency-free syntax highlighter for diff lines (comments, strings, numbers, keywords). Output is tokens of TEXT; a caller renders
 * them as text nodes, never HTML. Deliberately approximate: it colours, it never rewrites or drops a character. */
export type TokenClass = 'plain' | 'comment' | 'string' | 'number' | 'keyword';
export interface Token { text: string; cls: TokenClass }

const C_LIKE = ['abstract', 'break', 'case', 'catch', 'class', 'const', 'continue', 'default', 'do', 'else', 'enum', 'export', 'extends', 'final', 'finally', 'for', 'function', 'if', 'implements', 'import', 'instanceof', 'interface', 'let', 'new', 'package', 'private', 'protected', 'public', 'return', 'static', 'super', 'switch', 'this', 'throw', 'throws', 'try', 'var', 'void', 'while', 'int', 'long', 'boolean', 'null', 'true', 'false'];
const PYTHON = ['and', 'as', 'assert', 'async', 'await', 'class', 'def', 'del', 'elif', 'else', 'except', 'finally', 'for', 'from', 'global', 'if', 'import', 'in', 'is', 'lambda', 'None', 'nonlocal', 'not', 'or', 'pass', 'raise', 'return', 'try', 'while', 'with', 'yield', 'True', 'False'];
const GO_RUST = ['fn', 'func', 'let', 'mut', 'match', 'struct', 'impl', 'trait', 'pub', 'use', 'mod', 'type', 'var', 'const', 'package', 'import', 'return', 'if', 'else', 'for', 'range', 'go', 'defer', 'nil', 'true', 'false'];

interface Language { keywords: ReadonlySet<string>; lineComment: string | null }
const LANGS: Record<string, Language> = {
  c: { keywords: new Set(C_LIKE), lineComment: '//' },
  py: { keywords: new Set(PYTHON), lineComment: '#' },
  go: { keywords: new Set(GO_RUST), lineComment: '//' },
  plain: { keywords: new Set(), lineComment: null },
};
const BY_EXTENSION: Record<string, keyof typeof LANGS> = { java: 'c', js: 'c', jsx: 'c', ts: 'c', tsx: 'c', kt: 'c', c: 'c', h: 'c', cc: 'c', cpp: 'c', cs: 'c', py: 'py', sh: 'py', yaml: 'py', yml: 'py', go: 'go', rs: 'go' };

export function languageOf(path: string | null): keyof typeof LANGS {
  const ext = path?.split('.').pop()?.toLowerCase() ?? '';
  return BY_EXTENSION[ext] ?? 'plain';
}

export function highlightLine(line: string, language: keyof typeof LANGS): Token[] {
  const lang = LANGS[language] ?? LANGS.plain!;
  const out: Token[] = [];
  const push = (text: string, cls: TokenClass) => { if (text) out.push({ text, cls }); };
  let i = 0;
  let plain = '';
  const flush = () => { push(plain, 'plain'); plain = ''; };
  while (i < line.length) {
    if (lang.lineComment && line.startsWith(lang.lineComment, i)) { flush(); push(line.slice(i), 'comment'); return out; }
    const ch = line[i]!;
    if (ch === '"' || ch === "'") {
      let j = i + 1;
      while (j < line.length && line[j] !== ch) j += line[j] === '\\' ? 2 : 1;
      flush(); push(line.slice(i, Math.min(j + 1, line.length)), 'string'); i = j + 1; continue;
    }
    if (/[0-9]/.test(ch) && (i === 0 || !/[A-Za-z_]/.test(line[i - 1]!))) {
      let j = i + 1;
      while (j < line.length && /[0-9a-fA-FxX_.]/.test(line[j]!)) j++;
      flush(); push(line.slice(i, j), 'number'); i = j; continue;
    }
    if (/[A-Za-z_]/.test(ch)) {
      let j = i + 1;
      while (j < line.length && /[A-Za-z0-9_]/.test(line[j]!)) j++;
      const word = line.slice(i, j);
      if (lang.keywords.has(word)) { flush(); push(word, 'keyword'); } else plain += word;
      i = j; continue;
    }
    plain += ch; i++;
  }
  flush();
  return out;
}
