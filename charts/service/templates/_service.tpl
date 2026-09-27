{{- define "service.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: {{ .uid }}
runAsGroup: {{ .uid }}
fsGroup: {{ .uid }}
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{- define "service.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end -}}

{{- define "service.podScheduling" -}}
{{- with .priorityClassName }}
priorityClassName: {{ . }}
{{- end }}
{{- with .nodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{- define "service.imageReference" -}}
{{- $repository := required "image.repository is required" .repository -}}
{{- $reference := printf "%s:%s" $repository (required "image.tag is required" .tag) -}}
{{- if .registry -}}{{ printf "%s/%s" .registry $reference }}{{- else -}}{{ $reference }}{{- end -}}
{{- end -}}

{{- define "service.validate" -}}
{{- if not (regexMatch "^[a-z]([-a-z0-9]*[a-z0-9])?$" .name) -}}
{{- fail (printf "invalid service name %q: use lowercase letters, digits and '-', starting with a letter" .name) -}}
{{- end -}}
{{- if .route.enabled -}}
{{- $hostname := required (printf "route.hostname is required for service %q" .name) .route.hostname -}}
{{- $label := first (splitList "." $hostname) -}}
{{- if gt (len $label) 63 -}}
{{- fail (printf "hostname label too long: %q is %d characters, DNS labels allow 63" $label (len $label)) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Renders one service. Context keys: name, namespace, labels, image (map with registry, repository,
tag), pullPolicy, port, resources, priorityClassName, nodeSelector, tolerations, env, route (enabled,
hostname, labels, gateway).
*/}}
{{- define "service.manifests" -}}
{{- include "service.validate" . -}}
{{- $selector := dict "app.kubernetes.io/name" .name -}}
{{- $labels := merge (dict "app.kubernetes.io/component" "api") $selector (deepCopy .labels) -}}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .name }}
  namespace: {{ .namespace }}
  labels:
    {{- toYaml $labels | nindent 4 }}
spec:
  replicas: 1
  selector:
    matchLabels:
      {{- toYaml $selector | nindent 6 }}
  template:
    metadata:
      labels:
        {{- toYaml $labels | nindent 8 }}
    spec:
      {{- with include "service.podScheduling" . | trim }}{{ . | nindent 6 }}{{- end }}
      automountServiceAccountToken: false
      securityContext:
        {{- include "service.podSecurityContext" (dict "uid" 10001) | nindent 8 }}
      containers:
        - name: api
          image: {{ include "service.imageReference" .image }}
          imagePullPolicy: {{ .pullPolicy }}
          ports:
            - name: http
              containerPort: {{ .port }}
          {{- with .env }}
          env:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          startupProbe:
            httpGet:
              path: /healthz
              port: http
            periodSeconds: 2
            failureThreshold: 30
          readinessProbe:
            httpGet:
              path: /readyz
              port: http
            periodSeconds: 2
          livenessProbe:
            httpGet:
              path: /healthz
              port: http
            periodSeconds: 10
          resources:
            {{- toYaml .resources | nindent 12 }}
          securityContext:
            {{- include "service.containerSecurityContext" . | nindent 12 }}
          volumeMounts:
            - name: tmp
              mountPath: /tmp
      volumes:
        - name: tmp
          emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: {{ .name }}
  namespace: {{ .namespace }}
  labels:
    {{- toYaml $labels | nindent 4 }}
spec:
  selector:
    {{- toYaml $selector | nindent 4 }}
  ports:
    - name: http
      port: {{ .port }}
      targetPort: http
{{- if .route.enabled }}
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: {{ .name }}
  namespace: {{ .namespace }}
  labels:
    {{- toYaml (merge (deepCopy (.route.labels | default dict)) $labels) | nindent 4 }}
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: {{ required "route.gateway.name is required" .route.gateway.name }}
      namespace: {{ required "route.gateway.namespace is required" .route.gateway.namespace }}
      {{- with .route.gateway.sectionName }}
      sectionName: {{ . }}
      {{- end }}
  hostnames:
    - {{ .route.hostname | quote }}
  rules:
    - backendRefs:
        - name: {{ .name }}
          port: {{ .port }}
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-gateway-to-{{ .name }}
  namespace: {{ .namespace }}
  labels:
    {{- toYaml $labels | nindent 4 }}
spec:
  podSelector:
    matchLabels:
      {{- toYaml $selector | nindent 6 }}
  policyTypes: ["Ingress"]
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: {{ .route.gateway.namespace }}
      ports:
        - port: http
{{- end }}
{{- end -}}
