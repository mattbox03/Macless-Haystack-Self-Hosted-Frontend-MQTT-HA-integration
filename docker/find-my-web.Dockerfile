FROM python:3.12-slim

WORKDIR /app

COPY app/backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY app/backend/ /app/
COPY app/web/ /web/

ENV WEB_DIR=/web
ENV DATA_DIR=/data
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/config', timeout=3)"

CMD ["python", "server.py"]
