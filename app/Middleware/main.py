from . import app
from flask import request, abort


# 通过扩展装饰器实现中间件的效果
# 扩展装饰器，在视图函数执行前，按照顺序依次执行(列表顺序)
@app.before_request
def middleware_post():
    # print('do something before_request', request.path)
    pass


# 扩展装饰器，在视图函数执行后，按照反序依次执行(列表逆序)
@app.after_request
def after_request(response):
    # print('do something after_request', request.path)
    return response
