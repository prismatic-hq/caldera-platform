{{- define "services.environmentName" -}}
{{- $name := required "environment.name is required" .Values.environment.name -}}
{{- if not (regexMatch "^[a-z0-9]([-a-z0-9]*[a-z0-9])?$" $name) -}}
{{- fail (printf "invalid environment name %q: use lowercase letters, digits and '-', starting and ending with a letter or digit" $name) -}}
{{- end -}}
{{- $name -}}
{{- end -}}

{{- define "services.labels" -}}
app.kubernetes.io/part-of: {{ include "services.environmentName" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
prismatic.dev/environment: {{ include "services.environmentName" . }}
{{- with .Values.environment.kind }}
prismatic.dev/environment-kind: {{ . }}
{{- end }}
{{- end -}}

{{- define "services.databaseEnv" -}}
- name: DB_HOST
  value: postgres
- name: DB_PORT
  value: "5432"
- name: DB_NAME
  value: {{ .Values.postgres.database | quote }}
- name: DB_USER
  value: {{ .Values.postgres.user | quote }}
- name: DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: postgres-credentials
      key: password
{{- end -}}

{{- define "services.postgresImage" -}}
{{- $postgres := .Values.postgres -}}
{{- include "service.imageReference" (dict "registry" .Values.image.registry "repository" $postgres.image.repository "tag" ($postgres.image.tag | default .Values.datasetVersion)) -}}
{{- end -}}

{{- define "services.hook" -}}
helm.sh/hook: {{ .events | default "pre-install,pre-upgrade" }}
helm.sh/hook-weight: {{ .weight | quote }}
helm.sh/hook-delete-policy: before-hook-creation
{{- end -}}
