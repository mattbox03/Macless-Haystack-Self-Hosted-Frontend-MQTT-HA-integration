FROM python:3.12-slim
WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ /app/
COPY web/ /web/

ENV WEB_DIR=/web DATA_DIR=/data
EXPOSE 8000
CMD ["python", "server.py"]
