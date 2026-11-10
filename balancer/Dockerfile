FROM python:3.12-alpine
WORKDIR /app
COPY balance.py panel.py ./
COPY static ./static
# Runs behind ares-group.org (balance.ares-group.org, owner only): no panel password, localhost only.
ENV WARDOGS_BALANCE_DIR=/data \
    WARDOGS_PANEL_HOST=127.0.0.1 \
    WARDOGS_PANEL_PORT=8765 \
    WARDOGS_PANEL_AUTH=site \
    PYTHONUNBUFFERED=1
VOLUME /data
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1:8765/healthz || exit 1
CMD ["python3", "panel.py"]
