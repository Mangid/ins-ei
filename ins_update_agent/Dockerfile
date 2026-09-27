ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache python3

COPY run.py /opt/ins-update-agent/run.py

WORKDIR /opt/ins-update-agent

CMD ["python3", "/opt/ins-update-agent/run.py"]
