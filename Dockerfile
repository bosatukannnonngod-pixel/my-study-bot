# 1. Pythonの軽量版を使用
FROM python:3.11-slim

# 2. FFmpegをインストールする命令（これが最重要！）
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# 3. フォルダの設定
WORKDIR /app

# 4. 必要なライブラリをインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. プログラム類をコピー
COPY . .

# 6. 実行（ファイル名が main.py ならそれに合わせてください）
CMD ["python", "main.py"]
