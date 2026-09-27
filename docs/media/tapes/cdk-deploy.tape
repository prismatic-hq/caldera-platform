# Deploy every stack against the live account; 12-digit account IDs are masked.
# Run from the caldera-platform root: vhs docs/media/tapes/cdk-deploy.tape, then
# gifsicle -O3 --colors 32 --lossy=150 --resize-width 960 to stay under the 500 KB hook.
Output docs/media/cdk-deploy.gif

Require npx
Set Shell "bash"
Set FontSize 15
Set Width 1200
Set Height 700
Set Padding 20
Set Theme "Catppuccin Mocha"
Set TypingSpeed 40ms
Set PlaybackSpeed 1.0

Hide
Type `export PS1='\[\e[1;36m\]caldera-platform\[\e[0m\] $ '` Enter
Type `npx() { command npx "$@" 2>&1 | sed -E 's/[0-9]{12}/<account>/g'; }` Enter
Type "clear" Enter
Show

Type "npx --yes aws-cdk@2 deploy --all -c env=sandbox.yaml"
Sleep 500ms
Enter
Sleep 30s
Sleep 4s
