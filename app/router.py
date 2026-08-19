from .app import app
from .Controllers import ticket

# 注册蓝图（统一的路由注册）
app.register_blueprint(ticket, url_prefix="/ticket")
