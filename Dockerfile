FROM python:3.13-slim

WORKDIR /app
ENV PYTHONPATH=/app/src

COPY pyproject.toml /app/
RUN pip install --no-cache-dir uv && uv pip install --system .

COPY . /app

EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
