import os
import sys

from app import app
from app.Auto import ticket_store as store
from flask_cors import *

# 解决跨域问题
CORS(app, supports_credentials=True)


def _startup_cleanup():
    """
    每次启动清空上一轮的运行时数据，保证这次是干净的。

    只在真正的首次启动执行一次：debug 模式下 Werkzeug 会 fork 一个子进程做热重载，
    子进程环境里有 WERKZEUG_RUN_MAIN=true。不加这个判断的话，
    改一行代码触发热重载就会把刚录进去的抢票人清掉。

    KEEP_DATA=1 可以跳过清理（调试时想留着上一轮数据用）。
    KEEP_EVENT=1 保留上次解析的活动（默认连活动一起清，保证每次打开
    票务解析框都是空的；重新解析只要一个请求，成本很低）。
    """
    if os.environ.get("WERKZEUG_RUN_MAIN"):
        return
    if os.environ.get("KEEP_DATA") == "1":
        print("[启动] KEEP_DATA=1，跳过清理，沿用上一轮数据")
        return

    cleared = store.reset_runtime_data(
        keep_event=os.environ.get("KEEP_EVENT") == "1"
    )
    print(
        f"[启动] 已清空上一轮数据："
        f"抢票人 {cleared['accounts']} 个、抢票配置 {cleared['plans']} 条、"
        f"窗口归属 {cleared['owners']} 条、活动信息 {cleared['event']}"
    )


if __name__ == '__main__':
    _startup_cleanup()
    # macOS 上 5000 端口默认被 ControlCenter(AirPlay) 占用，
    # 本地调试时用 PORT 环境变量换一个即可，默认值保持 5000 不影响 Docker 部署
    port = int(os.environ.get("PORT", 5000))

    # 打包成单文件后**必须关掉 debug**。
    #
    # debug 模式的热重载会 fork 一个子进程、把监听 socket 的 fd 传过去，
    # 而 PyInstaller 的启动引导跑不通这套：子进程起来就报
    # OSError: Bad file descriptor，然后不断重启——每次重启还会重跑一遍
    # 启动清理，把刚录进去的抢票人反复清空。
    #
    # 另外 Werkzeug 的调试器允许在页面上执行代码，而这个服务监听 0.0.0.0，
    # 交付出去的程序开着 debug 本身也不合适。
    debug = not getattr(sys, "frozen", False)
    if not debug:
        print(f"[启动] 控制台地址：http://localhost:{port}")
        print("[启动] 关掉这个窗口程序就停了，抢票期间请保持它开着")
    app.run(host="0.0.0.0", port=port, debug=debug)
