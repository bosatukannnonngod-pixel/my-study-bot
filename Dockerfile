# 1. Python 3.11 をベースにする
FROM python:3.11-slim

# 2. ここで FFmpeg を強制インストールする (これが音を鳴らす鍵！)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# 3. サーバー内の作業ディレクトリ
WORKDIR /app

# 4. ライブラリをインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. すべてのファイル（main.py や start.mp3）をコピー
COPY . .

# 6. ボットを起動（ファイル名が main.py の場合）
CMD ["python", "main.py"]
