from flask import Flask, render_template, request, jsonify
import os


# 对应创建Flask对象的方法，并实现配置项加载
def CreateFlask():
    # Flask类接收一个__name__参数
    flask_ = Flask(__name__,
                   root_path=os.path.join(os.getcwd(), 'app'),  # 将当前文件目录设置为根目录
                   static_url_path="/",  # 访问静态资源的url前缀, 默认值是static
                   static_folder="static",  # 静态文件的目录，默认值是static
                   template_folder='templates'  # 设置模板目录
                   )
    # 设置全局对象
    flask_.config.from_mapping(
        VALUE='123',
    )
    # 根据py文件加载配置项(与from_mapping函数中同名的参数将被覆盖)
    flask_.config.from_pyfile(os.path.join(os.getcwd(), 'app', 'config.py'))
    return flask_


# 创建Flask对象(工厂模式)
app = CreateFlask()


@app.route("/", methods=["GET", "POST"])
def index():
    return render_template('index.html'), 200

# 上传图片并存储到本地，目录为static/img，返回图片的url
@app.route("/upload_image", methods=["POST"])
def upload_image():
    image = request.files['image']
    # 使用os模块获取当前文件的目录
    current_dir = os.path.dirname(os.path.abspath(__file__))
    image_url = os.path.join(current_dir, 'static', 'img', image.filename)
    image.save(image_url)
    return jsonify(code=200, result=image_url)


# 自定义401错误页面
@app.errorhandler(401)
def page_not_found(error):
    return render_template('401.html'), 401


# 自定义404错误页面
@app.errorhandler(404)
def page_not_found(error):
    return render_template('404.html'), 404
