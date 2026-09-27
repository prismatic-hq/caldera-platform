# Push a second feature branch (steward-api) and watch headroom pods and Karpenter NodeClaims.
# Run from the caldera-platform root with STEWARD_API_DIR set to a steward-api checkout on feature/tsunami:
# vhs docs/media/tapes/capacity-watch.tape
Output docs/media/raw/capacity-watch.mp4
Set Shell bash
Set FontSize 14
Set Width 1400
Set Height 750
Set Framerate 10
Set TypingSpeed 25ms
Set Padding 10

Hide
Type `export GIT_PAGER=cat GH_PAGER=cat PAGER=cat PS1='$ ' T=$PWD/docs/media/tapes && cd "$STEWARD_API_DIR" && tmux -L rec -f $T/tmux.conf new-session -s rec \; split-window -h \; send-keys "$T/watch-capacity.sh" Enter \; select-pane -t 0 \; send-keys clear Enter` Enter
Sleep 3s
Show

Type `echo "- Tsunami crew dispatch (preview demo)" >> README.md && git diff --stat` Enter
Sleep 1s
Type `git commit -qam "docs: tsunami preview demo" && git log --oneline -1` Enter
Sleep 3s
Type `url=https://steward-tsunami.preview.prismatic.ryanmcafee.com; s=$SECONDS` Enter
Type `git push -q -u origin feature/tsunami` Enter
Sleep 8s
Type `until curl -sf $url/readyz >/dev/null; do sleep 2; done; echo "URL live $((SECONDS-s))s after git push"` Enter
Wait+Screen@10m /URL live \d+s/
Type `curl -s $url/readyz; echo; gh run list -R prismatic-hq/steward-api -b feature/tsunami -L 2` Enter
Sleep 5s
Hide
Type `tmux -L rec kill-server` Enter
