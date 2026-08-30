# syntax=docker/dockerfile:1

FROM node:20-alpine AS builder
WORKDIR /app

COPY package*.json ./
RUN npm ci --no-audit --no-fund

COPY . .
ARG VITE_API_BASE=/api
ENV VITE_API_BASE=${VITE_API_BASE}
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine AS production

COPY --chown=101:101 nginx.conf /etc/nginx/nginx.conf
COPY --chown=101:101 site.conf /etc/nginx/conf.d/default.conf
COPY --chown=101:101 site-ssl.conf.template /etc/nginx/tls/site-ssl.conf.template
COPY --chown=101:101 --chmod=755 configure-tls.sh /docker-entrypoint.d/40-configure-tls.sh

RUN mkdir -p /tmp/nginx/client_temp \
    /tmp/nginx/proxy_temp \
    /tmp/nginx/fastcgi_temp \
    /tmp/nginx/uwsgi_temp \
    /tmp/nginx/scgi_temp

COPY --chown=101:101 --from=builder /app/dist /usr/share/nginx/html

EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://127.0.0.1:8080/nginx-health || exit 1

CMD ["nginx", "-g", "daemon off;"]
