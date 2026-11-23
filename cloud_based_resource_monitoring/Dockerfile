FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install Python dependencies
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy unimaas_infraflex package
COPY unimaas_infraflex /app/unimaas_infraflex
RUN chmod +x /app/unimaas_infraflex/entrypoint.sh

WORKDIR /app/unimaas_infraflex

ENTRYPOINT ["/app/unimaas_infraflex/entrypoint.sh"]
