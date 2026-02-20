FROM python:3.12-slim AS builder
WORKDIR /build

# Step 1: CPU-only torch — pip will see it's installed
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Step 2: requirements.txt — pip sees torch is already satisfied
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Step 3: Strip bloat from installed packages to reduce image size
# Removes ~150-200MB of test data, static libs, type stubs, and caches
RUN find /usr/local/lib/python3.12/site-packages -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null; \
    find /usr/local/lib/python3.12/site-packages -type d -name "tests" -exec rm -rf {} + 2>/dev/null; \
    find /usr/local/lib/python3.12/site-packages -type d -name "test" -exec rm -rf {} + 2>/dev/null; \
    rm -rf /usr/local/lib/python3.12/site-packages/torch/test \
           /usr/local/lib/python3.12/site-packages/torch/lib/*.a \
           /usr/local/lib/python3.12/site-packages/torch/include \
           /usr/local/lib/python3.12/site-packages/torch/_inductor/codegen/triton_templates; \
    find /usr/local/lib/python3.12/site-packages -name "*.pyc" -delete 2>/dev/null; \
    find /usr/local/lib/python3.12/site-packages -name "*.pyi" -delete 2>/dev/null; \
    true

# ---- Runtime stage ----
FROM python:3.12-slim
WORKDIR /app

# Copy slimmed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# System deps + Playwright Chromium in ONE layer to minimize disk usage
# Merging apt + playwright avoids duplicate apt list storage across layers
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libatk-bridge2.0-0 libdrm2 libxkbcommon0 libgbm1 libasound2 \
    libxshmfence1 libx11-xcb1 libjpeg62-turbo libwebp7 libpng16-16 \
    fonts-liberation gosu && \
    mkdir -p /opt/playwright-browsers && \
    playwright install --with-deps chromium && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# NOTE: BLIP model is NOT downloaded at build time to save ~1.7GB of disk.
# It will be lazily downloaded on first use at runtime (~30s one-time).
# The captioner gracefully falls back to color-based captions if unavailable.

COPY . .
RUN groupadd -g 1000 scraper && useradd -u 1000 -g scraper -m -s /bin/bash scraper && \
    chmod +x /app/entrypoint.sh

EXPOSE 1629
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:1629/api/health')"]
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "1629", "--workers", "1"]
