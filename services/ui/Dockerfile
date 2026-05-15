# syntax=docker/dockerfile:1.7
# Phase 0: serve a static "ok" page. Real React/Vite app comes in Phase 3.
FROM nginx:1.27-alpine

COPY services/ui/index.html /usr/share/nginx/html/index.html

EXPOSE 80
