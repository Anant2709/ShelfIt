FROM node:20-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

# Vite inlines env vars at build time, so the API base must be known here.
ARG VITE_API_BASE=http://localhost:8000/api
ENV VITE_API_BASE=$VITE_API_BASE

COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
