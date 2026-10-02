# Chạy 24/7 trên VPS/Render/Fly.io/Oracle Cloud Free: docker build -t mn-events . && docker run -d --restart=always --env-file .env -v $(pwd)/data:/app/data mn-events
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV TZ=Asia/Ho_Chi_Minh
CMD ["python", "main.py", "serve"]
