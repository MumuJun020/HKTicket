from .app import app
from .Controllers import eg, ticket

# 注册蓝图（统一的路由注册）
app.register_blueprint(eg, url_prefix="/eg")
app.register_blueprint(ticket, url_prefix="/ticket")
