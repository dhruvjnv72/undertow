#!/usr/bin/env bash
# One-command setup for an Ubuntu VM (for example Oracle Cloud "Always Free").
#   bash oracle_setup.sh your-name.duckdns.org
# It installs Docker, builds Undertow from this repository, runs it so it restarts by itself,
# and puts Caddy in front of it to give you automatic HTTPS for the domain you pass in.
# Needs: the domain already points at this VM's public IP, and ports 80 and 443 are open in the cloud firewall.
# No keys or secrets are used. Set UNDERTOW_RPC yourself only if you have your own Monad RPC URL.
set -euo pipefail

DOMAIN="${1:?usage: bash oracle_setup.sh your-name.duckdns.org}"

sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io git iptables-persistent
sudo systemctl enable --now docker

# Oracle's Ubuntu images block web ports inside the VM by default; open them.
sudo iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || sudo iptables -I INPUT 6 -p tcp --dport 80 -j ACCEPT
sudo iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || sudo iptables -I INPUT 6 -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save

if [ -d "$HOME/undertow" ]; then git -C "$HOME/undertow" pull; else git clone https://github.com/dhruvjnv72/undertow.git "$HOME/undertow"; fi
cd "$HOME/undertow"

# The full Dockerfile builds the Perpl CLI from source, so it also works on ARM machines. The first build takes a while.
sudo docker build -t undertow .

sudo docker network create ut 2>/dev/null || true
sudo docker rm -f undertow caddy 2>/dev/null || true
sudo docker run -d --name undertow --restart always --network ut \
  -e UNDERTOW_HISTORY_DAYS=1 ${UNDERTOW_RPC:+-e UNDERTOW_RPC="$UNDERTOW_RPC"} undertow

printf '%s {\n  reverse_proxy undertow:8000\n}\n' "$DOMAIN" > Caddyfile
sudo docker run -d --name caddy --restart always --network ut -p 80:80 -p 443:443 \
  -v "$PWD/Caddyfile:/etc/caddy/Caddyfile" -v caddy_data:/data caddy:2

echo "Done. Give it a couple of minutes, then open https://$DOMAIN/api/status"
