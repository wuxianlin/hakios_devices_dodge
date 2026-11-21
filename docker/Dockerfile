# The build container of this port: everything the kernel, the loader and the images need,
# in one image (the same set the CI installs on its runner, minus what /theus/theos carries).
#
#   docker build -t hakios-build:24.04 --build-arg UID=$(id -u) --build-arg GID=$(id -g) docker/
#   docker/run.sh                  # makes the container (the tree at /work, theos at /theos)
#   docker/build.sh                # builds the kernel inside it
#
# Beside this image the build needs theos (cctools' ld64 and the iPhoneOS 16.5 SDK) on the
# host, at the path docker/run.sh mounts: the upstream's docs/getting-started/building.md
# says how it is laid out; xnu-deps/setup.sh inside the container does the rest.
FROM ubuntu:24.04

ARG UID=1000
ARG GID=1000

ENV DEBIAN_FRONTEND=noninteractive

# the toolchain: the base set, then clang-20 (the kernels built with ubuntu's default clang-18
# ran slow and wedged mid-session) and the archive/payload extras (lzfse for the IPSWs, 7zz)
RUN apt-get update -q && apt-get install -y --no-install-recommends \
        build-essential git curl xz-utils make flex bison m4 perl \
        python3 python3-pip python3-venv autoconf automake libtool \
        pkg-config clang lld llvm nasm zip uuid-dev libncurses-dev ninja-build meson gawk \
        ca-certificates gnupg \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://apt.llvm.org/llvm-snapshot.gpg.key \
        | gpg --dearmor -o /usr/share/keyrings/llvm.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/llvm.gpg] https://apt.llvm.org/noble/ llvm-toolchain-noble-20 main" \
        > /etc/apt/sources.list.d/llvm20.list \
    && apt-get update -q && apt-get install -y --no-install-recommends clang-20 lld-20 llvm-20 \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m pip install --break-system-packages lzfse \
    && curl -fsSL https://github.com/ip7z/7zip/releases/download/26.05/7z2605-linux-x64.tar.xz \
        | tar -xJ -C /usr/local/bin --strip-components=1 7zz \
    && ln -sf /usr/local/bin/7zz /usr/local/bin/7z

# the builder: the same uid as the host's user, so what the container writes is ours
RUN (userdel -f ubuntu 2>/dev/null || true) \
    && (groupdel ubuntu 2>/dev/null || true) \
    && (getent group $GID >/dev/null || groupadd -g $GID builder) \
    && useradd -m -u $UID -g $GID -s /bin/bash builder

USER builder
WORKDIR /work
CMD ["sleep", "infinity"]
