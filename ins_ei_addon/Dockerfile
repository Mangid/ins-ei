ARG BUILD_FROM
FROM ${BUILD_FROM}

ARG BUILD_VERSION
ARG BUILD_ARCH
ENV INS_EI_ADDON_VERSION=${BUILD_VERSION}
LABEL io.hass.version="${BUILD_VERSION}"
LABEL io.hass.type="app"
LABEL io.hass.arch="${BUILD_ARCH}"

RUN apk add --no-cache python3
COPY ins_ei /opt/ins-ei/ins_ei
COPY run.py /opt/ins-ei/run.py
COPY web.py /opt/ins-ei/web.py
COPY web /opt/ins-ei/web
WORKDIR /opt/ins-ei
CMD ["sh", "-c", "python3 /opt/ins-ei/web.py & exec python3 /opt/ins-ei/run.py"]
