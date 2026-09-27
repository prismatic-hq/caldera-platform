#!/usr/bin/env bash
# Usage: prepare-dataset.sh TREMOR_API_DIR STEWARD_API_DIR OUT_DIR
set -euo pipefail

tremor_dir=$(cd "$1" && pwd)
steward_dir=$(cd "$2" && pwd)
out_dir=$3
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
postgres_image=${POSTGRES_IMAGE:-postgres:17.11-alpine3.24}
password=golden-build
containers=()

cleanup() {
  if ((${#containers[@]})); then docker rm -f "${containers[@]}" >/dev/null; fi
}
trap cleanup EXIT

psql_in() {
  docker exec -i -e PGTZ=UTC "$1" psql -X -At -F ' ' -v ON_ERROR_STOP=1 -U prismatic -d prismatic "${@:2}"
}

start_postgres() {
  local name=$1 port=$2
  docker run -d --rm --name "$name" -p "127.0.0.1:$port:5432" \
    -e POSTGRES_USER=prismatic -e POSTGRES_DB=prismatic -e POSTGRES_PASSWORD="$password" \
    "$postgres_image" >/dev/null
  containers+=("$name")
  for _ in $(seq 60); do
    if docker exec "$name" pg_isready -q -h 127.0.0.1 -U prismatic -d prismatic; then return; fi
    sleep 1
  done
  echo "postgres in $name did not become ready in 60s" >&2
  docker logs "$name" >&2
  exit 1
}

migrate() {
  local dir=$1 port=$2
  DB_HOST=127.0.0.1 DB_PORT=$port DB_NAME=prismatic DB_USER=prismatic DB_PASSWORD=$password \
    uv run --directory "$dir" --locked --no-dev alembic upgrade head
}

build_database() {
  local name=$1 port=$2
  start_postgres "$name" "$port"
  migrate "$tremor_dir" "$port"
  migrate "$steward_dir" "$port"
  uv run --project "$root" --locked golden-seeder | psql_in "$name" -q
  psql_in "$name" <"$here/checksums.sql"
}

mkdir -p "$out_dir"
build_database golden-build-a 55432 >"$out_dir/expected-checksums.txt"
build_database golden-build-b 55433 >"$out_dir/checksums-rebuild.txt"
if ! diff -u "$out_dir/expected-checksums.txt" "$out_dir/checksums-rebuild.txt"; then
  echo "golden dataset is not deterministic: two builds from the same inputs differ" >&2
  exit 1
fi
rm "$out_dir/checksums-rebuild.txt"
docker exec golden-build-a pg_dump -U prismatic -d prismatic --no-owner --no-privileges \
  >"$out_dir/golden.sql"
cat "$out_dir/expected-checksums.txt"
