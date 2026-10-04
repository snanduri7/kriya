import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';

export interface VirtualListProps<T> {
  items: readonly T[];
  rowHeight: number;
  height: number;
  selectedIndex: number | null;
  onSelect: (index: number) => void;
  renderRow: (item: T, index: number, selected: boolean) => ReactNode;
  getKey: (item: T, index: number) => string;
  ariaLabel: string;
  emptyText?: string;
  overscan?: number;
}

/** Fixed-row-height virtualized listbox with full keyboard navigation (P-21, P-35). No dependencies. */
export function VirtualList<T>({ items, rowHeight, height, selectedIndex, onSelect, renderRow, getKey, ariaLabel, emptyText = 'nothing recorded', overscan = 6 }: VirtualListProps<T>) {
  const ref = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const total = items.length * rowHeight;
  const first = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
  const last = Math.min(items.length, Math.ceil((scrollTop + height) / rowHeight) + overscan);

  useEffect(() => {
    const el = ref.current;
    if (!el || selectedIndex === null) return;
    const top = selectedIndex * rowHeight;
    if (top < el.scrollTop) el.scrollTop = top;
    else if (top + rowHeight > el.scrollTop + height) el.scrollTop = top + rowHeight - height;
  }, [selectedIndex, rowHeight, height]);

  const onKeyDown = useCallback((e: KeyboardEvent<HTMLDivElement>) => {
    if (!items.length) return;
    const current = selectedIndex ?? -1;
    let next: number | null = null;
    if (e.key === 'ArrowDown') next = Math.min(items.length - 1, current + 1);
    else if (e.key === 'ArrowUp') next = Math.max(0, current - 1);
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = items.length - 1;
    else if (e.key === 'PageDown') next = Math.min(items.length - 1, current + Math.floor(height / rowHeight));
    else if (e.key === 'PageUp') next = Math.max(0, current - Math.floor(height / rowHeight));
    if (next !== null) { e.preventDefault(); onSelect(next); }
  }, [items.length, selectedIndex, onSelect, height, rowHeight]);

  const rows: ReactNode[] = [];
  for (let i = first; i < last; i++) {
    const item = items[i] as T;
    const selected = i === selectedIndex;
    rows.push(
      <div
        key={getKey(item, i)}
        role="option"
        aria-selected={selected}
        aria-posinset={i + 1}
        aria-setsize={items.length}
        className={`vrow${selected ? ' selected' : ''}`}
        style={{ transform: `translateY(${i * rowHeight}px)`, height: rowHeight }}
        onClick={() => onSelect(i)}
      >
        {renderRow(item, i, selected)}
      </div>,
    );
  }

  return (
    <div
      ref={ref}
      role="listbox"
      aria-label={ariaLabel}
      aria-activedescendant={undefined}
      tabIndex={0}
      className="vlist"
      style={{ height }}
      onScroll={(e) => setScrollTop((e.target as HTMLDivElement).scrollTop)}
      onKeyDown={onKeyDown}
    >
      {items.length === 0 ? <div className="vempty">{emptyText}</div> : <div className="vinner" style={{ height: total }}>{rows}</div>}
    </div>
  );
}
