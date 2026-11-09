FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 GARMIN_TOKENS=/tokens
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY garmin_sync.py .
RUN mkdir -p /tokens && chmod 700 /tokens
ENTRYPOINT ["python", "garmin_sync.py"]
CMD ["run"]
