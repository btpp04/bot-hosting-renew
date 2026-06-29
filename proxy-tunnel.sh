#!/usr/bin/env bash
# proxy-tunnel.sh - Set up proxy tunnel for GitHub Actions
set -e

PROXY="$1"
if [ -z "$PROXY" ]; then
  echo "PROXY=" >> $GITHUB_ENV
  exit 0
fi

if [[ "$PROXY" =~ ^(socks5|http|https):// ]]; then
  echo "PROXY=$PROXY" >> $GITHUB_ENV
  exit 0
fi

if [[ "$PROXY" =~ ^hysteria2:// ]]; then
  echo "Setting up hy2 tunnel..."
  wget -qO sb.tar.gz https://github.com/SagerNet/sing-box/releases/download/v1.13.7/sing-box-1.13.7-linux-amd64.tar.gz
  tar -xzf sb.tar.gz && mv sing-box-*/sing-box . && chmod +x ./sing-box
  STRIPPED="${PROXY#hysteria2://}"; STRIPPED="${STRIPPED%%#*}"
  NO_QUERY="${STRIPPED%%\?*}"; QUERY="${STRIPPED#*\?}"
  AUTH_HOST="${NO_QUERY%@*}"; HOSTPORT="${NO_QUERY##*@}"
  PASS="${AUTH_HOST#*:}"; HOST="${HOSTPORT%:*}"; PORT="${HOSTPORT#*:}"
  PEER=$(echo "$QUERY" | tr '&' '\n' | grep '^peer=' | sed 's/^peer=//')
  PEER="${PEER:-www.bing.com}"
  ALPN=$(echo "$QUERY" | tr '&' '\n' | grep '^alpn=' | sed 's/^alpn=//')
  ALPN="${ALPN:-h3}"
  INSEC=""
  [[ "$(echo "$QUERY" | tr '&' '\n' | grep '^insecure=' | sed 's/^insecure=//')" == "1" ]] && INSEC='"insecure": true,'
  cat > config.json <<JSONEOF
{"log":{"level":"warn"},"inbounds":[{"type":"socks","tag":"socks-in","listen":"127.0.0.1","listen_port":1080}],"outbounds":[{"type":"hysteria2","tag":"hy2-out","server":"${HOST}","server_port":${PORT},"password":"${PASS}","tls":{"enabled":true,"server_name":"${PEER}","alpn":["${ALPN}"],${INSEC}}}]}
JSONEOF
  ./sing-box run -c config.json &
  sleep 3
  IP=$(curl -sS --max-time 5 --socks5 127.0.0.1:1080 https://api.ipify.org 2>/dev/null || echo "tunnel_failed")
  echo "Tunnel IP: $IP"
  echo "PROXY=socks5://127.0.0.1:1080" >> $GITHUB_ENV
  exit 0
fi

echo "Unsupported proxy type: ${PROXY:0:30}"
echo "PROXY=" >> $GITHUB_ENV
