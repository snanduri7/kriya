# Linux reproduction of the hosted runner (ubuntu-24.04, uid 1001, own dockerd).
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip python3-dev build-essential git curl ca-certificates \
      openjdk-17-jdk-headless docker.io iptables sudo rsync procps \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://archive.apache.org/dist/maven/maven-3/3.9.9/binaries/apache-maven-3.9.9-bin.tar.gz \
      | tar -xz -C /opt && ln -s /opt/apache-maven-3.9.9/bin/mvn /usr/local/bin/mvn
RUN userdel -r ubuntu 2>/dev/null || true; useradd -m -u 1001 -G docker runner
COPY entry.sh /entry.sh
RUN chmod +x /entry.sh
ENTRYPOINT ["/entry.sh"]
