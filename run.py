from app import app
from flask_cors import *

# 解决跨域问题
CORS(app, supports_credentials=True)

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)
