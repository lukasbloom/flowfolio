FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir pip==24.* && \
    pip install --no-cache-dir "fastapi==0.136.*" "uvicorn[standard]==0.29.*" \
    "sqlalchemy[asyncio]==2.0.*" "aiosqlite==0.20.*" "alembic==1.13.*" \
    "pydantic-settings==2.*" "passlib[bcrypt]==1.7.*" "bcrypt<5" "python-jose[cryptography]==3.*" \
    "httpx==0.27.*" "apscheduler==3.11.*" "selectolax==0.3.*"

COPY . .

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
