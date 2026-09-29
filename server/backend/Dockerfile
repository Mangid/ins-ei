FROM python:3.13-slim

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app.py .
COPY backend/notifications.py .
COPY customer-portal /app/customer-portal

EXPOSE 8100

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100"]
