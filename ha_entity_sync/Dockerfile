ARG BUILD_FROM
FROM ${BUILD_FROM}
WORKDIR /app
COPY app.py recovery.py index.html run.sh ./
RUN pip install --no-cache-dir websocket-client==1.8.0 \
    && chmod a+x /app/run.sh
CMD [ "/app/run.sh" ]
