#!/bin/sh
set -eu

mkdir -p /data

if [ ! -s /data/secrets.json ]; then
  echo ""
  echo "Google provider is waiting for data/google/secrets.json."
  echo "Generate it with GoogleFindMyTools on a desktop with Chrome,"
  echo "copy it to that path, then restart this container."
  echo ""
  exec tail -f /dev/null
fi

if [ ! -f /data/periodic_jobs.json ]; then
  printf '{}\n' > /data/periodic_jobs.json
fi

rm -f /app/Auth/secrets.json /app/periodic_jobs.json
ln -s /data/secrets.json /app/Auth/secrets.json
ln -s /data/periodic_jobs.json /app/periodic_jobs.json

exec python /app/microservice.py
