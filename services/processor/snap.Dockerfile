# services/processor/snap.Dockerfile
#
# ESA SNAP 10.0 sidecar container.
#
# This image is intentionally large (JRE + SNAP desktop + S1TBX modules).
# It runs as a non-root user, on the internal bridge network, with no
# host networking and no --privileged flag (OPSEC §6).
#
# SNAP version is pinned to 10.0 for reproducibility.  Check for updates at:
# https://step.esa.int/main/download/snap-download/
#
# The container does NOT run a long-lived process.  The processor worker
# invokes ``gpt`` via ``docker exec sar-viewer-snap gpt <graph.xml>`` or
# via a shared scratch volume.  We keep the container running with a
# ``tail -f /dev/null`` entrypoint so the worker can exec into it.
#
# Memory note: SNAP GPT is configured via snap.Dockerfile ARG to use up to
# 14 GB RAM (suitable for a 16 GB host — leaves 2 GB for OS + other
# services).  Override with build arg SNAP_MAX_MEM.

ARG SNAP_VERSION=10.0
ARG SNAP_MAX_MEM=14G

FROM ubuntu:22.04 AS snap-installer

ARG SNAP_VERSION
# SNAP Linux installer URL (esa.int official CDN)
ARG SNAP_INSTALLER_URL="https://download.esa.int/step/snap/${SNAP_VERSION}/installers/esa-snap_sentinel_unix_${SNAP_VERSION}.sh"

RUN apt-get update && apt-get install -y --no-install-recommends \
        wget \
        ca-certificates \
        unzip \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /snap-install

# Download SNAP installer — pinned version for reproducibility
RUN wget -q "${SNAP_INSTALLER_URL}" -O snap-installer.sh \
    && chmod +x snap-installer.sh

# Run headless SNAP installation into /opt/snap
# -q  = quiet, -dir = install directory, -o = overwrite
RUN ./snap-installer.sh -q -dir /opt/snap

# ---- runtime stage ----
FROM ubuntu:22.04

ARG SNAP_MAX_MEM

RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 \
        libfreetype6 \
        fontconfig \
    && rm -rf /var/lib/apt/lists/*

# Copy installed SNAP from builder stage
COPY --from=snap-installer /opt/snap /opt/snap

ENV PATH="/opt/snap/bin:${PATH}"
# Override SNAP's internal JVM heap via the snap config file.
# We write a gpt.vmoptions file that SNAP reads on startup.
RUN echo "-Xmx${SNAP_MAX_MEM}" > /opt/snap/bin/gpt.vmoptions \
    && echo "-Xms512m"          >> /opt/snap/bin/gpt.vmoptions \
    && echo "-XX:+UseG1GC"      >> /opt/snap/bin/gpt.vmoptions

# Non-root user
RUN groupadd -r snap && useradd -r -g snap snap

# Scratch directory (mounted from host at /scratch)
RUN mkdir -p /scratch && chown snap:snap /scratch

# SNAP user home (for caches, orbit downloads, DEM cache)
RUN mkdir -p /home/snap/.snap/auxdata && chown -R snap:snap /home/snap
ENV HOME="/home/snap"

USER snap

# Keep the container alive so the processor worker can exec into it.
# In production the worker calls: docker exec sar-viewer-snap gpt <args>
ENTRYPOINT ["tail", "-f", "/dev/null"]
