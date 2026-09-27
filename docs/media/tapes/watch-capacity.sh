#!/usr/bin/env bash
# Shows headroom pods, preview-environments NodeClaims and pending preview pods every 2s.
exec watch -t -n2 "echo 'headroom (caldera-system)'; kubectl --context caldera -n caldera-system get pods -o wide 2>&1 | cut -c1-90; echo; echo 'nodeclaims'; kubectl --context caldera get nodeclaims 2>&1 | cut -c1-110; echo; echo 'pending preview pods'; kubectl --context caldera get pods -A --field-selector=status.phase=Pending 2>&1 | grep -E '^NAMESPACE|preview-' | cut -c1-110"
