#!/usr/bin/env bash
# data.json 하나만 들어 있는 커밋을 data 브랜치에 덮어쓴다 (기록이 쌓이지 않음)
set -euo pipefail
tmp=$(mktemp -d)
cp data.json "$tmp/"
cd "$tmp"
git init -q -b data
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add data.json
git commit -qm "data: $(TZ=Asia/Seoul date '+%F %H:%M')"
git push -qf "https://x-access-token:${GH_TOKEN}@github.com/${REPO}.git" data
echo "data 브랜치 업데이트 완료"
