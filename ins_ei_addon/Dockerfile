ARG BUILD_FROM
FROM ${BUILD_FROM}

ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL io.hass.version="${BUILD_VERSION}" \\
      io.hass.type="addon" \\
      io.hass.arch="${BUILD_ARCH}"

RUN apk add --no-cache python3

COPY ins_ei /opt/ins-ei/ins_ei
COPY ins_ei_addon/run.py /opt/ins-ei/run.py

WORKDIR /opt/ins-ei
CMD ["python3", "/opt/ins-ei/run.py"]
