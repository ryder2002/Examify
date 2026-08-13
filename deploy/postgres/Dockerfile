FROM postgres:17.10-alpine

RUN apk add --no-cache pgbackrest \
    && mkdir -p /var/lib/pgbackrest /var/spool/pgbackrest \
    && chown -R postgres:postgres /var/lib/pgbackrest /var/spool/pgbackrest
