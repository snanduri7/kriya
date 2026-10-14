# GUI client boundary

The GUI is a client of Kriya's backend services. The renderer never owns state, evidence parsing, skill files, tool configuration, authority or secrets.

Today, and unchanged by Demo V2:

```
renderer  ->  HostAdapter (ui/shared/src/host/HostAdapter.ts)  ->  typed IPC allowlist  ->  kriya-kup-stdio process  ->  KUP envelope
```

Skills and Tools follow exactly this path (`query(KupRequest)`); they add no new host capability and no new filesystem or process access in the renderer.

## Intended split (documented, NOT implemented)

```
KriyaClient            (transport-independent: what the product does)
  query()  execute()  decide()
    +- LocalKriyaClient    current: IPC to a local child process
    +- RemoteKriyaClient   future: network API

HostCapabilities       (native to the machine the window is on)
  chooseDirectory()  openInIDE()  clipboard()  nativeNotifications()
```

`HostAdapter.query` is already transport-independent and is the seam for `KriyaClient`. What still assumes a single local machine, and is left alone: workspaces are
absolute filesystem paths, run state is a local process, settings include `kriyaExecutable` and `configDirectory`, open-in-IDE takes a path.
New Demo V2 operations do not add to that: skills and tools are identified by name/id, not path, and carry no filesystem path in their descriptors.

Deployments this should allow without rewriting renderer domain logic: desktop with a local backend; desktop with a local backend and a remote model server; later a remote or shared Kriya server.
No remote multi-user support is implemented.
