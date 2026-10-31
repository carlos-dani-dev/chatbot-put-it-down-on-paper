#!/usr/bin/env bash
# Deploy na EC2 — chamado pelo GitHub Actions logo depois do `git pull` (ver .github/workflows/deploy.yml).
# Também dá pra rodar na mão, de dentro do projeto na EC2:  bash deploy/deploy.sh
set -euo pipefail

cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"   # o poetry fica aqui, e uma sessão SSH não interativa não carrega o .bashrc

echo "==> dependências"
poetry install --no-interaction

echo "==> migrações do banco"
poetry run alembic upgrade head

echo "==> reiniciando serviços"
sudo systemctl restart arnaldo-webhook arnaldo-site

# espera cada serviço responder; se algum não subir, mostra o log dele e falha o deploy
check() {
    local name=$1 url=$2
    for _ in $(seq 1 15); do
        if curl -fsS -o /dev/null "$url"; then
            echo "    $name ok"
            return 0
        fi
        sleep 2
    done
    echo "!!! $name não respondeu em $url — últimas linhas do log:"
    sudo journalctl -u "$name" -n 40 --no-pager
    return 1
}

echo "==> conferindo"
check arnaldo-webhook http://127.0.0.1:8000/healthy
check arnaldo-webhook http://127.0.0.1:8000/sentry-debug
check arnaldo-site http://127.0.0.1:8002/

echo "==> deploy ok: $(git log -1 --format='%h %s')"
