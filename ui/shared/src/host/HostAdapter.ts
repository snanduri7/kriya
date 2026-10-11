import type { DecideApprovalRequest, DecideApprovalResult, ExecutionState, KupEnvelope, KupRequest, StartRunRequest, StartRunResult } from '../model/kup';

/**
 * The ONE host boundary (gate A-2, P-R1). Every capability a host supplies to
 * the shared panels goes through this interface: a KUP query, open in IDE,
 * clipboard, settings. ui/shared never imports Electron or Node; a host
 * (Electron preload, the browser test host, a future IDE plugin) implements
 * this and hands it to <App/>. The wire-level message schema for a real host
 * lives in ui/kup (P-R2, Phase B); this is its TypeScript face.
 */
export type EditorId = 'vscode' | 'intellij' | 'eclipse';

export interface HostSettings {
  editor: EditorId;
  kriyaExecutable: string | null;
  /** The recovery-assessment workspace: travels only as an explicit argument (workspace.status, snapshot.acquire). */
  workspacePath: string | null;
  /** The configuration directory: the child's working directory for every kriya call (kriya.yaml discovery); null = HOME. */
  configDirectory: string | null;
}

export interface OpenInIdeRequest {
  path: string;
  line?: number;
}

export interface OpenInIdeResult {
  ok: boolean;
  editor: EditorId;
  verified: boolean; // false = the host knows the command form is unverified on this machine
  message: string;
}

export interface HostInfo {
  kind: string; // 'electron' | 'browser-test' | ...
  hostVersion: string;
  fixtureMode: boolean;
  /** The validated configuration directory kriya runs in (08 review F-5), or null when invalid; shown in the trust strip. */
  configDirectory: string | null;
  configDirectorySource: 'setting' | 'default_home' | 'invalid';
  configDirectoryProblem: string | null;
}

/** The desktop's folder lifecycle (GUI M5). The host owns the NATIVE chooser and the remembered list; it returns paths and nothing else - what a
 * folder is (identity, Git, state) is Kriya's answer (`workspace.describe`). A host without a native chooser (a browser test host) omits it. */
export interface WorkspaceChoice { chosen: boolean; path: string | null; /** RUN_ACTIVE: a run this host started is in progress, so the workspace cannot change (M6). */ refused?: 'RUN_ACTIVE' | null }
/** New Project (end-user experience). The renderer never names where a project is created: the NATIVE chooser returns an opaque token for the parent folder
 * (the path travels for display only) and a later request names only a project NAME; Kriya (`workspace.create`) makes the folder. */
export interface ProjectParentChoice { chosen: boolean; token: string | null; parent: string | null; refused?: 'RUN_ACTIVE' | null }
export interface ProjectCreateResult { created: boolean; path: string | null; code: string | null; message: string | null; reason: string | null }
/** A native notification for one of three durable moments. The host builds the text; only the closed kind and the launch id cross. */
export type NotifyKind = 'approval_required' | 'run_succeeded' | 'run_unsuccessful';
export interface NotifyRequest { kind: NotifyKind; launch_id: string }
export interface NotifyResult { shown: boolean; reason: string }
export type MenuCommand = 'openFolder' | 'newProject' | 'showRun';
export interface WorkspaceHost {
  /** The native folder chooser. Cancelling resolves { chosen: false } and changes nothing. */
  choose(): Promise<WorkspaceChoice>;
  recent(): Promise<string[]>;
  /** Makes an already-validated folder the current workspace and the most recent one; resolves the new recent list. */
  remember(path: string): Promise<string[]>;
  forget(path: string): Promise<string[]>;
  /** New Project, step 1: the native chooser for the PARENT folder. Absent on a host that cannot create projects. */
  chooseProjectParent?(): Promise<ProjectParentChoice>;
  /** New Project, step 2: ask Kriya to create `name` inside the chosen parent. */
  createProject?(request: { token: string; name: string }): Promise<ProjectCreateResult>;
  /** Application-menu commands (File > Open Folder...). Returns the unsubscribe function. */
  onMenuCommand(listener: (command: MenuCommand) => void): () => void;
}

/** Start Run (GUI M6). The host launches the ONE Kriya command line for a typed {workspace, goal}; it reports the PROCESS, never the run's outcome (that is
 * Kriya's durable records, read with launch.status and the evidence operations). A host with no execution backend omits it. */
export interface ExecutionHostPort {
  startRun(request: StartRunRequest): Promise<StartRunResult>;
  state(): Promise<ExecutionState>;
  /** Review decision (GUI final): the host forwards ONE typed decision to Kriya's `launch-decision` action and reports Kriya's answer. It never decides. */
  decideApproval(request: DecideApprovalRequest): Promise<DecideApprovalResult>;
  /** Ask the host for a native notification (approval waiting / run finished / run ended without success). Absent on a host with none. */
  notify?(request: NotifyRequest): Promise<NotifyResult>;
}

export interface HostAdapter {
  workspace?: WorkspaceHost;
  execution?: ExecutionHostPort;
  query(request: KupRequest, signal?: AbortSignal): Promise<KupEnvelope>;
  openInIde(request: OpenInIdeRequest): Promise<OpenInIdeResult>;
  copyToClipboard(text: string): Promise<void>;
  getSetting<K extends keyof HostSettings>(key: K): Promise<HostSettings[K] | undefined>;
  setSetting<K extends keyof HostSettings>(key: K, value: HostSettings[K]): Promise<void>;
  hostInfo(): HostInfo;
}
