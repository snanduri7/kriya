export const FUSE_SENTINEL: string;
export const FUSE_INDEX: Record<string, number>;
export const PRODUCT_OFF: string[];
export const FRAMEWORK_BINARY: string[];
export function locateWire(buffer: Buffer): { start: number; length: number; version: number };
export function readFuses(buffer: Buffer): Record<string, boolean | null>;
export function applyProductFuses(buffer: Buffer): Buffer;
export function productFuseFindings(buffer: Buffer): string[];
