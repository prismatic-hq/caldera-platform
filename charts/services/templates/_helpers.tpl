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
