# syntax=docker/dockerfile:1

FROM node:20-alpine AS builder
WORKDIR /app

# Install dependencies based on lockfile/package manifest
COPY package*.json ./
RUN npm install

# Copy source and build
COPY . .
ARG VITE_API_BASE=/api
ENV VITE_API_BASE=${VITE_API_BASE}
RUN npm run build

FROM nginx:1.27-alpine AS production

# Copy custom Nginx configuration
COPY nginx.conf /etc/nginx/nginx.conf
COPY site.conf /etc/nginx/conf.d/default.conf

RUN mkdir -p /var/cache/nginx /var/run/nginx \
    && mkdir -p /tmp/nginx/client_temp \
               /tmp/nginx/proxy_temp \
               /tmp/nginx/fastcgi_temp \
               /tmp/nginx/uwsgi_temp \
               /tmp/nginx/scgi_temp \
    && chown -R 0:0 /var/cache/nginx /var/run/nginx /tmp/nginx \
    && chmod -R g+rwX /var/cache/nginx /var/run/nginx /tmp/nginx

# Copy built assets
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
