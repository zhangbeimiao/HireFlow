#!/bin/sh
set -eu

ssl_enabled="$(psql -Atc 'SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()')"
test "$ssl_enabled" = "t"
test "$(psql -Atc 'SELECT 42')" = "42"
echo "PostgreSQL verify-full query succeeded"

