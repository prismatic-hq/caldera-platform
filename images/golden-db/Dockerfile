# syntax=docker/dockerfile:1
ARG POSTGRES_IMAGE=postgres:17.11-alpine3.24

FROM ${POSTGRES_IMAGE} AS base
ENV GOLDEN_DATA=/var/lib/postgresql/golden PGTZ=UTC
RUN adduser -D -H -u 10001 -s /sbin/nologin golden \
  && mkdir -p "$GOLDEN_DATA" /var/run/postgresql \
  && chown golden:golden "$GOLDEN_DATA" /var/run/postgresql

FROM base AS data
COPY --chmod=0755 build-data.sh /build/build-data.sh
COPY checksums.sql /build/checksums.sql
COPY --from=dataset golden.sql expected-checksums.txt /build/
USER 10001:10001
RUN /build/build-data.sh

FROM base
COPY --from=data --chown=10001:10001 /var/lib/postgresql/golden /var/lib/postgresql/golden
COPY --chmod=0755 entrypoint.sh /usr/local/bin/golden-entrypoint
USER 10001:10001
ENV PGDATA=/var/lib/postgresql/data/pgdata
EXPOSE 5432
ENTRYPOINT ["golden-entrypoint"]
