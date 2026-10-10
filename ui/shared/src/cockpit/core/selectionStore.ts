import { useSyncExternalStore } from 'react';
import { initialSelection, selectionReducer, type Selection, type SelectionAction } from '../../state/selection';

/** The one shared selection. It wraps the M1 reducer (extended additively), so modules never keep their own copy of what is selected. */
export interface SelectionStore {
  getState(): Selection;
  dispatch(action: SelectionAction): void;
  subscribe(listener: () => void): () => void;
}

export function createSelectionStore(initial: Selection = initialSelection): SelectionStore {
  let state = initial;
  const listeners = new Set<() => void>();
  return {
    getState: () => state,
    dispatch(action) {
      const next = selectionReducer(state, action);
      if (next === state) return;
      state = next;
      for (const listener of [...listeners]) listener();
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
  };
}

export function useSelectionState<T>(store: SelectionStore, select: (s: Selection) => T): T {
  return useSyncExternalStore(store.subscribe, () => select(store.getState()), () => select(store.getState()));
}
