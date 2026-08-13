from flask import Blueprint, jsonify, request

eg = Blueprint("eg", __name__)


@eg.route("/test", methods=["get"])
def test():
    try:
        return jsonify(code=200, result="hello")
    except Exception as e:
        print(e)
        return jsonify(code=500, result=str(e))
