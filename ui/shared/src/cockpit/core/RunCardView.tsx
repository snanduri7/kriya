import type { RunSummary } from '../../model/kup';
import { runCard, type CardStatus } from './presentation';

// One mark per CardStatus (the Record type makes a status without a mark a compile error). `applied_open` always has tone `warn`, so its mark is the warning sign.
const MARK: Record<CardStatus, string> = { success: '✓', failure: '✕', unfinished: '●', applied_open: '⚠', unknown: '•' };

/** One run in a history list: what was asked, whether it worked, how long it took, what it changed. Internal ids, digests and providers are not shown here (they are under Details). */
export function RunCardView({ run }: { run: RunSummary }) {
  const card = runCard(run);
  return (
    <>
      <div className="ck-runtitle" data-title-is-request={card.titleIsRequest}>{card.title}</div>
      <div className="muted small ck-runmeta" data-status={card.status} data-tone={card.tone}>
        {[`${card.tone === 'ok' ? MARK[card.status] : '⚠'} ${card.statusLabel}`, card.duration, card.filesChanged !== null ? `${card.filesChanged} file${card.filesChanged === 1 ? '' : 's'} changed` : null, card.when].filter((p): p is string => p !== null).join(' · ')}
      </div>
    </>
  );
}
