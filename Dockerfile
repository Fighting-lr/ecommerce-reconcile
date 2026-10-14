# /* StarOfCAU-BEGIN: Production Dockerfile */
# 小微电商多渠道资金对账与年审异常核算系统 Docker 镜像
FROM python:3.12-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量，确保 Python 输出实时刷新且无字节码缓存
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 复制项目代码
COPY . /app

# 暴露报告服务默认端口
EXPOSE 8088

# 默认启动命令：生成压测流水、执行三方对账与试算平衡，并启动 Web 审计看板
CMD ["python", "run.py", "--count", "1000", "--serve", "--port", "8088"]
# /* StarOfCAU-END */
