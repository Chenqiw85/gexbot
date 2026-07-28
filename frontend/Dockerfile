FROM node:22-slim AS deps

WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install

FROM node:22-slim AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-slim
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app ./
CMD ["npm", "run", "start"]
