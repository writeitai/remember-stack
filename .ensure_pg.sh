if ! docker exec rs-pg psql -U rememberstack -d rememberstack_test -tAc "select 1" >/dev/null 2>&1; then
  docker start rs-pg >/dev/null 2>&1 || {
    docker image inspect rs-pg:wpet7 >/dev/null 2>&1 || docker build -q -f Dockerfile.postgres -t rs-pg:wpet7 . >/tmp/pgbuild.log 2>&1
    docker rm -f rs-pg >/dev/null 2>&1
    docker run -d --name rs-pg -e POSTGRES_USER=rememberstack -e POSTGRES_PASSWORD=rememberstack_test -e POSTGRES_DB=rememberstack_test -p 5432:5432 rs-pg:wpet7 postgres -c shared_preload_libraries=pg_textsearch,pg_partman_bgw -c pg_partman_bgw.dbname=rememberstack_test -c pg_partman_bgw.role=rememberstack -c max_locks_per_transaction=512 >/dev/null
  }
  for i in $(seq 1 90); do docker exec rs-pg psql -U rememberstack -d rememberstack_test -tAc "select 1" >/dev/null 2>&1 && break; sleep 1; done
fi
echo pg-ready
