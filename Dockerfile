FROM python:slim-bookworm

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

COPY amznDocker.py .

# Comando per eseguire il bot
CMD ["python", "amznDocker.py"]