#!/usr/bin/env bash
# Azure App Service (Linux Python) startup command.
# gunicorn binds to the port Azure injects; create_app() is our Flask factory.
gunicorn \
  --bind=0.0.0.0:${PORT:-8000} \
  --timeout 600 \
  --workers 2 \
  --access-logfile '-' \
  --error-logfile '-' \
  "backend.flask_app:create_app()"
