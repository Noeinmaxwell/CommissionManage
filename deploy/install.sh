#!/usr/bin/env bash
# Install the service in an existing checkout at its production path.
set -euo pipefail
cd /home/code/CommissionManage
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    echo "需要 Python 3.10 或更新版本。" >&2
    exit 1
fi
if ! python3 -m venv .venv; then
    apt-get update
    apt-get install -y python3-venv
    python3 -m venv .venv
fi
.venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
install -m 0644 deploy/commission-manage.service /etc/systemd/system/commission-manage.service
systemctl daemon-reload
systemctl enable commission-manage.service
systemctl restart commission-manage.service
curl --noproxy '*' --retry 8 --retry-delay 1 --retry-connrefused --fail --silent --show-error http://100.79.13.109:5444/api/stats >/dev/null
echo "服务已启动：http://100.79.13.109:5444/"
