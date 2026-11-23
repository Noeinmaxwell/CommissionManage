#!/usr/bin/env bash
# Run in a checkout on a computer with access to the destination over Tailscale SSH.
set -euo pipefail
cd "$(dirname "$0")"
branch=$(git symbolic-ref --short HEAD)
git check-ref-format --branch "$branch" >/dev/null
bundle=$(mktemp)
trap 'rm -f "$bundle"' EXIT
git bundle create "$bundle" "refs/heads/$branch"

remote_script=$(cat <<'REMOTE'
set -eu
umask 022
target=/home/code/CommissionManage
origin=https://github.com/Noeinmaxwell/CommissionManage.git
bundle=$(mktemp)
trap 'rm -f "$bundle"' EXIT
cat > "$bundle"
branch=$(git bundle list-heads "$bundle" | sed -n 's|^[^ ]* refs/heads/||p')
git check-ref-format --branch "$branch" >/dev/null
mkdir -p "$target"
cd "$target"
if [ ! -d .git ]; then
    if [ -n "$(ls -A)" ]; then
        echo "目标目录已有文件且不是 Git 仓库，部署已停止。" >&2
        exit 1
    fi
    git init --initial-branch="$branch" .
    git remote add origin "$origin"
fi
case "$(git remote get-url origin)" in
    "$origin"|https://github.com/Noeinmaxwell/CommissionManage|git@github.com:Noeinmaxwell/CommissionManage.git) ;;
    *) echo "目标目录的 origin 指向其他仓库。" >&2; exit 1 ;;
esac
if [ -n "$(git status --porcelain)" ]; then
    echo "目标工作树有未提交更改，部署已停止。" >&2
    exit 1
fi
git fetch "$bundle" "refs/heads/$branch"
commit=$(git rev-parse FETCH_HEAD)
if git show-ref --verify --quiet "refs/heads/$branch"; then
    git switch "$branch"
    git merge --ff-only "$commit"
else
    git switch -c "$branch" "$commit"
fi
git config "branch.$branch.remote" origin
git config "branch.$branch.merge" "refs/heads/$branch"
bash deploy/install.sh 100.79.13.109
REMOTE
)
tailscale ssh root@ubuntu-server-codex "$remote_script" < "$bundle"
