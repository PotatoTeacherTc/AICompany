FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AICOMPANY_ENV=production \
    ALLOW_PAID_PROVIDER=False

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /data

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "application.production:create_production_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
