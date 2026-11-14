#!/usr/bin/env bash
# Install the `ws` controller + Hermes skill on the always-on VPS (where Hermes runs).
#   bash install_vps.sh
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
PREFIX="${WS_PREFIX:-$HOME/.modal-ws}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SKILL_DST="$HERMES_HOME/skills/engineering/modal-workstation"

echo "==> Installing to $PREFIX"
mkdir -p "$PREFIX" "$HOME/.local/bin"
rsync -a --delete --exclude '.venv' --exclude '.git' --exclude '__pycache__' "$SRC/" "$PREFIX/app/" 2>/dev/null || {
  rm -rf "$PREFIX/app"; cp -r "$SRC" "$PREFIX/app"; }

if command -v uv >/dev/null 2>&1; then
  uv venv -q "$PREFIX/venv" --python 3.11 || uv venv -q "$PREFIX/venv"
  uv pip install -q --python "$PREFIX/venv/bin/python" -U modal
else
  python3 -m venv "$PREFIX/venv"
  "$PREFIX/venv/bin/pip" install -q -U pip modal
fi

cat > "$HOME/.local/bin/ws" <<EOF
#!/usr/bin/env bash
exec "$PREFIX/venv/bin/python" "$PREFIX/app/ws.py" "\$@"
EOF
chmod +x "$HOME/.local/bin/ws"
ln -sf "$PREFIX/venv/bin/modal" "$HOME/.local/bin/modal"

echo "==> Installing Hermes skill to $SKILL_DST"
mkdir -p "$SKILL_DST"
cp "$SRC/hermes-skill/modal-workstation/SKILL.md" "$SKILL_DST/SKILL.md"

case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *)
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
  echo "   (added ~/.local/bin to PATH in ~/.bashrc - restart the Hermes gateway so it sees it)";;
esac

cat <<'EOT'

==> Done. Next steps:
 1) Authenticate Modal on this VPS (pick one):
      modal token new                      # opens a link, approve in your browser
      modal token set --token-id ak-... --token-secret as-...
 2) Set a spending limit in the Modal dashboard (Settings -> Usage/Billing) e.g. $30.
 3) Build the image once (~10-15 min, then cached):
      ws build
 4) Test:
      ws desktop start --profile light
      ws desktop stop
 5) In Hermes (Telegram/CLI):  /modal-workstation شغل الديسكتوب
EOT
