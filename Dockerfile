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
COPY undertow ./undertow
COPY abi ./abi
ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
# Set UNDERTOW_RPC to your own endpoint (for example Alchemy) for faster, steadier refreshes.
EXPOSE 8000
CMD ["python", "-m", "undertow.server", "--refresh", "--host", "0.0.0.0", "--port", "8000"]
