/**
 * "Open in IDE" (gate A-2 P-R5, 06 Phase E): editor command FORMS are a fixed table; the only data that reaches the
 * command is a validated absolute file path INSIDE the selected workspace and a positive integer line. No command
 * text ever comes from run data. Pure planning here; the spawn happens in main.ts through a no-shell spawn.
 */
import { realpathSync, statSync } from 'node:fs';
import { isAbsolute, relative, resolve, sep } from 'node:path';
import type { EditorId } from './ipc_contract';

export interface EditorForm {
  id: EditorId;
  label: string;
  binary: string;
  verified: boolean;
  verificationNote: string;
  argv: (path: string, line: number | undefined) => string[];
  /** The command line that opens a FOLDER (the project) in this editor; null = no verified folder form, so a folder is refused rather than guessed. */
  folderArgv: ((path: string) => string[]) | null;
}

export const EDITOR_FORMS: Record<EditorId, EditorForm> = {
  vscode: {
    id: 'vscode', label: 'Visual Studio Code', binary: 'code', verified: true,
    verificationNote: 'MEASURED 2026-10-04 on the owner\'s Mac: `code -g /abs/file.txt:7` exit 0, VS Code CLI 1.140.0 (cursor line: owner to confirm visually)',
    argv: (path, line) => ['-g', line ? `${path}:${line}` : path],
    folderArgv: (path) => [path],   // `code <folder>` opens the folder as the window's workspace
  },
  intellij: {
    id: 'intellij', label: 'IntelliJ IDEA', binary: 'idea', verified: false,
    verificationNote: 'unverified - not installed on the owner\'s Mac (documented form: idea --line N file)',
    argv: (path, line) => (line ? ['--line', String(line), path] : [path]),
    folderArgv: (path) => [path],   // `idea <dir>` opens a project directory (documented form; unverified on this machine, see verificationNote)
  },
  eclipse: {
    id: 'eclipse', label: 'Eclipse', binary: 'eclipse', verified: false,
    verificationNote: 'unverified - not installed on the owner\'s Mac (documented form: eclipse file; no line argument)',
    argv: (path) => [path],
    folderArgv: null,               // Eclipse opens a workspace with -data, a different thing from a project folder: no guessed form
  },
};

export type OpenPlan = { ok: true; editor: EditorForm; binary: string; argv: string[]; realPath: string } | { ok: false; editor: EditorForm; reason: string };

/** Containment uses real paths (symlinks resolved) and a path relation, never a string prefix. */
export function planOpenInIde(editorId: EditorId, workspace: string | null, path: string, line?: number): OpenPlan {
  const editor = EDITOR_FORMS[editorId];
  if (!workspace) return { ok: false, editor, reason: 'no workspace selected; open in IDE needs the workspace boundary' };
  if (line !== undefined && (!Number.isInteger(line) || line < 1)) return { ok: false, editor, reason: 'line must be a positive integer' };
  let wsReal: string, fileReal: string;
  try { wsReal = realpathSync(workspace); } catch { return { ok: false, editor, reason: 'workspace does not exist' }; }
  const candidate = isAbsolute(path) ? path : resolve(wsReal, path);
  try { fileReal = realpathSync(candidate); } catch { return { ok: false, editor, reason: 'file does not exist inside the workspace' }; }
  const rel = relative(wsReal, fileReal);
  if (rel === '') {
    // The selected workspace ITSELF (by real path - a symlink to anywhere else is a different folder and is refused below): the project folder. Only this one folder, only a directory,
    // never with a line, and only through the editor's own folder form.
    if (line !== undefined) return { ok: false, editor, reason: 'a folder has no line to go to' };
    try { if (!statSync(fileReal).isDirectory()) return { ok: false, editor, reason: 'the workspace is not a folder' }; } catch { return { ok: false, editor, reason: 'the workspace is not readable' }; }
    if (editor.folderArgv === null) return { ok: false, editor, reason: `${editor.label} has no verified way to open a project folder from here; open the folder in ${editor.label} yourself` };
    return { ok: true, editor, binary: editor.binary, argv: editor.folderArgv(fileReal), realPath: fileReal };
  }
  if (isAbsolute(rel) || rel.split(sep)[0] === '..') return { ok: false, editor, reason: 'file is outside the selected workspace' };
  try { if (!statSync(fileReal).isFile()) return { ok: false, editor, reason: 'target is not a regular file' }; } catch { return { ok: false, editor, reason: 'file is not readable' }; }
  return { ok: true, editor, binary: editor.binary, argv: editor.argv(fileReal, line), realPath: fileReal };
}
