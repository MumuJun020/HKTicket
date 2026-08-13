# coding:utf-8

import multiprocessing

workers = multiprocessing.cpu_count() * 2 + 1  # 指定 Gunicorn 启动的 worker 进程数量，一般建议设置为 CPU 核心数的 2-4 倍，此处根据系统cpu核心数计算，默认值：1
threads = 3  # 配置 Gunicorn 的工作线程数， 默认值：1
worker_class = "gevent"  # 工作模式协程，采用gevent库，支持异步处理请求，提高吞吐量，默认的是sync模式，
bind = "0.0.0.0:5000"  # 指定 Gunicorn 监听的地址和端口，默认值：8000
proc_name = 'flaskItem'  # 设置启动的进程名
backlog = 512  # 指定等待连接的最大数量，当连接超过这个数量时，请求将被拒绝，默认值：512
timeout = 30  # 设置超时时间120s，按自己的需求进行设置，默认值：30
keepalive = 2  # 指定 keep-alive 连接的时间。一般设定在1~5秒之间。默认值：2
max_requests = 0  # 指定每个 worker 进程在处理多少个请求后自动重启，0 表示关闭自动重启功能，默认值：0
max_requests_jitter = 0  # 指定每个 worker 进程自动重启时随机等待的最大秒数，避免同时重启所有 worker 进程，默认值：0
graceful_timeout = 30  # 指定 worker 进程在重启前等待处理完请求的最长时间，默认值：30
worker_connections = 1000  # 指定每个 worker 进程的最大并发连接数，默认值：1000
pidfile = './log/gunicorn.pid'  # 设置进程文件目录
accesslog = './log/gunicorn_access.log'  # 设置访问日志
errorlog = './log/gunicorn_error.log'  # 错误信息日志路径
loglevel = 'info'  # 指定日志的详细程度，支持 debug、info、warning、error、critical 等多个级别。
access_log_format = '%(t)s %(p)s %(h)s "%(r)s" %(s)s %(L)s %(b)s %(f)s" "%(a)s"'  # 设置gunicorn访问日志格式，错误日志无法设置
capture_output = False  # 指定是否将日志输出到标准输出，设为 True 后，accesslog 和 errorlog 配置将失效，默认值：False

# HTTP请求行的最大大小，此参数用于限制HTTP请求行的允许大小，默认情况下，这个值为4094。
# 值是0~8190的数字。此参数可以防止任何DDOS攻击
limit_request_line = 4096

# 限制HTTP请求中请求头字段的数量。
# 此字段用于限制请求头字段的数量以防止DDOS攻击，与limit-request-field-size一起使用可以提高安全性。
# 默认情况下，这个值为100，这个值不能超过32768
limit_request_fields = 100

# 限制HTTP请求中请求头的大小，默认情况下这个值为8190。值是一个整数或者0，当该值为0时，表示将对请求头大小不做限制
limit_request_field_size = 8190

# daemon = False  # 是否以守护进程方式运行，默认为False
# raw_env = ''  # 自定义环境变量的列表，默认为空
