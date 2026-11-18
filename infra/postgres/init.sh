#!/bin/sh
set -eu
# psql variables are quoted by psql, not interpolated into SQL by the shell.
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set=app_password="$APP_DB_PASSWORD" --set=database_name="$POSTGRES_DB" --set=ON_ERROR_STOP=1 <<'SQL'
CREATE ROLE insightforge_app LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON DATABASE :"database_name" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"database_name" TO insightforge_app;
SQL
