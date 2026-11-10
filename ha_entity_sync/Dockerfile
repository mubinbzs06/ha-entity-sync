ARG BUILD_FROM
FROM ${BUILD_FROM}
WORKDIR /app
COPY app.py index.html run.sh ./
RUN chmod a+x /app/run.sh
CMD [ "/app/run.sh" ]
