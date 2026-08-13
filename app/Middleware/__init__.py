from ..app import app
from .main import *

# class wsgi_app_reload(object):
#     def __init__(self, old_wsgi_app):
#         self.old_wsgi_app = old_wsgi_app
#
#     def __call__(self, environ, start_response):
#         # Middleware.before()
#         print('before')
#         # if 'other' in request.url:
#         #     return redirect(url_for("index"))
#
#         ret = self.old_wsgi_app(environ, start_response)
#         print('after')
#         # Middleware.after(environ)
#         return ret


# app.wsgi_app = wsgi_app_reload(app.wsgi_app)
