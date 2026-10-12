# vendor/

`perpl-cli-0.2.9-linux-amd64` is Perpl's official command-line tool (https://crates.io/crates/perpl-cli, version 0.2.9), compiled on
Linux x86-64 so hosts do not have to spend 10 to 50 minutes compiling it. It was extracted from the Docker image built by the main
`Dockerfile`, which runs `cargo install perpl-cli --version 0.2.9 --locked`.

* Size: 14.7 MB. SHA-256: `82D4FF77DEC3FAC9C6B3E586820733E2743C1FA2FA80105018DCD3CE04EDFD15`
* Depends only on libc, libm and libgcc (standard on Debian and Ubuntu); no OpenSSL.
* Do not trust a binary you cannot verify: rebuild it yourself with the main `Dockerfile` (or `cargo install perpl-cli --version 0.2.9 --locked`),
  or use that `Dockerfile` instead of `Dockerfile.fast`. The only thing it does is read the chain; the dashboard never calls its `order` commands.
