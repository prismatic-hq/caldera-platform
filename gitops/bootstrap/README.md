# bootstrap

Root Argo CD Application (App of Apps) that the `GitOpsBridgeStack` points at after installing Argo CD.
It syncs `addons/` first, then `applicationsets/`, ordered by sync waves.
Also holds the AppProjects: `platform`, `baseline`, `staging`, `prod`, `tenants` and `previews`.
