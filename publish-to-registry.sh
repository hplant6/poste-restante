#!/usr/bin/env bash
# Lists Poste Restante in the official MCP registry. Safe to re-run.
set -euo pipefail
cd /tmp
echo "1/3 Downloading the official publisher..."
url="$(curl -s https://api.github.com/repos/modelcontextprotocol/registry/releases/latest \
  | python3 -c "import sys,json;print(next(a['browser_download_url'] for a in json.load(sys.stdin)['assets'] if 'linux_amd64' in a['name'] and a['name'].endswith('.tar.gz')))")"
curl -sL "$url" | tar xz mcp-publisher
echo "2/3 Proving you own itshenry.me..."
./mcp-publisher login http --domain itshenry.me \
  --private-key "$(openssl pkey -in ~/.config/palimpsest/registry-key.pem -outform DER | tail -c 32 | xxd -p -c 64)"
echo "3/3 Publishing..."
cd ~/poste-restante && /tmp/mcp-publisher publish
echo "Done. Poste Restante is in the MCP registry."
