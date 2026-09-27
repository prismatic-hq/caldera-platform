#!/usr/bin/env bash
# Usage: watch-env.sh <environment>; shows the preview namespace and its pods every 2s.
env_name="$1"
exec watch -t -n2 "echo 'kubectl get ns,pods  (preview-$env_name)'; echo; kubectl --context caldera get ns | grep -E '^NAME|preview-$env_name'; echo; kubectl --context caldera -n preview-$env_name get pods 2>&1"
