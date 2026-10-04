/** Imports that would tie ui/shared to a host (gate A-2, P-R1). Used by the ESLint rule AND the dependency check. */
export const NODE_BUILTINS = ['assert', 'async_hooks', 'buffer', 'child_process', 'cluster', 'console', 'constants', 'crypto', 'dgram', 'diagnostics_channel', 'dns', 'domain', 'events', 'fs', 'http', 'http2', 'https', 'inspector', 'module', 'net', 'os', 'path', 'perf_hooks', 'process', 'punycode', 'querystring', 'readline', 'repl', 'stream', 'string_decoder', 'sys', 'timers', 'tls', 'trace_events', 'tty', 'url', 'util', 'v8', 'vm', 'wasi', 'worker_threads', 'zlib'];
export const FORBIDDEN_IMPORT_PATTERNS = ['electron', 'electron/*', '@electron/*', 'node:*', ...NODE_BUILTINS, ...NODE_BUILTINS.map((m) => `${m}/*`)];
/** One regex over the BARE import specifier (a relative './panels/Inspector' never matches): electron, @electron/*, node:*, every Node built-in. */
export const FORBIDDEN_IMPORT_REGEX = `^(node:.*|electron(/.*)?|@electron/.*|(${NODE_BUILTINS.join('|')})(/.*)?)$`;
/** Dependencies ui/shared/package.json may declare (runtime). Anything else fails the check. */
export const ALLOWED_SHARED_DEPENDENCIES = ['react', 'react-dom'];
