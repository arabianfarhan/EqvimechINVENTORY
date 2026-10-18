# Container for hosting the app outside Streamlit Cloud (e.g. DigitalOcean
# App Platform in Bangalore, close to the Supabase database in Mumbai).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Kolkata

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Secrets come from environment variables set in the hosting dashboard:
#   DATABASE_URL (required), MANAGER_PASSWORD, RESET_CODE (optional)
EXPOSE 8080
CMD streamlit run app.py \
    --server.port=${PORT:-8080} \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --browser.gatherUsageStats=false \
    --server.fileWatcherType=none
