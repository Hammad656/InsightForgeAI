FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN python -m pip install --no-cache-dir --timeout 120 pip==26.2 \
    && pip install --no-cache-dir --timeout 120 -r requirements.txt \
    && useradd --create-home app \
    && install -d -o app -g app -m 0700 /var/lib/insightforge/reports
COPY . .
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
