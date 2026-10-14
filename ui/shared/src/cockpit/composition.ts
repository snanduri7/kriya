/**
 * The composition root: the one place that lists the compile-time modules and the rail pages. Adding a module is one import and one
 * array entry here (or none, for a module a test builds); AppShell is never edited. There is no dynamic loading.
 */
import { createModuleRegistry, type ModuleDefinition, type ModuleRegistry, type PageDefinition } from './core';
import { attemptsModule } from './modules/attempts';
import { changesModule } from './modules/changes';
import { contextModule } from './modules/context';
import { drawerBuildModule, drawerGitModule, drawerRawEventsModule, drawerTestsModule } from './modules/drawer';
import { evidenceModule } from './modules/evidence';
import { overviewModule } from './modules/overview';
import { planModule } from './modules/plan';
import { runsModule } from './modules/runs';
import { settingsModule } from './modules/settings';
import { skillsModule } from './modules/skills';
import { timelineModule } from './modules/timeline';
import { toolsModule } from './modules/tools';
import { trustModule } from './modules/trust';
import { validationModule } from './modules/validation';
import { whyModule } from './modules/why';
import { workspaceModule } from './modules/workspace';

export const COCKPIT_PAGES: readonly PageDefinition[] = [
  { id: 'runs', label: 'Runs', order: 10, layout: 'run', icon: '▶' },
  { id: 'workspace', label: 'Workspace', order: 20, layout: 'single', icon: '▣' },
  { id: 'skills', label: 'Skills', order: 22, layout: 'single', icon: '✦' },
  { id: 'tools', label: 'Tools', order: 24, layout: 'single', icon: '▤' },
  { id: 'settings', label: 'Settings', order: 30, layout: 'single', icon: '⚙' },
];

export const COCKPIT_MODULES: readonly ModuleDefinition[] = [
  runsModule, overviewModule, timelineModule, trustModule, changesModule, validationModule, whyModule, contextModule, planModule, attemptsModule, evidenceModule,
  drawerBuildModule, drawerTestsModule, drawerGitModule, drawerRawEventsModule, workspaceModule, skillsModule, toolsModule, settingsModule,
];

export function createCockpitRegistry(options: { disabled?: readonly string[]; extra?: readonly ModuleDefinition[] } = {}): ModuleRegistry {
  return createModuleRegistry([...COCKPIT_MODULES, ...(options.extra ?? [])], { pages: COCKPIT_PAGES, disabled: options.disabled });
}
