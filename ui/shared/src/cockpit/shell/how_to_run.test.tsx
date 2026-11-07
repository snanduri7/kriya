import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { NO_RUN_RECIPE_TEXT, runRecipeOf } from '../core';

const ended = (recipe: unknown) => ({ at: 't', verified_run_recipe: recipe });

describe('the verified run recipe as the cockpit reads it (typed, from the backend, never derived)', () => {
  it('parses exactly the steps the backend recorded', () => {
    const recipe = runRecipeOf(ended({ steps: [{ kind: 'setup', command: 'python3 -m pip install -r requirements.txt' }, { kind: 'run', command: 'python3 main.py' }], entrypoint: 'main.py', environment_note: null, statement: 'Kriya ran this.' }));
    expect(recipe).toEqual({ steps: [{ kind: 'setup', command: 'python3 -m pip install -r requirements.txt' }, { kind: 'run', command: 'python3 main.py' }], entrypoint: 'main.py', note: null, statement: 'Kriya ran this.' });
  });
  it.each([
    [undefined], [null], ['python3 main.py'], [{}], [{ steps: 'python3 main.py' }], [{ steps: [] }],
    [{ steps: [{ kind: 'setup', command: 'only a setup step' }] }],              // nothing to RUN: no recipe
    [{ steps: [{ kind: 'evil', command: 'rm -rf /' }, { kind: 'run', command: 7 }, null, 'x'] }],
  ])('returns no recipe for a missing or malformed field (%j) - never an invented one', (value) => {
    expect(runRecipeOf(ended(value))).toBeNull();
    expect(runRecipeOf(undefined)).toBeNull();
  });
  it('drops unknown step kinds and non-string commands but keeps the valid run step', () => {
    const recipe = runRecipeOf(ended({ steps: [{ kind: 'evil', command: 'x' }, { kind: 'run', command: 'node app.js' }, { kind: 'run', command: '' }] }));
    expect(recipe?.steps).toEqual([{ kind: 'run', command: 'node app.js' }]);
  });
  it('says plainly when there is nothing verified to show', () => {
    expect(NO_RUN_RECIPE_TEXT).toBe('Kriya did not run this application, so it has no verified run command.');
    expect(renderToStaticMarkup).toBeTypeOf('function');
  });
});
