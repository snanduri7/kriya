import type { ExecutionHostPort, ExecutionState, HostAdapter, HostInfo, HostSettings, KupEnvelope, KupRequest, MenuCommand, DecideApprovalRequest, DecideApprovalResult, StartRunRequest, StartRunResult, OpenInIdeRequest, OpenInIdeResult, WorkspaceChoice, WorkspaceHost, ProjectParentChoice, ProjectCreateResult, NotifyRequest, NotifyResult } from '@kriya-ui/shared';

export interface KriyaHostBridge {
  query(request: unknown): Promise<unknown>;
  openInIde(request: unknown): Promise<unknown>;
  copyToClipboard(text: string): Promise<void>;
  getSetting(key: string): Promise<unknown>;
  setSetting(key: string, value: unknown): Promise<void>;
  hostInfo(): Promise<unknown>;
  chooseWorkspace(): Promise<unknown>;
  recentWorkspaces(): Promise<unknown>;
  rememberWorkspace(path: string): Promise<unknown>;
  forgetWorkspace(path: string): Promise<unknown>;
  chooseProjectParent(): Promise<unknown>;
  createProject(request: unknown): Promise<unknown>;
  startRun(request: unknown): Promise<unknown>;
  executionState(): Promise<unknown>;
  decideApproval(request: unknown): Promise<unknown>;
  notifyRun(request: unknown): Promise<unknown>;
  onMenuCommand(listener: (command: MenuCommand) => void): () => void;
  measureMode: boolean;
}

/** HostAdapter over the preload bridge. The renderer has no other capability (contextIsolation + sandbox). */
export class ElectronHostAdapter implements HostAdapter {
  readonly workspace: WorkspaceHost;
  readonly execution: ExecutionHostPort;
  constructor(private bridge: KriyaHostBridge, private info: HostInfo) {
    this.workspace = {
      choose: () => bridge.chooseWorkspace() as Promise<WorkspaceChoice>,
      recent: () => bridge.recentWorkspaces() as Promise<string[]>,
      remember: (path) => bridge.rememberWorkspace(path) as Promise<string[]>,
      forget: (path) => bridge.forgetWorkspace(path) as Promise<string[]>,
      chooseProjectParent: () => bridge.chooseProjectParent() as Promise<ProjectParentChoice>,
      createProject: (request) => bridge.createProject(request) as Promise<ProjectCreateResult>,
      onMenuCommand: (listener) => bridge.onMenuCommand(listener),
    };
    this.execution = {
      startRun: (request: StartRunRequest) => bridge.startRun(request) as Promise<StartRunResult>,
      state: () => bridge.executionState() as Promise<ExecutionState>,
      decideApproval: (request: DecideApprovalRequest) => bridge.decideApproval(request) as Promise<DecideApprovalResult>,
      notify: (request: NotifyRequest) => bridge.notifyRun(request) as Promise<NotifyResult>,
    };
  }
  static async create(bridge: KriyaHostBridge): Promise<ElectronHostAdapter> {
    const info = (await bridge.hostInfo()) as HostInfo;
    return new ElectronHostAdapter(bridge, info);
  }
  query(request: KupRequest): Promise<KupEnvelope> { return this.bridge.query(request) as Promise<KupEnvelope>; }
  openInIde(request: OpenInIdeRequest): Promise<OpenInIdeResult> { return this.bridge.openInIde(request) as Promise<OpenInIdeResult>; }
  copyToClipboard(text: string): Promise<void> { return this.bridge.copyToClipboard(text); }
  getSetting<K extends keyof HostSettings>(key: K): Promise<HostSettings[K] | undefined> { return this.bridge.getSetting(key) as Promise<HostSettings[K] | undefined>; }
  async setSetting<K extends keyof HostSettings>(key: K, value: HostSettings[K]): Promise<void> {
    await this.bridge.setSetting(key, value);
    this.info = (await this.bridge.hostInfo()) as HostInfo;
  }
  hostInfo(): HostInfo { return this.info; }
}
