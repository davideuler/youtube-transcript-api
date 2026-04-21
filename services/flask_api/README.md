# Flask API Submodule

This submodule exposes `youtube-transcript-api` as a Flask REST API and is managed
with `uv`.

## Start

```bash
cd services/flask_api
uv sync
uv run youtube-transcript-flask-api
```

## Deploy

```bash
cd services/flask_api
./scripts/deploy.sh
```

## Test

Run unit tests:

```bash
cd services/flask_api
uv run --extra dev pytest tests
```

Run the smoke test against a running service:

```bash
cd services/flask_api
./scripts/test_api.sh http://127.0.0.1:8080
```

## Environment

- `YTA_API_HOST`: bind host, default `0.0.0.0`
- `YTA_API_PORT`: bind port, default `8080`
- `YTA_API_DEBUG`: Flask debug flag, default `false`
- `YTA_PROXY_HTTP_URL`: optional HTTP proxy for `GenericProxyConfig`
- `YTA_PROXY_HTTPS_URL`: optional HTTPS proxy for `GenericProxyConfig`
- `YTA_GUNICORN_WORKERS`: gunicorn worker count, default `2`
- `YTA_GUNICORN_TIMEOUT`: gunicorn timeout in seconds, default `120`
