{{- define "preview.name" -}}
{{- $name := required "vent.name is required" .Values.vent.name -}}
{{- if not (regexMatch "^[a-z0-9]([-a-z0-9]*[a-z0-9])?$" $name) -}}
{{- fail (printf "invalid vent name %q: use lowercase letters, digits and '-', starting and ending with a letter or digit" $name) -}}
{{- end -}}
{{- range $service, $_ := .Values.services -}}
{{- $label := printf "%s-%s" $service $name -}}
{{- if gt (len $label) 63 -}}
{{- fail (printf "vent label too long: %q is %d characters, DNS labels allow 63" $label (len $label)) -}}
{{- end -}}
{{- end -}}
{{- $name -}}
{{- end -}}

{{- define "preview.labels" -}}
app.kubernetes.io/part-of: vent
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
prismatic.dev/vent: {{ include "preview.name" . }}
{{- end -}}

{{- define "preview.selectorLabels" -}}
app.kubernetes.io/name: {{ .name }}
{{- end -}}

{{- define "preview.image" -}}
{{- $registry := .root.Values.image.registry -}}
{{- $reference := printf "%s:%s" .image.repository (required "image tag is required" .tag) -}}
{{- if $registry -}}{{ printf "%s/%s" $registry $reference }}{{- else -}}{{ $reference }}{{- end -}}
{{- end -}}

{{- define "preview.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: {{ .uid }}
runAsGroup: {{ .uid }}
fsGroup: {{ .uid }}
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{- define "preview.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end -}}
