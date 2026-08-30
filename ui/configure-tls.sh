#!/bin/sh
set -eu

domain="${SSL_DOMAIN:-}"

if [ -z "$domain" ]; then
    exit 0
fi

if ! printf '%s' "$domain" | grep -Eq '^[A-Za-z0-9.-]+$'; then
    echo "Invalid SSL_DOMAIN: $domain" >&2
    exit 1
fi

certificate="/etc/letsencrypt/live/$domain/fullchain.pem"
private_key="/etc/letsencrypt/live/$domain/privkey.pem"

if [ ! -r "$certificate" ] || [ ! -r "$private_key" ]; then
    echo "Certificate for $domain not found; starting in HTTP bootstrap mode"
    exit 0
fi

sed "s/__SSL_DOMAIN__/$domain/g" \
    /etc/nginx/tls/site-ssl.conf.template \
    > /etc/nginx/conf.d/default.conf

# Reload periodically so renewed certificates are picked up without downtime.
(while sleep 6h; do nginx -s reload; done) &