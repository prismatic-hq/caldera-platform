# applicationsets

Workload ApplicationSets that read desired state from `prismatic-hq/applications-infra`:
- `environments`: Git files generator over `environments/**/env.yaml` -> `env-<env>` Applications.
- `services`: Git files generator over `environments/**/services/*.yaml` -> `<service>-<env>` Applications.
Vent Applications use the `previews` AppProject and carry finalizers so deleting a file cools the vent.
