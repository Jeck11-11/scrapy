# syntax=docker/dockerfile:1

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install build dependencies required by Scrapy and its optional extras.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        build-essential \
        libffi-dev \
        libxml2-dev \
        libxslt-dev \
        libssl-dev \
        python3-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install project dependencies.
COPY pyproject.toml README.rst ./
COPY scrapy ./scrapy
COPY extras ./extras
COPY conftest.py ./

RUN pip install --upgrade --no-cache-dir pip \
    && pip install --no-cache-dir .

# Copy the remaining project files (documentation, configs, etc.).
COPY . ./

# Scrapy's CLI is a convenient entry point; override CMD to run other tools.
ENTRYPOINT ["scrapy"]
CMD ["--help"]
