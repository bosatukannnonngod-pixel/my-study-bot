# 1. ベースイメージとしてPythonを使用
FROM python:3.11-slim

# 2. FFmpegと必要なシステムライブラリをインストール
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libffi-dev \
    python3-dev \
    build-essential \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 3. 作業ディレクトリの設定
WORKDIR /app

# 4. 依存ライブラリをコピーしてインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. すべてのファイルをコピー
COPY . .

# 6. ボットを実行
CMD ["python", "study_bot.py"]
