FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 GARMIN_TOKENS=/tokens
WORKDIR /app
# Node kjører openGyms egen .fit-eksport (og_fit.mjs) når styrkeøkter sendes til Garmin.
RUN apt-get update && apt-get install -y --no-install-recommends nodejs && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY garmin_sync.py og_fit.mjs ./
RUN mkdir -p /tokens && chmod 700 /tokens
ENTRYPOINT ["python", "garmin_sync.py"]
CMD ["run"]
