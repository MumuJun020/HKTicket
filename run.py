"""
入口：一条命令启动抢票控制台。

    python run.py

会自己挑一个空闲端口、自己把浏览器打开。不需要先设环境变量、
也不需要在项目根目录下执行。

可选环境变量：
    PORT=5055        指定端口（被占用时会自动往后找）
    NO_BROWSER=1     不自动打开浏览器
    KEEP_DATA=1      启动时不清空上一轮数据（调试用）
    KEEP_EVENT=1     只保留上次解析的活动，抢票人照常清空
"""
import os
import socket
import sys
import threading
import time
import webbrowser

from app import app
from app.Auto import ticket_store as store
from flask_cors import *

# 解决跨域问题
CORS(app, supports_credentials=True)

DEFAULT_PORT = 5000


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


def _port_free(port: int) -> bool:
    """这个端口现在能不能绑上。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # 不设 SO_REUSEADDR：这里要问的是"现在能不能真的用"，
        # 设了的话某些平台会对已被占用的端口也返回成功，等于白测
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def _pick_port(preferred: int, tries: int = 20) -> int:
    """
    从 preferred 开始往后找一个空闲端口。

    为什么要自动找：macOS 上 5000 被 ControlCenter（AirPlay）默认占用，
    每次都得手动设 PORT 才能起来；而且重启时上一个进程可能还没完全退出，
    端口短暂占用也会启动失败。让程序自己换一个，比让人去查谁占了端口省事得多。
    """
    for i in range(tries):
        p = preferred + i
        if _port_free(p):
            if i:
                print(f"[启动] 端口 {preferred} 被占用，改用 {p}")
            return p
    raise SystemExit(
        f"从 {preferred} 开始连续 {tries} 个端口都被占用了，"
        f"请用 PORT 环境变量指定一个空闲端口"
    )


def _open_browser_when_ready(port: int, timeout: float = 15.0):
    """
    等服务真的能连上了再打开浏览器。

    不能起个线程 sleep 几秒就开——机器慢的时候服务还没绑好端口，
    浏览器打开就是"无法连接"，用户会以为程序坏了。这里轮询到端口真的
    能连上为止，连不上就不开，让控制台里的地址兜底。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                webbrowser.open(f"http://localhost:{port}")
                return
        except OSError:
            time.sleep(0.3)


def main():
    _startup_cleanup()

    port = _pick_port(int(os.environ.get("PORT", DEFAULT_PORT)))

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

    # 自动开浏览器。debug 模式下 Werkzeug 会 fork 子进程重跑一遍本文件，
    # 不加 WERKZEUG_RUN_MAIN 判断的话每次热重载都会再弹一个标签页。
    if os.environ.get("NO_BROWSER") != "1" and not os.environ.get("WERKZEUG_RUN_MAIN"):
        threading.Thread(
            target=_open_browser_when_ready, args=(port,), daemon=True
        ).start()

    print()
    print("  抢票控制台已启动")
    print(f"  → http://localhost:{port}")
    print()
    print("  · 浏览器会自动打开，没打开就手动访问上面的地址")
    print("  · 关掉这个窗口程序就停了，抢票期间请保持它开着")
    print("  · 需要先打开「比特浏览器」客户端并登录")
    print()

    app.run(host="0.0.0.0", port=port, debug=debug)


if __name__ == '__main__':
    main()
