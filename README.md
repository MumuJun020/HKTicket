# 开发环境 & 部署环境
+ 开发
    + Python3.8.6
    + Pip20.2.4
    + MySQL8.0
+ 部署
    + Centos7.9
    + Docker23.0.3
    + Python3.8.6
    + Pip20.2.4
    + MySQL8.0
# 项目结构
```
    + app   # 项目主要编辑目录
        + Controllers   # 接口编辑
        + Middleware    # 中间件
        + Models    # 第三方模块，如连接MySQL
        + static    # 静态资源存放路径
        + templates     # 模板存放路径
        app.py  # flask对象实例化位置
        config.py   # 配置文件编辑位置
        router.py   # 蓝图注册（路由注册）
    + log   # gunicorn日志存放位置
        gunicorn.pid
        gunicorn_access.log
        gunicorn_error.log
    + sql   # 测试数据库
        by.sql
    docker_run.sh   # 容器启动文件
    Dockerfile  # 构建项目启动镜像
    gunicorn.conf.py    # gunicorn配置文件
    requierments.txt    # 依赖包记录
    run.py  # 启动路径
    
```
# 项目部署

## （1）直接使用gunicorn部署
### 一、安装python

### 二、将项目上传到服务器

### 三、在项目目录创建虚拟环境
> 为了给项目创造一个独立的允许环境，将项目所需的python环境和依赖包单独存放
#### （1）安装virtualenv
    pip3 install virtualenv
####（2）创建虚拟环境
    virtualenv -p python3 venv
#### （3）激活虚拟环境
    source venv/bin/activate
#### （4）在虚拟环境下安装依赖
    pip3 install -r requirements.txt
#### （5）启动
    gunicorn run:app -c gunicorn.conf.py
#### （6）后台启动
    先执行：nohup gunicorn run:app -c gunicorn.conf.py &
    退出后继续执行：nohup gunicorn run:app -c gunicorn.conf.py
#### （7）退出虚拟环境
    deactivate

## （2）使用docker部署
### 一、将项目上传到服务器中，如：/root/flask-item
### 二、安装docker
    1. 更新依赖包
    sudo yum update
    2. 安装Docker需要的依赖包
    sudo yum install -y yum-utils device-mapper-persistent-data lvm2
    3. 添加Docker仓库
    sudo yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
    4. 安装最新版本的Docker
    sudo yum install docker-ce
    5. 启动Docker服务
    sudo systemctl start docker
    6. 设置Docker服务开机自启动
    sudo systemctl enable docker
    7. 验证Docker是否安装成功（Docker已经成功安装，则会输出Docker的版本信息）
    docker --version
### 三、使用docker拉取python3.8.6 和 MySQL8.0.27镜像
    docker pull python:3.8.6
    docker pull mysql:8.0.27
### 四、docker中创建一个自定义网络
    docker network create my-network
### 五、启动MySQL容器
> 项目目录下进入sql文件夹中执行sh mysql_run.sh即可，内容如下，根据实际情况修改：

    docker run -d --name mysql \
    -p 3306:3306 \
    -e MYSQL_ROOT_PASSWORD=zxxyp \
    -v /root/mysql/data:/var/lib/mysql \
    --network=my-network \
    --bind-address=0.0.0.0 \
    mysql:8.0.27 \
    --default-authentication-plugin=mysql_native_password \
    --character-set-server=utf8mb4 \
    --collation-server=utf8mb4_unicode_ci \
    --max_allowed_packet=128M \
    --innodb_buffer_pool_size=2G \
    --innodb_log_file_size=512M \
    --innodb_flush_log_at_trx_commit=2 \
    --skip-name-resolve \
    --skip-character-set-client-handshake \
    --explicit_defaults_for_timestamp
* -d: 指定容器在后台运行。
* --name: 指定容器的名称。
* -p: 将容器的端口映射到主机的端口。
* -e: 设置环境变量，这里设置了MySQL的root账号密码。
* -v: 将宿主机上的目录挂载到容器中，这里将MySQL的数据目录挂载到了宿主机上的指定目录中。
* --network: 指定容器所属的网络。
* mysql:8.0.27: 指定要启动的MySQL镜像名称及版本号。
* --default-authentication-plugin=mysql_native_password: 指定默认的密码验证插件。
* --bind-address=0.0.0.0 表示允许远程连接。
* --character-set-server=utf8mb4: 设置MySQL服务器的字符集为utf8mb4。
* --collation-server=utf8mb4_unicode_ci: 设置MySQL服务器的排序规则为utf8mb4_unicode_ci。
* --max_allowed_packet=128M: 设置最大允许的数据包大小为128MB。
* --innodb_buffer_pool_size=2G: 设置InnoDB缓存池大小为2GB。
* --innodb_log_file_size=512M: 设置InnoDB日志文件大小为512MB。
* --innodb_flush_log_at_trx_commit=2: 设置InnoDB事务提交时日志写入的策略，这里设置为2表示事务提交时异步写入日志。
* --skip-name-resolve: 禁用DNS反解析功能。
* --skip-character-set-client-handshake: 禁用客户端字符集校验。
* --explicit_defaults_for_timestamp: 启用严格的时间戳模式。

### 基于python3.8.6基础镜像构建项目所需镜像
> 依赖python3.8.6这个基础镜像构建一个扩展镜像，名为：flask-item:py-3.8.6
> 新镜像不仅具备python环境，同时还安装了项目运行所需依赖包，这样每次更新项目只需要重新启动新容器即可

    项目目录下执行（依据项目中Dockerfile）：
        docker build -t flask-item:3.8.6 . 
### 基于flask-item:3.8.6镜像启动项目容器
    项目目录下执行:
        sh docker_run.sh
    
    docker_run.sh中内容如下：
        echo "容器启动中...."
        docker rm -f my-container
        docker run -d -p 5000:5000 \
        --name my-container \
        --network=my-network \
        -v ./:/app flask-item:py-3.8.6 \
        gunicorn run:app -c gunicorn.conf.py
### 注意
使用docket部署时需要修改config.py文件中主机地址，内容如下：
* MYSQL_HOST = '127.0.0.1'  # 主机名
* MYSQL_HOST = 'mysql'  # 主机名,docker部署使用,设置为docker中同一网络环境下的MySQL容器名称
