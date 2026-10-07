/** First-real-store acceptance, step 8 (representative panels): renders the shared App against REAL KUP envelopes exported
 * from the owner's store (ui/docs/POST_MATRIX_READINESS.md §7). The exports live OUTSIDE the repository; the directory is
 * named by KRIYA_REAL_EXPORT_DIR and holds capabilities.json, snapshot.list.json, acquire1.json, verify1.json,
 * history.list.page1.json (+ optional page2), history.detail.<run>.json and history.prompt.<run>.json. Without the variable
 * the file is skipped, so fixture-only CI is unchanged. No goal, prompt or output text is asserted or printed. */
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { App } from '@kriya-ui/shared';
import type { HostAdapter, HostInfo, HostSettings, KupEnvelope, KupRequest, OpenInIdeRequest, OpenInIdeResult, RunDetail, RunSummary } from '@kriya-ui/shared';

const DIR = process.env.KRIYA_REAL_EXPORT_DIR ?? '';
const load = (name: string) => JSON.parse(readFileSync(join(DIR, name), 'utf8')) as KupEnvelope;
const files = (re: RegExp) => (DIR ? readdirSync(DIR).filter((f: string) => re.test(f)).sort() : []);
const describeReal = DIR && existsSync(join(DIR, 'history.list.page1.json')) ? describe : describe.skip;

/** Answers every KUP request from the exported envelopes; nothing is synthesized. */
class ExportHost implements HostAdapter {
  calls: KupRequest[] = [];
  private byRun = new Map<string, KupEnvelope>();
  private list1 = load('history.list.page1.json');
  private list2 = existsSync(join(DIR, 'history.list.page2.json')) ? load('history.list.page2.json') : null;
  private prompt = files(/^history\.prompt\..+\.json$/).map((f: string) => load(f))[0] ?? null;
  constructor() { for (const f of files(/^history\.detail\..+\.json$/)) { const e = load(f); if (!e.error) this.byRun.set((e.data as RunDetail).run.run_id, e); } }
  private refuse(req: KupRequest, message: string): KupEnvelope { return { ...this.list1, operation: req.operation, source: null, consistency: null, data: null, error: { code: 'INVALID_REQUEST', message } } as unknown as KupEnvelope; }
  async query(req: KupRequest): Promise<KupEnvelope> {
    this.calls.push(req);
    switch (req.operation) {
      case 'capabilities': return load('capabilities.json');
      case 'snapshot.list': return load('snapshot.list.json');
      case 'snapshot.acquire': return load('acquire1.json');
      case 'snapshot.verify': return load('verify1.json');
      case 'history.list': return req.cursor && this.list2 ? this.list2 : this.list1;
      case 'history.detail': return this.byRun.get(req.run_id) ?? this.refuse(req, `no export for ${req.run_id}`);
      case 'history.prompt': return this.prompt ?? this.refuse(req, 'no prompt export');
      default: return this.refuse(req, 'not exported');
    }
  }
  async openInIde(request: OpenInIdeRequest): Promise<OpenInIdeResult> { return { ok: false, editor: 'vscode', verified: false, message: `not launched in the harness: ${request.path}` }; }
  async copyToClipboard(): Promise<void> { /* no clipboard in jsdom */ }
  async getSetting<K extends keyof HostSettings>(key: K): Promise<HostSettings[K] | undefined> { return ({ editor: 'vscode', workspacePath: null, kriyaExecutable: null, configDirectory: null } as HostSettings)[key]; }
  async setSetting(): Promise<void> { /* read-only harness */ }
  hostInfo(): HostInfo { return { kind: 'real-export-harness', hostVersion: '0', fixtureMode: false, configDirectory: '/Users/sriramnanduri', configDirectorySource: 'default_home', configDirectoryProblem: null }; }
}

async function open(detail: KupEnvelope<RunDetail>) {
  const host = new ExportHost();
  render(<App host={host} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Acquire new snapshot' }));
  const sid = (load('acquire1.json').data as { snapshot_id: string }).snapshot_id;
  await waitFor(() => expect(screen.getByRole('region', { name: /trust strip/i })).toHaveTextContent(sid), { timeout: 10000 });
  const runId = detail.data!.run.run_id;
  const runs = () => screen.getByRole('listbox', { name: 'Recorded runs' });
  fireEvent.change(screen.getAllByRole('searchbox')[0]!, { target: { value: runId } });
  await waitFor(() => expect(within(runs()).getAllByRole('option').length).toBe(1), { timeout: 10000 });
  fireEvent.click(within(runs()).getAllByRole('option')[0]!);
  await waitFor(() => expect(screen.getByRole('button', { name: /^all events \(\d+\)$/ })).toBeInTheDocument(), { timeout: 10000 });
  return { host, sid };
}
const detailEnvelopes = () => files(/^history\.detail\..+\.json$/).map((f: string) => load(f)).filter((e: KupEnvelope) => !e.error) as KupEnvelope<RunDetail>[];

describeReal('real store exports render in every representative panel (acceptance step 8)', () => {
  it('trust strip: the real store path, digest verified at pin, Kriya identity; the runs list shows the literal recorded status values', async () => {
    const [first] = detailEnvelopes();
    expect(first).toBeDefined();
    const { sid } = await open(first!);
    const strip = screen.getByRole('region', { name: /trust strip/i });
    expect(strip).toHaveTextContent((load('capabilities.json').source as { trace_database: string }).trace_database);
    const verify = load('verify1.json').data as { verified_at: string; sha256: string };
    expect(strip).toHaveTextContent(`digest verified at pin (${verify.verified_at}); metadata checked per query`);
    expect(strip).toHaveTextContent(`sha256 ${verify.sha256.slice(0, 16)}`);
    expect(strip).toHaveTextContent(sid);
    expect(strip).not.toHaveTextContent('unverified');
    expect(strip).not.toHaveTextContent('fixtures');
    fireEvent.change(screen.getAllByRole('searchbox')[0]!, { target: { value: '' } });
    const statuses = new Set((load('history.list.page1.json').data as { runs: RunSummary[] }).runs.slice(0, 16).map((r) => String(r.status)));
    const runs = screen.getByRole('listbox', { name: 'Recorded runs' });
    for (const s of statuses) expect(within(runs).getAllByText(s).length).toBeGreaterThan(0); // literal, never mapped
  });

  for (const detail of detailEnvelopes()) {
    const d = detail.data!;
    it(`run ${d.run.run_id} (status ${d.run.status}): timeline by kind, gates by type/success, evidence by serializer fields, attribution not recorded with Kriya's reason`, async () => {
      await open(detail);
      const events = (d.run_events.data ?? []) as unknown[];
      await waitFor(() => expect(screen.getByRole('status', { name: 'Event filter result' })).toHaveTextContent(`${events.length} of ${events.length} recorded events shown`));
      expect(within(screen.getByRole('listbox', { name: /recorded events/i })).queryByText('(unnamed event)')).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole('tab', { name: /^Gates/ }));
      const gates = (d.gate_outcomes.data ?? []) as { type: string; success: boolean }[];
      const panel = document.getElementById('insp-panel-gates')!;
      expect(gates.length).toBeGreaterThan(0);
      for (const g of gates) expect(panel).toHaveTextContent(`${g.type} · ${g.success ? 'success' : 'failure'}`);
      for (const absent of ['result not recorded', 'unknown fields preserved', 'type not recorded', 'result ambiguous']) expect(panel).not.toHaveTextContent(absent);
      fireEvent.click(screen.getByRole('tab', { name: /^Evidence/ }));
      const ev = document.getElementById('insp-panel-evidence')!;
      const records = (d.evidence_records.data ?? []) as { kind: string; source: string; attempt: number }[];
      expect(records.length).toBeGreaterThan(0);
      records.forEach((r, i) => expect(ev).toHaveTextContent(`#${i + 1} ${r.kind} · ${r.source} · attempt ${r.attempt}`));
      expect(ev).not.toHaveTextContent('unknown fields preserved');
      fireEvent.click(screen.getByRole('tab', { name: /^Why/ }));
      const why = document.querySelector('.why') as HTMLElement;
      expect(why).toHaveTextContent('Recorded causal attribution: not recorded - the baseline persists failure categories, not causal attribution');
      expect(why).toHaveTextContent(`Recorded failure category: ${d.run.failure_category ?? 'none recorded'}`);
      expect(why).not.toHaveTextContent('unresolved reference');
    });
  }

  it('the prompt is fetched only on request, reported by role and length; browsing never acquires', async () => {
    const [first] = detailEnvelopes();
    const { host } = await open(first!);
    fireEvent.click(screen.getByRole('tab', { name: /^Prompt/ }));
    expect(host.calls.some((c) => c.operation === 'history.prompt')).toBe(false);
    fireEvent.click(screen.getByText('Load prompt (sensitive)'));
    await waitFor(() => expect(screen.getByText(/role: planner/)).toBeInTheDocument());
    const p = files(/^history\.prompt\..+\.json$/).map((f: string) => load(f))[0]!.data as { prompt_rendered: string | null };
    expect(screen.getByText(new RegExp(`prompt_rendered: ${p.prompt_rendered!.length.toLocaleString()} chars`))).toBeInTheDocument();
    expect(host.calls.filter((c) => c.operation === 'snapshot.acquire')).toHaveLength(1);
  });
});
