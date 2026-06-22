FROM node:20-slim

WORKDIR /app

COPY api/package.json ./package.json
RUN npm install

COPY api ./

RUN npm run build

CMD ["node", "dist/main.js"]
