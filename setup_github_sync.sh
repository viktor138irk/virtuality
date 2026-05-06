#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/opt/virtuality"
DEFAULT_BRANCH="main"

if [[ "$EUID" -ne 0 ]]; then
  echo "Ошибка: запусти от root: sudo bash setup_github_sync.sh"
  exit 1
fi

apt update
apt install -y git openssh-client

if [[ ! -d "$PROJECT_DIR" ]]; then
  echo "Ошибка: папка проекта не найдена: $PROJECT_DIR"
  echo "Сначала запусти install_virtuality_node.sh"
  exit 1
fi

cd "$PROJECT_DIR"

read -rp "GitHub repo URL [git@github.com:viktor138irk/virtuality.git]: " GITHUB_REPO
GITHUB_REPO="${GITHUB_REPO:-git@github.com:viktor138irk/virtuality.git}"

read -rp "Git author name [Virtuality Node]: " GIT_NAME
GIT_NAME="${GIT_NAME:-Virtuality Node}"

read -rp "Git author email [virtuality@local]: " GIT_EMAIL
GIT_EMAIL="${GIT_EMAIL:-virtuality@local}"

git config --global user.name "$GIT_NAME"
git config --global user.email "$GIT_EMAIL"

cat > "$PROJECT_DIR/git_sync.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/opt/virtuality"
cd "$PROJECT_DIR"

MESSAGE="${1:-Auto sync $(date '+%Y-%m-%d %H:%M:%S')}"

git add .
git commit -m "$MESSAGE" || {
  echo "Нет изменений для commit."
  exit 0
}

git push origin main
EOF

chmod +x "$PROJECT_DIR/git_sync.sh"

if [[ ! -d "$PROJECT_DIR/.git" ]]; then
  git init
fi

git branch -M "$DEFAULT_BRANCH"

if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$GITHUB_REPO"
else
  git remote add origin "$GITHUB_REPO"
fi

git add .
git commit -m "Initial Virtuality node base" || true
git push -u origin "$DEFAULT_BRANCH"

echo "GitHub sync готов."
echo "Ручная синхронизация: sudo /opt/virtuality/git_sync.sh \"Описание изменений\""
