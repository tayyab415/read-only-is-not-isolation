# Preparation only. Build in a quiet collection window on the existing labhost.
FROM python:3.12-slim AS builder
LABEL org.research.p7.build="nginx-1.30.6-squid-7.8-v1"
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential pkg-config libxml2-dev libltdl-dev libcap-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY sources/nginx-1.30.6.tar.gz sources/squid-7.8.tar.gz ./
RUN echo '3cf46f5ed7504d460125866b7bea99fb357f31d887e2e63b35503310e9b8b6c8  nginx-1.30.6.tar.gz' | sha256sum -c - \
    && echo 'b1367b2a0e5ecf1641880f74100c570b0668fe3e8fe08693e83b270401846dad  squid-7.8.tar.gz' | sha256sum -c - \
    && tar xzf nginx-1.30.6.tar.gz && tar xzf squid-7.8.tar.gz
WORKDIR /build/nginx-1.30.6
RUN ./configure --prefix=/opt/p7/nginx --without-http_rewrite_module --without-http_gzip_module \
    && make -j1 && make install
WORKDIR /build/squid-7.8
RUN ./configure --prefix=/opt/p7/squid --enable-http-violations \
    --with-default-user=research --disable-auth --disable-external-acl-helpers \
    --disable-url-rewrite-helpers --disable-storeid-rewrite-helpers \
    --without-openssl --without-gnutls --without-nettle \
    && make -j1 && make install
RUN /opt/p7/nginx/sbin/nginx -V 2> /opt/p7/nginx-build.txt \
    && /opt/p7/squid/sbin/squid -v > /opt/p7/squid-build.txt

FROM python:3.12-slim
LABEL org.research.p7.build="nginx-1.30.6-squid-7.8-v1"
RUN apt-get update && apt-get install -y --no-install-recommends libxml2 libltdl7 libcap2 libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 20001 research && useradd -u 20001 -g 20001 --no-create-home research
COPY --from=builder /opt/p7 /opt/p7
USER 20001:20001
WORKDIR /tmp
CMD ["python3", "-c", "import time; time.sleep(86400)"]
