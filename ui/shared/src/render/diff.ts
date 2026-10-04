/** Line diff of two RECORDED texts (P-23: comparisons need recorded before/after). Myers-free O(n*m) LCS on
 * the middle after trimming the common prefix/suffix, bounded so a huge pair degrades to a block view
 * instead of freezing the main thread (P-35: no freeze over 200 ms). */
export type DiffOp = 'equal' | 'add' | 'del';
export interface DiffLine { op: DiffOp; before: number | null; after: number | null; text: string }

export const DIFF_DP_LIMIT = 3000; // lines per side in the DP middle; 3000x3000 Uint16 = 18 MB, ~60 ms

export function diffLines(before: string, after: string): { lines: DiffLine[]; exact: boolean } {
  const a = before.split('\n');
  const b = after.split('\n');
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length, endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) { endA--; endB--; }
  const out: DiffLine[] = [];
  for (let i = 0; i < start; i++) out.push({ op: 'equal', before: i + 1, after: i + 1, text: a[i] ?? '' });
  const midA = a.slice(start, endA), midB = b.slice(start, endB);
  let exact = true;
  if (midA.length > DIFF_DP_LIMIT || midB.length > DIFF_DP_LIMIT) {
    exact = false;
    midA.forEach((t, i) => out.push({ op: 'del', before: start + i + 1, after: null, text: t }));
    midB.forEach((t, i) => out.push({ op: 'add', before: null, after: start + i + 1, text: t }));
  } else {
    const n = midA.length, m = midB.length;
    const w = m + 1;
    const dp = new Uint16Array((n + 1) * (m + 1));
    for (let i = n - 1; i >= 0; i--) {
      for (let j = m - 1; j >= 0; j--) {
        dp[i * w + j] = midA[i] === midB[j] ? (dp[(i + 1) * w + j + 1] ?? 0) + 1 : Math.max(dp[(i + 1) * w + j] ?? 0, dp[i * w + j + 1] ?? 0);
      }
    }
    let i = 0, j = 0;
    while (i < n && j < m) {
      if (midA[i] === midB[j]) { out.push({ op: 'equal', before: start + i + 1, after: start + j + 1, text: midA[i] ?? '' }); i++; j++; }
      else if ((dp[(i + 1) * w + j] ?? 0) >= (dp[i * w + j + 1] ?? 0)) { out.push({ op: 'del', before: start + i + 1, after: null, text: midA[i] ?? '' }); i++; }
      else { out.push({ op: 'add', before: null, after: start + j + 1, text: midB[j] ?? '' }); j++; }
    }
    while (i < n) { out.push({ op: 'del', before: start + i + 1, after: null, text: midA[i] ?? '' }); i++; }
    while (j < m) { out.push({ op: 'add', before: null, after: start + j + 1, text: midB[j] ?? '' }); j++; }
  }
  for (let k = 0; k < a.length - endA; k++) {
    out.push({ op: 'equal', before: endA + k + 1, after: endB + k + 1, text: a[endA + k] ?? '' });
  }
  return { lines: out, exact };
}
