FROM python:3.11-slim

ARG GOOGLE_FIND_HUB_REF=main
ARG GOOGLE_FIND_HUB_REPOSITORY=https://github.com/traccar/google-find-hub-sync.git

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && git clone --depth 1 "${GOOGLE_FIND_HUB_REPOSITORY}" /app \
    && if [ "${GOOGLE_FIND_HUB_REF}" != "main" ]; then \
         cd /app \
         && git fetch --depth 1 origin "${GOOGLE_FIND_HUB_REF}" \
         && git checkout FETCH_HEAD; \
       fi \
    && pip install --no-cache-dir -r /app/requirements.txt \
    && apt-get purge -y --auto-remove git \
    && rm -rf /var/lib/apt/lists/*

COPY docker/google-entrypoint.sh /usr/local/bin/google-entrypoint
RUN chmod 0755 /usr/local/bin/google-entrypoint

WORKDIR /app
EXPOSE 5500
ENTRYPOINT ["/usr/local/bin/google-entrypoint"]
