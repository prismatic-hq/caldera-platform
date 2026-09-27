# Data written in one preview environment is absent in another, cross-namespace traffic is dropped, and reset restores golden data.
# Run from the caldera-platform root: vhs docs/media/tapes/isolation-reset.tape
Output docs/media/raw/isolation-reset.mp4
Set Shell bash
Set FontSize 14
Set Width 1400
Set Height 750
Set Framerate 10
Set TypingSpeed 20ms
Set Padding 10

Hide
Type `unset VIRTUAL_ENV; export GIT_PAGER=cat PAGER=cat PS1='$ ' T=$PWD/docs/media/tapes && tmux -L rec -f $T/tmux.conf new-session -s rec \; split-window -v -l 30% \; send-keys "$T/watch-drops.sh preview-tsunami" Enter \; select-pane -t 0 \; send-keys clear Enter` Enter
Sleep 3s
Show

Type `q=https://tremor-quake-alerts.preview.prismatic.ryanmcafee.com t=https://tremor-tsunami.preview.prismatic.ryanmcafee.com` Enter
Type `curl -s -X POST $q/alerts -H 'content-type: application/json' -d '{"station":"KLAU","severity":"warning","magnitude":5.8,"message":"isolation demo"}' | jq -c '{station,magnitude,message}'` Enter
Sleep 2s
Type `for u in $q $t; do echo "$u: $(curl -s "$u/alerts?limit=500" | jq '[.[] | select(.message=="isolation demo")] | length') demo alert(s)"; done` Enter
Sleep 3s
Type `kubectl --context caldera -n preview-tsunami exec deploy/tremor -- python -c "import socket; socket.create_connection(('postgres.preview-quake-alerts', 5432), timeout=3)" 2>/dev/null && echo connected || echo "blocked: preview-tsunami -> preview-quake-alerts postgres"` Enter
Wait+Screen@30s /(?m)^(blocked: preview|connected$)/
Sleep 3s
Type `uv run preview env reset --name quake-alerts --context caldera 2>&1 | grep -v '^Hang tight\|^\.\.\.'` Enter
Wait+Screen@6m /total_seconds/
Sleep 1s
Type `curl -s "$q/alerts?limit=500" | jq '[.[] | select(.message=="isolation demo")] | length'` Enter
Sleep 5s
Hide
Type `tmux -L rec kill-server` Enter
