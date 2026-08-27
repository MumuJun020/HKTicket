"""
把项目打包成单文件可执行程序。

    python build_exe.py

**必须在目标系统上打包。** PyInstaller 不能交叉编译：
要 Windows 的 .exe 就得在 Windows 上跑这个脚本，在 macOS 上跑只会得到 macOS 可执行文件。

打包前先装：
    pip install -r requirements.txt
    pip install pyinstaller

产物在 dist/ 下。**data/ 目录会生成在可执行文件旁边**，
里面是账号密码等明文数据，分发时不要一起打包出去。
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
NAME = "HKTicket"


def _playwright_driver() -> str:
    """
    找 Playwright 的 driver 目录。

    Playwright 的 node driver 是运行时才去包目录里找的，PyInstaller 的静态分析
    发现不了，不显式打进去的话打出来的程序一连浏览器就报找不到 driver。
    """
    import playwright

    d = os.path.join(os.path.dirname(playwright.__file__), "driver")
    if not os.path.isdir(d):
        raise SystemExit(
            f"找不到 Playwright driver（{d}）。先执行 pip install playwright"
        )
    return d


def main():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        raise SystemExit("没装 PyInstaller，先执行：pip install pyinstaller")

    sep = ";" if os.name == "nt" else ":"   # --add-data 的分隔符按平台不同
    driver = _playwright_driver()

    # 资源目录可能不存在（比如 app/static 清空后就没了），
    # 传给 PyInstaller 会直接报错中断，所以先过滤一遍
    data_args = []
    for src, dst in (
        (os.path.join(ROOT, "app", "templates"), "app/templates"),
        (os.path.join(ROOT, "app", "static"), "app/static"),
        (driver, "playwright/driver"),
    ):
        if os.path.isdir(src):
            data_args += ["--add-data", f"{src}{sep}{dst}"]
        else:
            print(f"[跳过] 资源目录不存在：{src}")

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name", NAME,
        # 控制台窗口要留着：日志、错误、启动地址都打在那里，
        # 用 --noconsole 的话程序起没起来、报什么错全看不见
        "--console",

        # 这几个是运行时才 import 的，静态分析扫不到
        "--hidden-import", "playwright.async_api",
        "--hidden-import", "openpyxl",
        "--hidden-import", "engineio.async_drivers.threading",

        # 打包体积：这些都用不到，排除掉能小一大截
        "--exclude-module", "tkinter",
        "--exclude-module", "matplotlib",
        "--exclude-module", "numpy",
        "--exclude-module", "pandas",
        "--exclude-module", "PIL",
        "--exclude-module", "pytest",
    ] + data_args + [
        os.path.join(ROOT, "run.py"),
    ]

    print("开始打包，第一次会比较久（几分钟）...\n")
    print(" ".join(args) + "\n")
    r = subprocess.run(args, cwd=ROOT)
    if r.returncode != 0:
        raise SystemExit(f"打包失败，退出码 {r.returncode}")

    exe = os.path.join(ROOT, "dist", NAME + (".exe" if os.name == "nt" else ""))
    if not os.path.exists(exe):
        raise SystemExit(f"打包结束但没找到产物：{exe}")

    size = os.path.getsize(exe) / 1024 / 1024
    print(f"\n完成：{exe}  ({size:.0f} MB)")

    # 顺手把使用说明放到产物旁边，交付时一起给
    readme = os.path.join(ROOT, "dist", "使用说明.txt")
    with open(readme, "w", encoding="utf-8") as f:
        f.write(
            f"""{NAME} 使用说明
{'=' * 40}

1. 先装好「比特浏览器」并登录，保持它一直开着。
   本程序通过 127.0.0.1:54345 和它通信，它没开就什么都做不了。

2. 在比特浏览器里按抢票人数创建窗口，建议每个窗口配独立代理 IP。

3. 双击 {os.path.basename(exe)} 启动，会弹出一个黑色控制台窗口
   （不要关掉它，关了程序就停了）。

4. 浏览器打开 http://localhost:5000
   端口被占用的话，在控制台里改用：set PORT=5055 再启动。

5. 按界面上的 ①②③④ 顺序操作即可。

注意
----
· 程序旁边会生成 data 目录，里面有**明文密码**，不要连它一起发给别人。
· 程序只做到「锁单」，**不会自动付款**，抢到后要自己去付。
· 锁单后一般只有五分钟付款时间，注意界面顶部的「待处理」提醒。
· 验证码需要人工在弹出的浏览器窗口里完成。
"""
        )
    print(f"使用说明：{readme}")


if __name__ == "__main__":
    main()
