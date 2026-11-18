# Build host for Silvering (Android 6.0.1, CAF LA.BR.1.2.7).
# Android 6 builds with OpenJDK 7, GNU make 3.81 and Python 2.7: Ubuntu 14.04.
FROM ubuntu:14.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        bc bison build-essential ca-certificates ccache curl flex g++-multilib \
        gcc-multilib genisoimage git gnupg gperf lib32ncurses5-dev lib32z-dev libc6-dev-i386 \
        libgl1-mesa-dev libx11-dev libxml2-utils openjdk-7-jdk python rsync \
        unzip x11proto-core-dev xsltproc zip zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

ARG UID=1000
ARG GID=1000
RUN groupadd -g ${GID} builder && useradd -m -u ${UID} -g ${GID} -s /bin/bash builder

ENV LC_ALL=C \
    USER=builder \
    USE_CCACHE=1 \
    CCACHE_DIR=/ccache \
    JACK_SERVER_VM_ARGUMENTS="-Dfile.encoding=UTF-8 -XX:+TieredCompilation -Xmx4g"

USER builder
WORKDIR /src
