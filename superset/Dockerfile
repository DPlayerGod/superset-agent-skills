FROM apache/superset:6.1.0

USER root
RUN pip install --no-cache-dir psycopg2-binary --target=/app/.venv/lib/python3.10/site-packages
COPY superset/superset_config.py /app/pythonpath/superset_config.py
COPY superset/templates /app/pythonpath/templates
COPY superset/static /app/pythonpath/static
ENV SUPERSET_CONFIG_PATH=/app/pythonpath/superset_config.py
USER superset
