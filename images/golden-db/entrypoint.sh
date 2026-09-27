#!/bin/sh
set -eu

: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set: every preview environment needs its own password}"
: "${POSTGRES_USER:=prismatic}"
: "${PGDATA:=/var/lib/postgresql/data/pgdata}"

if [ ! -s "$PGDATA/PG_VERSION" ]; then
  mkdir -p "$PGDATA"
  cp -a "$GOLDEN_DATA/." "$PGDATA/"
fi
chmod 700 "$PGDATA"

pg_ctl -D "$PGDATA" -o "-c listen_addresses=''" -w start >/dev/null
password=$(printf '%s' "$POSTGRES_PASSWORD" | sed "s/'/''/g")
printf "ALTER ROLE \"%s\" PASSWORD '%s';\n" "$POSTGRES_USER" "$password" \
  | psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres
pg_ctl -D "$PGDATA" -m fast -w stop >/dev/null

exec postgres -D "$PGDATA" -c listen_addresses='*' "$@"
