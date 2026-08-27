from flask import Flask, render_template
import os
import sys


def _resource_dir() -> str:
    """
    静态资源（templates / static）所在目录。

    普通运行时就是本文件所在的 app/ 目录；PyInstaller 打包后，资源被解压到
    sys._MEIPASS 下的临时目录，要从那里取。

    原来写的是 os.getcwd()，有两个问题：换个目录启动就找不到模板；
    打包后压根没有 app/ 这个子目录。
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "app")
    return os.path.dirname(os.path.abspath(__file__))


# 对应创建Flask对象的方法，并实现配置项加载
def CreateFlask():
    # Flask类接收一个__name__参数
    flask_ = Flask(__name__,
                   root_path=_resource_dir(),  # 兼容 PyInstaller，见 _resource_dir
                   static_url_path="/",  # 访问静态资源的url前缀, 默认值是static
                   static_folder="static",  # 静态文件的目录，默认值是static
                   template_folder='templates'  # 设置模板目录
                   )
    return flask_


# 创建Flask对象(工厂模式)
app = CreateFlask()


@app.route("/", methods=["GET", "POST"])
def index():
    return render_template('index.html'), 200


# 自定义401错误页面
@app.errorhandler(401)
def unauthorized(error):
    return render_template('401.html'), 401


# 自定义404错误页面
@app.errorhandler(404)
def page_not_found(error):
    return render_template('404.html'), 404
