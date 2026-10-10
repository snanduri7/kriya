import { useCallback, useRef } from 'react';

/** A vertical splitter: drag, or focus and press the arrow keys. `sign` says which way growth goes (+1: dragging right grows the pane). */
export function ResizeHandle({ label, value, min, max, sign, onChange }: { label: string; value: number; min: number; max: number; sign: 1 | -1; onChange(next: number): void }) {
  const start = useRef<{ x: number; value: number } | null>(null);
  const clamp = useCallback((v: number) => Math.min(max, Math.max(min, Math.round(v))), [min, max]);
  return (
    <button
      type="button" className="ck-resize" role="separator" aria-orientation="vertical" aria-label={label} aria-valuenow={value} aria-valuemin={min} aria-valuemax={max}
      onPointerDown={(e) => { start.current = { x: e.clientX, value }; e.currentTarget.setPointerCapture?.(e.pointerId); }}
      onPointerMove={(e) => { if (start.current) onChange(clamp(start.current.value + sign * (e.clientX - start.current.x))); }}
      onPointerUp={() => { start.current = null; }}
      onKeyDown={(e) => {
        const step = e.shiftKey ? 40 : 10;
        if (e.key === 'ArrowLeft') { e.preventDefault(); onChange(clamp(value - sign * step)); }
        if (e.key === 'ArrowRight') { e.preventDefault(); onChange(clamp(value + sign * step)); }
        if (e.key === 'Home') { e.preventDefault(); onChange(min); }
        if (e.key === 'End') { e.preventDefault(); onChange(max); }
      }}
    />
  );
}
