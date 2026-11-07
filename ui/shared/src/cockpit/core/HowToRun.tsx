import { useState } from 'react';
import { useProvider } from './context';
import { CopyText } from './CopyText';
import { NO_RUN_RECIPE_TEXT, type RunRecipe } from './presentation';

/**
 * HOW TO RUN: the commands Kriya's own application runtime check really executed and saw succeed, as the backend recorded them (the run recipe) - shown exactly, never derived, rewritten or invented here.
 * Copy puts a command on the clipboard; "Open Terminal Here" opens a terminal AT the project folder (the host opens the folder it has open) and runs NOTHING: the person decides whether to paste the command.
 * With no recipe (Kriya did not run the application, or the run is not one it can truthfully describe) the card says so instead of guessing.
 */
export function HowToRun({ recipe }: { recipe: RunRecipe | null }) {
  const provider = useProvider();
  const [note, setNote] = useState<string | null>(null);
  if (recipe === null) return <section className="ck-howto" aria-label="How to run" data-testid="how-to-run"><h3>How to run</h3><p className="muted" data-testid="how-to-run-none">{NO_RUN_RECIPE_TEXT}</p></section>;
  const open = provider.desktop?.openTerminal;
  const runCommands = recipe.steps.filter((s) => s.kind === 'run').map((s) => s.command);
  return (
    <section className="ck-howto" aria-label="How to run" data-testid="how-to-run">
      <h3>How to run</h3>
      {recipe.note ? <p className="muted small" data-testid="how-to-run-note">{recipe.note}</p> : null}
      <ol className="ck-howto-steps">
        {recipe.steps.map((s, i) => (
          <li key={i} data-kind={s.kind}>
            {s.kind === 'setup' ? <span className="muted small ck-howto-label">First, once</span> : null}
            <div className="ck-howto-row"><code className="ck-howto-command" data-testid={s.kind === 'run' ? 'run-command' : 'setup-command'}>{s.command}</code><CopyText text={s.command} what={s.kind === 'setup' ? 'setup command' : 'run command'} label="Copy" /></div>
          </li>
        ))}
      </ol>
      <div className="ck-howto-actions">
        {runCommands.length > 1 ? <CopyText text={runCommands.join('\n')} what="all run commands" label="Copy all" /> : null}
        {open ? <button type="button" className="ck-quiet" data-testid="open-terminal" onClick={() => void open().then((r) => setNote(r.opened ? null : `Could not open a terminal: ${r.reason ?? 'unknown reason'}`), (e: unknown) => setNote(`Could not open a terminal: ${e instanceof Error ? e.message : 'unknown reason'}`))}>Open Terminal Here</button> : null}
        {note ? <span className="muted small" role="status">{note}</span> : null}
      </div>
      {recipe.statement ? <p className="muted small" data-testid="how-to-run-evidence">{recipe.statement}</p> : null}
    </section>
  );
}
