FROM ghcr.io/home-assistant/base:latest

ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL io.hass.version="${BUILD_VERSION}" \\
      io.hass.type="app" \\
      io.hass.arch="${BUILD_ARCH}"

RUN apk add --no-cache python3
COPY ins_ei /opt/ins-ei/ins_ei
COPY run.py /opt/ins-ei/run.py
WORKDIR /opt/ins-ei
CMD ["python3", "/opt/ins-ei/run.py"]
