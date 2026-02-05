FROM python:3.10-slim

# ここで音声を再生するためのツール（ffmpeg）を入れています
RUN apt-get update && apt-get install -y ffmpeg libffi-dev libnacl-dev python3-dev

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "your_code_name.py"] 
# ↑ your_code_name.py は自分のファイル名に変えてね
