# Run Cockpit platform (M1)

```
cockpit/
  core/        UI Core: Availability, CapabilityRegistry, ModuleRegistry, SelectionStore, RunContext, UIProvider contract,
               EvidenceClient (loadRunEvidence), context hooks. The only thing modules import ('../../core').
  design/      DesignSystem: tokens (cockpit.css) + Badge, Panel, KeyValue, TabBar, AvailabilityView, ProviderBanner.
  providers/   HostProvider (the one implementation) + KriyaProvider (production). fixture/ is DEV/TEST ONLY.
  shell/       AppShell (composition + layout only), GlobalToolbar, NavigationRail, SecondaryPane, MainWorkspace,
               ExecutionInspector, EvidenceDrawer, ModuleHost, ResizeHandle.
  modules/     runs overview timeline trust changes validation why context plan attempts evidence workspace settings drawer
  composition.ts   the one list of modules and pages
  testing/     harness (renderModule / renderShell) over the fixture provider
```

Add a module: create `modules/<id>/index.tsx` exporting a `ModuleDefinition`, add it to `composition.ts`. Remove one: delete
that entry (or pass `disabled`). `AppShell` is never edited. Modules import only `core`, `design`, model types and their own
files (`boundaries.test.ts` fails otherwise); they read only the selection fields they declare.

Run one module's tests alone: `npx vitest run src/cockpit/modules/changes` (from `ui/shared`).
Screenshots (development evidence): `cd ui/standalone && npm run build:cockpit-fixture`, then
`KRIYA_UI_CAPTURE=<dir> KRIYA_UI_CAPTURE_STEPS=$PWD/capture-cockpit.json npx electron .`.
