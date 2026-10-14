#!/usr/bin/env bash
# Boots the shipped server/docker-compose.yml the way an operator does (the file
# plus .env plus compose.override.yml in one directory), on a fake domain with no
# outbound network. Deploys a site, checks routing through Traefik, routes a
# hostname through the shared file-provider directory, then upgrades the server
# image in place and checks that the site survived.
#
# Usage: BUZZ_FROM_IMAGE=<image> BUZZ_TO_IMAGE=<image> server/tests/compose/boot-test.sh
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
from_image=${BUZZ_FROM_IMAGE:?Set BUZZ_FROM_IMAGE to the image the stack starts on}
to_image=${BUZZ_TO_IMAGE:?Set BUZZ_TO_IMAGE to the image the stack upgrades to}
export BUZZ_TEST_PROJECT=${BUZZ_TEST_PROJECT:-buzz-compose-test}
export BUZZ_SERVER_IMAGE=$from_image
domain=buzz.test
site=hello
marker="compose boot test $RANDOM"

workdir=$(mktemp -d)
cp "$here/../../docker-compose.yml" "$workdir/docker-compose.yml"
cp "$here/compose.test.yml" "$workdir/compose.override.yml"
cat > "$workdir/.env" <<EOF
BUZZ_DOMAIN=$domain
GITHUB_CLIENT_ID=compose-test
GITHUB_CLIENT_SECRET=compose-test
BUZZ_CLOUDFLARE_DNS_API_TOKEN=compose-test
BUZZ_ACME_DIRECTORY_URL=https://127.0.0.1:9/directory
EOF
mkdir "$workdir/site"
echo "<h1>$marker</h1>" > "$workdir/site/index.html"
(cd "$workdir/site" && python3 -m zipfile -c site.zip index.html)

stack() {
  docker compose --project-directory "$workdir" -p "$BUZZ_TEST_PROJECT" "$@"
}

https() {
  local url=$1
  shift
  stack run --rm -T client --silent --show-error --insecure \
    --connect-to "::traefik:443" "$@" "https://$url"
}

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    stack logs --no-color --tail 100 || true
  fi
  stack --profile client down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$workdir"
  exit "$status"
}
trap cleanup EXIT

expect() {
  local description=$1 expected=$2 actual=$3
  if [[ "$actual" != *"$expected"* ]]; then
    echo "FAIL: $description: expected '$expected', got '$actual'"
    exit 1
  fi
  echo "ok: $description"
}

retry() {
  local output
  for _ in $(seq 1 20); do
    if output=$("$@" 2>&1); then
      echo "$output"
      return 0
    fi
    sleep 1
  done
  echo "$output"
  return 1
}

echo "Starting the stack on $from_image"
stack up --detach --wait --wait-timeout 120

expect "health through Traefik" '{"status":"' "$(retry https "$domain/health" --fail)"

token=$(stack exec -T server python - < "$here/seed_session.py")
deploy=$(https "$domain/deploy" --fail \
  --header "Authorization: Bearer $token" \
  --header "X-Buzz-Site: $site" \
  --form "file=@/site/site.zip")
expect "deploy through Traefik" "\"site_name\":\"$site\"" "$deploy"
expect "site served on its subdomain" "$marker" "$(https "$site.$domain/" --fail)"

stack exec -T server sh -c 'cat > /traefik-dynamic/compose-test.yaml' <<EOF
http:
  routers:
    compose-test:
      rule: Host(\`custom.example\`)
      entryPoints: [websecure]
      service: buzz@docker
      middlewares: [compose-test-host]
      tls: {}
  middlewares:
    compose-test-host:
      headers:
        customRequestHeaders:
          Host: $site.$domain
EOF
expect "file-provider router from the shared directory" "$marker" \
  "$(retry https custom.example/ --fail)"

echo "Upgrading the server to $to_image"
export BUZZ_SERVER_IMAGE=$to_image
stack up --detach --wait --wait-timeout 120
expect "server runs the new image" "$to_image" \
  "$(stack ps server --format '{{.Image}}')"
expect "health after upgrade" '{"status":"' "$(retry https "$domain/health" --fail)"
expect "site survived the upgrade" "$marker" "$(retry https "$site.$domain/" --fail)"
