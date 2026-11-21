#!/usr/bin/env bash
# Deploy Poste Restante to Bluehost in ONE ssh connection (cPHulk locks out repeated logins).
# Site:  ~/public_html/itshenry-portfolio/poste-restante/   (pages, game download, office/, mcp/)
# Data:  ~/poste-restante-web/                              (outside the web root; letters, state, the sealed Undeliverable)
# Redeploys never touch existing data files except undeliverable.json (the fixed text).
set -euo pipefail
umask 022   # Apache must be able to read everything in the site (a 077 umask once took the site down)
cd "$(dirname "$0")/.."
python3 -I tests/test_solver.py | tail -1
python3 -I tests/test_office.py | tail -1
STAGE=$(mktemp -d); SITE="$STAGE/public_html/itshenry-portfolio/poste-restante"
mkdir -p "$SITE/office" "$SITE/mcp" "$SITE/undeliverable" "$STAGE/poste-restante-web"
cp server/web/index.html server/web/llms.txt server/web/.htaccess poste_restante.py "$SITE/"
cp server/web/undeliverable/index.html "$SITE/undeliverable/"
cp server/office/office.cgi server/office/.htaccess poste_restante.py "$SITE/office/"
cp server/mcp/mcp.cgi server/mcp/.htaccess "$SITE/mcp/"
cp ~/.config/poste-restante/undeliverable.json "$STAGE/poste-restante-web/"
# the owner's moderation key (server keeps it as a JSON string, outside the web root)
[ -f ~/.config/poste-restante/admin_token ] && python3 -c "import json,os;print(json.dumps(open(os.path.expanduser('~/.config/poste-restante/admin_token')).read().strip()))" > "$STAGE/poste-restante-web/admin_token.json"
chmod -R u=rwX,go=rX "$SITE"; chmod 755 "$SITE/office/office.cgi" "$SITE/mcp/mcp.cgi"; chmod 644 "$SITE/office/poste_restante.py" "$SITE/poste_restante.py"
chmod 700 "$STAGE/poste-restante-web"; chmod 600 "$STAGE/poste-restante-web/"*.json
tar czf - -C "$STAGE" . | ssh -o ConnectTimeout=20 bluehost 'cd ~ && tar xzf - --no-overwrite-dir && chmod -R u=rwX,go=rX public_html/itshenry-portfolio/poste-restante && chmod 755 public_html/itshenry-portfolio/poste-restante/office/office.cgi public_html/itshenry-portfolio/poste-restante/mcp/mcp.cgi && chmod 700 poste-restante-web && echo deployed'
rm -rf "$STAGE"
echo "Check: curl -s https://itshenry.me/poste-restante/office/office.cgi  (and curl -X PURGE <url> if the edge cache holds an old copy)"
