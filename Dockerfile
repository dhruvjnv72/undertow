# Hugging Face Space build (branch hf-space). Downloads the prebuilt Perpl CLI and verifies its checksum instead of compiling it.
FROM python:3.13-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL -o /usr/local/bin/perpl-cli https://raw.githubusercontent.com/dhruvjnv72/undertow/main/vendor/perpl-cli-0.2.9-linux-amd64     && echo "82d4ff77dec3fac9c6b3e586820733e2743c1fa2fa80105018dcd3ce04edfd15  /usr/local/bin/perpl-cli" | sha256sum -c -     && chmod 755 /usr/local/bin/perpl-cli     && pip install --no-cache-dir websockets eth-abi eth-utils 'eth-hash[pycryptodome]'     && useradd -m -u 1000 user && mkdir -p /app/data && chown -R user:user /app
USER user
WORKDIR /app
COPY --chown=user:user undertow ./undertow
COPY --chown=user:user abi ./abi
ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
EXPOSE 8000
CMD ["python", "-m", "undertow.server", "--refresh", "--host", "0.0.0.0", "--port", "8000"]
