FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY api ./api
USER 10001
EXPOSE 8765
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8765"]
