FROM python:3.9-slim

# 设置工作目录
WORKDIR /app

# 复制依赖文件
COPY requirements.txt .

# 安装依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# 暴露端口
EXPOSE 3000

# 创建启动脚本
RUN echo '#!/bin/sh' > /app/start.sh && \
    echo 'exec flask run --host=0.0.0.0 --port=$PORT' >> /app/start.sh && \
    chmod +x /app/start.sh

# 使用 JSON 格式的 CMD 指令
CMD ["sh", "/app/start.sh"]
