# One image: builds the React frontend, then serves it and the API from the Python backend on port 8000.
FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app/backend

COPY backend/requirements.lock backend/requirements-hyper.txt ./
RUN pip install --no-cache-dir -r requirements.lock
# Optional: .hyper row previews. The Hyper API ships wheels for Linux x86_64 only, so on an ARM host
# this step is skipped and the app still works without .hyper previews.
RUN pip install --no-cache-dir -r requirements-hyper.txt || echo "Hyper API not installed: .hyper preview disabled"

COPY backend/app ./app
COPY --from=frontend /app/frontend/dist /app/frontend/dist

# Run as an unprivileged user; uploads live in a temp dir that is deleted automatically.
RUN useradd --create-home appuser
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
