# Python API (app/) for Render, Railway, Fly.io, Azure App Service, Cloud Run, ...
# The web app (web/) is deployed separately, on Vercel.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv

# compilers only for wheels a platform may lack (pbixray's native parts); removed afterwards
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
 && pip install -r requirements.txt \
 && apt-get purge -y build-essential && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY app ./app

# local working copies of uploaded files (copies of record: database or storage bucket)
ENV BIP_DATA_DIR=/data
RUN mkdir -p /data && useradd --system --home /srv bip && chown bip /data
USER bip

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health')"
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
