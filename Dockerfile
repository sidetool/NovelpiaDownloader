FROM debian:bookworm-slim AS build
RUN apt-get update && apt-get install -y --no-install-recommends mono-devel ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip \
    && rm -rf /var/lib/apt/lists/*
ARG ROSLYN_VERSION=4.12.0
RUN curl --fail --location --retry 3 \
    "https://api.nuget.org/v3-flatcontainer/microsoft.net.compilers.toolset/${ROSLYN_VERSION}/microsoft.net.compilers.toolset.${ROSLYN_VERSION}.nupkg" \
    -o /tmp/roslyn.zip \
    && unzip -q /tmp/roslyn.zip -d /opt/roslyn \
    && rm /tmp/roslyn.zip
WORKDIR /src
COPY . .
RUN sha256sum --check docker/upstream.sha256 \
    && xbuild NovelpiaDownloader.csproj /p:Configuration=Release \
        /p:CscToolPath=/opt/roslyn/tasks/net472 /p:CscToolExe=csc.exe /verbosity:minimal \
    && mcs docker/WebHost.cs docker/ImageTransport.cs docker/EpubOptimization.cs -r:bin/Release/NovelpiaDownloader.exe \
        -r:System.Windows.Forms -r:System.Web.Extensions -out:bin/Release/NovelpiaWebHost.exe

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    mono-runtime libmono-system-windows-forms4.0-cil \
    libmono-system-web-extensions4.0-cil libmono-microsoft-csharp4.0-cil \
    libmono-system-net-http4.0-cil libgdiplus ca-certificates-mono \
    fonts-noto-cjk xvfb x11-utils \
    nginx-light supervisor curl apache2-utils python3 python3-pil tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --create-home --shell /bin/sh novelpia
COPY --from=build /src/bin/Release/ /opt/novelpia/
COPY docker/ /opt/service/
COPY web/ /var/www/novelpia/
COPY --from=build /src/icon.ico /var/www/novelpia/favicon.ico
RUN chmod +x /opt/service/*.sh \
    && rm -f /etc/nginx/sites-enabled/default \
    && cp /opt/service/nginx.conf /etc/nginx/nginx.conf \
    && mkdir -p /data/state /data/downloads /run/novelpia
ENV DISPLAY=:99 LANG=C.UTF-8 TZ=Asia/Seoul
EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD /opt/service/healthcheck.sh
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/service/entrypoint.sh"]
