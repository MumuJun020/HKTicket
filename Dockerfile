# 设置基础镜像
FROM python:3.8.6
# 设置工作目录
WORKDIR /app
# 拷贝 requirements.txt 文件到镜像中
COPY ./requirements.txt /app
# 安装 Python 依赖
RUN pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple/