# Push a feature branch in tremor-api and watch its preview environment come up.
# Run from the caldera-platform root: vhs docs/media/tapes/commit-push-namespace.tape
Output docs/media/raw/commit-push-namespace.mp4
Set Shell bash
Set FontSize 14
Set Width 1400
Set Height 750
Set Framerate 10
Set TypingSpeed 25ms
Set Padding 10

Hide
Type `export GIT_PAGER=cat GH_PAGER=cat PAGER=cat PS1='$ ' T=$PWD/docs/media/tapes && cd /Users/ryanmcafee/Projects/.worktrees/tremor-api-quake-alerts && tmux -L rec -f $T/tmux.conf new-session -s rec \; split-window -h \; send-keys "$T/watch-env.sh quake-alerts" Enter \; select-pane -t 0 \; send-keys clear Enter` Enter
Sleep 3s
Show

Type `echo "- Quake alert thresholds (preview demo)" >> README.md && git diff --stat` Enter
Sleep 1s
Type `git commit -qam "docs: quake alerts preview demo" && git log --oneline -1` Enter
Sleep 3s
Type `url=https://tremor-quake-alerts.preview.prismatic.ryanmcafee.com; s=$SECONDS` Enter
Type `git push -q -u origin feature/quake-alerts` Enter
Sleep 8s
Type `until curl -sf $url/readyz >/dev/null; do sleep 2; done; echo "URL live $((SECONDS-s))s after git push"` Enter
Wait+Screen@10m /URL live \d+s/
Type `curl -s $url/readyz; echo; gh run list -R prismatic-hq/tremor-api -b feature/quake-alerts -L 2` Enter
Sleep 5s
Hide
Type `tmux -L rec kill-server` Enter
