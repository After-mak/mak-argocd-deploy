{{- define "sample-fastapi.labels" -}}
app.kubernetes.io/name: sample-fastapi
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
{{- end }}
