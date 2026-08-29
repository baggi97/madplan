FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8099

# Så Docker og Watchtower kan se om containeren kom sundt op igen efter en
# opdatering. Bruger stdlib, så vi slipper for at installere curl.
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/sundhedstjek' % os.getenv('WEB_PORT','8099'), timeout=4).status == 200 else 1)"

CMD ["python", "-m", "app.main"]
