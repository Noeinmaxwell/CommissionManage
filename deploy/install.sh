#!/usr/bin/env bash
# Install the service in an existing checkout at its production path.
set -euo pipefail
cd /home/code/CommissionManage
host=$(python3 -c 'import ipaddress, sys; print(ipaddress.IPv4Address(sys.argv[1]))' "${1:-127.0.0.1}")
probe=$host
if [[ $host == 0.0.0.0 ]]; then probe=127.0.0.1; fi
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
if [[ $host != 127.* ]]; then
    install -d -m 0700 /etc/commission-manage
    # Publish the complete file atomically and preserve credentials on reinstall.
    if [[ ! -e /etc/commission-manage/access.env ]]; then
        credentials=$(mktemp /etc/commission-manage/.access.XXXXXX)
        python3 -c 'import secrets; print("COMMISSION_ADMIN_PASSWORD=" + secrets.token_urlsafe(32)); print("COMMISSION_VIEWER_PASSWORD=" + secrets.token_urlsafe(32))' > "$credentials"
        ln "$credentials" /etc/commission-manage/access.env || test -s /etc/commission-manage/access.env
        rm -f "$credentials"
    fi
fi
unit=$(mktemp)
trap 'rm -f "$unit"' EXIT
sed "s/--host 127.0.0.1/--host $host/" deploy/commission-manage.service > "$unit"
install -m 0644 "$unit" /etc/systemd/system/commission-manage.service
systemctl daemon-reload
systemctl enable commission-manage.service
systemctl restart commission-manage.service
curl --noproxy '*' --retry 8 --retry-delay 1 --retry-connrefused --fail --silent --show-error "http://${probe}:5444/healthz" >/dev/null
echo "服务已启动：http://${probe}:5444/"
