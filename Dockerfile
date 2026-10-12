# Undertow dashboard + data refresher in one container.
#   docker build -t undertow .
#   docker run -p 8000:8000 undertow            # then open http://localhost:8000
# The first data refresh takes about a minute. Read-only app: no keys, no order sending.

FROM rust:1-slim-bookworm AS cli
RUN apt-get update && apt-get install -y --no-install-recommends pkg-config libssl-dev ca-certificates && rm -rf /var/lib/apt/lists/*
RUN cargo install perpl-cli --version 0.2.9 --locked

FROM python:3.13-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libssl3 && rm -rf /var/lib/apt/lists/*
COPY --from=cli /usr/local/cargo/bin/perpl-cli /usr/local/bin/perpl-cli
WORKDIR /app
# Live tab (Perpl WebSocket feed). Without it the rest of the dashboard still works.
RUN pip install --no-cache-dir websockets     && useradd -m -u 1000 user && mkdir -p /app/data && chown -R user:user /app
USER user
COPY --chown=user:user undertow ./undertow
COPY --chown=user:user abi ./abi
ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
# Set UNDERTOW_RPC to your own endpoint (for example Alchemy) for faster, steadier refreshes.
EXPOSE 8000
CMD ["python", "-m", "undertow.server", "--refresh", "--host", "0.0.0.0"]  # port: $PORT if the host sets it, else 8000
