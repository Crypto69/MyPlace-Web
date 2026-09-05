# ---- frontend ----
FROM node:22-alpine AS webbuild
WORKDIR /build
COPY frontend/package.json ./
RUN npm install --no-fund --no-audit
COPY frontend/ ./
RUN npm run build

# ---- backend ----
FROM python:3.12-slim
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ backend/
COPY --from=webbuild /build/dist frontend/dist

ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
ENV APP_BUILD_SHA=$GIT_SHA APP_BUILD_TIME=$BUILD_TIME

EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
