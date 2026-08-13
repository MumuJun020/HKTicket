echo "容器启动中...."
docker rm -f my-container
docker run -d -p 5000:5000 \
--name my-container \
--network=my-network \
-v ./:/app flask-item:py-3.8.6 \
gunicorn run:app -c gunicorn.conf.py