/**
 * Electron fuses, for the SHIPPED product (independent review P2-2). A fuse is a byte in the "fuse wire" inside the Electron Framework binary: the sentinel,
 * a schema version, a length, then one ASCII '0'/'1' per fuse. The stock bundle ships RunAsNode, NODE_OPTIONS and --inspect ENABLED, which would let anything
 * that can set the app's environment or arguments run arbitrary code (and drive the app's privileged IPC) from outside the app's own code. The product turns
 * those three off; an evidence package keeps the stock fuses (its capture driver needs a debugger-free path, but the dev drivers are the evidence-only code).
 * Pure functions over a Buffer so the rules are unit-tested without a real framework binary.
 */
export const FUSE_SENTINEL = 'dL7pKGdnNz796PbbjQWNKmHXBZaB9tsX';
/** Fuse schema v1 positions (electron/fuses). Only the ones this product must have OFF are listed. */
export const FUSE_INDEX = { RunAsNode: 0, EnableNodeOptionsEnvironmentVariable: 2, EnableNodeCliInspectArguments: 3 };
export const PRODUCT_OFF = ['RunAsNode', 'EnableNodeOptionsEnvironmentVariable', 'EnableNodeCliInspectArguments'];

/** Locate the wire. Returns { start, length, version } or throws: a binary without exactly one wire is never patched or accepted. */
export function locateWire(buffer) {
  const needle = Buffer.from(FUSE_SENTINEL);
  const first = buffer.indexOf(needle);
  if (first < 0) throw new Error('fuse wire not found in the framework binary');
  if (buffer.indexOf(needle, first + 1) >= 0) throw new Error('more than one fuse wire sentinel in the framework binary');
  const version = buffer[first + needle.length];
  const length = buffer[first + needle.length + 1];
  const start = first + needle.length + 2;
  if (version !== 1) throw new Error(`unsupported fuse schema version ${version}`);
  if (length < 4 || start + length > buffer.length) throw new Error('fuse wire length is implausible');
  for (let i = 0; i < length; i += 1) if (buffer[start + i] !== 0x30 && buffer[start + i] !== 0x31) throw new Error('fuse wire holds a byte that is neither 0 nor 1');
  return { start, length, version };
}

export function readFuses(buffer) {
  const { start, length } = locateWire(buffer);
  const out = {};
  for (const [name, index] of Object.entries(FUSE_INDEX)) out[name] = index < length ? buffer[start + index] === 0x31 : null;
  return out;
}

/** Returns a patched COPY with the product's fuses off. */
export function applyProductFuses(buffer) {
  const copy = Buffer.from(buffer);
  const { start } = locateWire(copy);
  for (const name of PRODUCT_OFF) copy[start + FUSE_INDEX[name]] = 0x30;
  return copy;
}

/** Findings for a framework binary that must be a product build (empty = every listed fuse is off). */
export function productFuseFindings(buffer) {
  const state = readFuses(buffer);
  return PRODUCT_OFF.filter((name) => state[name] !== false).map((name) => `fuse ${name} is ${state[name] === null ? 'missing' : 'ON'} in the product framework`);
}

export const FRAMEWORK_BINARY = ['Contents', 'Frameworks', 'Electron Framework.framework', 'Versions', 'A', 'Electron Framework'];
