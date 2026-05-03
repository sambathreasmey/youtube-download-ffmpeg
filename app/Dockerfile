FROM python:3.12-slim

# =========================
# SYSTEM DEPENDENCIES
# =========================
RUN apt-get update && apt-get install -y \
    docker.io \
    curl \
    ffmpeg \
    unzip \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# =========================
# DENO INSTALL (FIXED + STABLE)
# =========================
RUN curl -fsSL https://deno.land/install.sh | sh

# IMPORTANT: make deno globally available
ENV DENO_INSTALL="/root/.deno"
ENV PATH="$DENO_INSTALL/bin:$PATH"

# verify (optional but useful for debugging)
RUN deno --version

# =========================
# APP SETUP
# =========================
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN pip install --no-cache-dir -U yt-dlp

COPY . .

EXPOSE 5000