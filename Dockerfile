# syntax=docker/dockerfile:1.7

# ---------- builder stage ----------
FROM python:3.13-alpine AS builder

WORKDIR /build

# Only the files needed to install the package — see .dockerignore for what's
# excluded from the build context.
COPY pyproject.toml ./
COPY src/ ./src/

# Install pgsleuth into an isolated venv that we copy into the runtime stage.
# --no-cache-dir keeps the pip wheel cache out of the layer.
# --no-compile skips .pyc generation (we don't need them; PYTHONDONTWRITEBYTECODE
# in the runtime stage prevents them being created at runtime).
RUN python -m venv /venv \
 && /venv/bin/pip install --no-cache-dir --no-compile --upgrade pip \
 && /venv/bin/pip install --no-cache-dir --no-compile .


# ---------- runtime stage ----------
FROM python:3.13-alpine

# Non-root user, no shell, no home directory. pgsleuth reads from a DB and
# writes to stdout/stderr; it has no business with the filesystem.
RUN adduser -D -H -s /sbin/nologin pgsleuth

COPY --from=builder /venv /venv

ENV PATH="/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER pgsleuth

ENTRYPOINT ["pgsleuth"]
CMD ["--help"]
