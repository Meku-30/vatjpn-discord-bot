FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY vatsim_stat_notify_to_discord.py .
COPY settings.ini.example .

CMD ["python", "-u", "vatsim_stat_notify_to_discord.py"]
