/** Request generations (P-32): a response is applied only if its generation is still current for its slot;
 * a failed refresh clears the slot's "current" status instead of leaving stale data looking fresh. */
export type Slot = 'capabilities' | 'list' | 'detail' | 'prompt' | 'status' | 'snapshots' | 'acquire' | 'verify';

export interface SlotState<T> {
  generation: number;
  value: T | null;
  observedAt: string | null;
  current: boolean;
  error: { code: string; message: string } | null;
  pending: boolean;
}

export function emptySlot<T>(): SlotState<T> {
  return { generation: 0, value: null, observedAt: null, current: false, error: null, pending: false };
}

export class GenerationCounter {
  private generations = new Map<Slot, number>();

  next(slot: Slot): number {
    const g = (this.generations.get(slot) ?? 0) + 1;
    this.generations.set(slot, g);
    return g;
  }

  isCurrent(slot: Slot, generation: number): boolean {
    return (this.generations.get(slot) ?? 0) === generation;
  }
}

export function startRequest<T>(slot: SlotState<T>, generation: number): SlotState<T> {
  return { ...slot, generation, pending: true };
}

/** Apply a successful response; a stale generation is discarded (returns the slot unchanged). */
export function applyResponse<T>(slot: SlotState<T>, generation: number, counter: GenerationCounter, name: Slot, value: T, observedAt: string): SlotState<T> {
  if (!counter.isCurrent(name, generation)) return slot;
  return { generation, value, observedAt, current: true, error: null, pending: false };
}

/** Apply a failure: the previous value may stay visible for reference but is no longer "current". */
export function applyFailure<T>(slot: SlotState<T>, generation: number, counter: GenerationCounter, name: Slot, error: { code: string; message: string }): SlotState<T> {
  if (!counter.isCurrent(name, generation)) return slot;
  return { ...slot, generation, current: false, error, pending: false };
}
