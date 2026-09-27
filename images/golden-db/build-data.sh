#!/bin/sh
set -eu

initdb -D "$GOLDEN_DATA" -U prismatic --auth-local=trust --auth-host=scram-sha-256 \
  --encoding=UTF8 --locale=C.UTF-8 >/dev/null
echo "host all all all scram-sha-256" >>"$GOLDEN_DATA/pg_hba.conf"
pg_ctl -D "$GOLDEN_DATA" -o "-c listen_addresses=''" -w start >/dev/null
createdb -U prismatic prismatic
psql -X -q -v ON_ERROR_STOP=1 -U prismatic -d prismatic -f /build/golden.sql
psql -X -At -F ' ' -v ON_ERROR_STOP=1 -U prismatic -d prismatic -f /build/checksums.sql \
  >/tmp/checksums.txt
if ! diff -u /build/expected-checksums.txt /tmp/checksums.txt; then
  echo "golden data checksums differ from the workflow's database; refusing to bake the image" >&2
  exit 1
fi
pg_ctl -D "$GOLDEN_DATA" -m smart -w stop >/dev/null
