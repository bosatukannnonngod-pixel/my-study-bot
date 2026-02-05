# Pythonのイメージを使用
FROM python:3.10-slim

# FFmpegをインストール
RUN apt-get update && apt-get install -y ffmpeg libffi-dev libnacl-dev python3-dev

# 作業ディレクトリを作成
WORKDIR /app

# 依存ライブラリをコピーしてインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 全てのファイルをコピー
COPY . .

# ボットを実行
CMD ["python", "study_bot.py"]
