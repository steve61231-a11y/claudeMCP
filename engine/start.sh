#!/bin/sh
# Container entrypoint.
#
# This used to be one line:
#
#     alembic upgrade head && uvicorn engine.api_server:app ...
#
# The `&&` is the problem. With no DATABASE_URL — the single most likely
# thing to be missing on a first deploy — alembic exits non-zero, uvicorn
# never starts, the container dies, and the host reports "service crashed"
# with no further explanation. The real reason is one line in a log nobody
# has been told to open.
#
# Worse, on a host that only offers a public domain once a service is
# listening, a crash loop means the domain button never appears at all. The
# operator is then hunting for a menu that cannot exist, which is exactly
# how an evening gets lost.
#
# So: say plainly what is wrong, and start the server anyway. A page that
# loads and explains itself beats a container that vanishes. This is the
# same rule the product is built on — a thing that failed must not look
# like a thing that was never there.

set -u

banner() {
  echo ""
  echo "============================================================"
  echo "  $1"
  echo "============================================================"
  echo ""
}

if [ -z "${DATABASE_URL:-}" ]; then
  banner "DATABASE_URL is not set — starting anyway, but nothing will work.

  Set it to your Postgres connection string in this host's
  environment variables, then redeploy. Neon's free tier gives you
  one: https://neon.tech

  The app will start and serve its health endpoint so you can see
  this, but every report will fail until a database is reachable."
else
  echo "Running database migrations..."
  if (cd engine && alembic upgrade head); then
    echo "Migrations complete."
  else
    banner "MIGRATIONS FAILED — starting anyway so you can see this.

  The database could not be reached or could not be migrated.
  Check DATABASE_URL. The most common causes are a wrong password,
  a database that has been suspended, or a connection string
  copied without its ?sslmode=require suffix."
  fi
fi

echo "Starting server on port ${PORT:-8000}..."
exec uvicorn engine.api_server:app --host 0.0.0.0 --port "${PORT:-8000}"
