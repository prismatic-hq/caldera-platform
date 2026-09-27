#!/usr/bin/env bash
# Usage: to-gif.sh <raw.mp4> <out.gif>; drops idle frames, then optimizes with gifsicle.
set -euo pipefail
ffmpeg -v error -y -i "$1" -vf "mpdecimate=hi=64*48:lo=64*12:frac=0.5,setpts=N/10/TB,fps=10,tpad=stop_mode=clone:stop_duration=5,scale=1120:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=5" "$2.tmp.gif"
gifsicle -O3 --lossy=80 --colors 128 "$2.tmp.gif" -o "$2"
rm "$2.tmp.gif"
