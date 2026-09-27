# p50/p90 (nearest rank) per command and stage from `preview` CLI {"timings": ...} lines.
def pct($p): sort | .[(length * $p | ceil) - 1];
[.[].timings] | group_by(.command)[] as $runs
| ($runs | map(.stages + {total: .total_seconds})) as $rows
| ($rows | map(keys) | add | unique)[] as $stage
| [$rows[][$stage] | numbers] as $seconds
| "| \($runs[0].command) | \($stage) | \($seconds | pct(0.5)) | \($seconds | pct(0.9)) | \($seconds | length) |"
