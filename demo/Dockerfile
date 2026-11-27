# Recorder image: the same Playwright base as e2e/ (browsers for 1.65.1 are baked in) plus ffmpeg and a font
# for the captions. The recorder drives two browsers, then ffmpeg composes the 1920x1080 captioned video.
FROM mcr.microsoft.com/playwright:v1.65.1-noble
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /demo
COPY package.json package-lock.json ./
RUN npm ci
COPY recorder ./recorder
CMD ["node", "recorder/record.mjs"]
