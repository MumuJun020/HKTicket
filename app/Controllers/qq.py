from flask import Blueprint, jsonify, request, Response, stream_with_context
from ..Auto.operation import run
from playwright.async_api import async_playwright
from ..utils.logger import LogCapture, log_manager, task_state_manager
import uuid
import threading
import asyncio
import json
import queue

qq = Blueprint("qq", __name__)


@qq.route("/do_public", methods=["post"])
def do_public():
    task_id = str(uuid.uuid4())
    try:
        browser_id = request.json.get("browser_id", "")
        if not browser_id:
            return jsonify(code=400, result="浏览器ID不能为空"), 400

        name = request.json.get("name", "")
        content = request.json.get("content", "")
        img = request.json.get("img", "")
        wait_time_min = float(request.json.get("wait_time_min", 3))  # 默认最小等待3秒
        wait_time_max = float(request.json.get("wait_time_max", 8))  # 默认最大等待8秒

        obj = {
            "name": name,
            "content": content,
            "img": img,
            "wait_time_min": wait_time_min,
            "wait_time_max": wait_time_max,
        }

        # 先创建日志任务和状态任务
        log_manager.create_task(task_id)
        task_state_manager.create_task(task_id)
        log_manager.add_log(task_id, f"开始发布帖子... (浏览器ID: {browser_id})", "info")

        # 在后台线程中异步执行任务，立即返回task_id
        def run_task():
            try:
                with LogCapture(task_id):

                    async def _run():
                        async with async_playwright() as playwright:
                            await run(playwright, "public", obj, task_id, browser_id)

                    # 在新的事件循环中运行
                    asyncio.run(_run())
            except Exception as e:
                log_manager.add_log(task_id, f"发布失败：{str(e)}", "error")

        # 启动后台线程
        thread = threading.Thread(target=run_task, daemon=True)
        thread.start()

        # 立即返回task_id，让前端开始轮询日志
        return jsonify(code=200, result="发布任务已启动", task_id=task_id)
    except Exception as e:
        log_manager.add_log(task_id, f"启动发布任务失败：{str(e)}", "error")
        return (
            jsonify(code=500, result="启动发布任务失败：" + str(e), task_id=task_id),
            500,
        )


@qq.route("/do_like", methods=["get", "post"])
def do_like():
    task_id = str(uuid.uuid4())
    try:
        browser_id = None
        if request.method == "POST":
            browser_id = request.json.get("browser_id", "")
            if not browser_id:
                return jsonify(code=400, result="浏览器ID不能为空"), 400

            # 支持新的格式：channels 列表
            channels = request.json.get("channels", [])
            if channels:
                # 新格式：多个频道配置
                obj = {
                    "channels": channels,
                }
            else:
                # 兼容旧格式：单个频道
                name = request.json.get("name", "")
                like_probability = float(request.json.get("like_probability", 0.8))
                time_limit = int(request.json.get("time_limit", 0))
                post_match = request.json.get("post_match", "")
                comment_count = int(request.json.get("comment_count", 0))
                like_count = int(request.json.get("like_count", 0))
                # 兼容旧参数名page_count，如果存在则使用，否则使用post_count
                post_count = int(
                    request.json.get("post_count", request.json.get("page_count", 50))
                )

                obj = {
                    "channels": [
                        {
                            "name": name,
                            "like_probability": like_probability,
                            "time_limit": time_limit,
                            "post_match": post_match,
                            "comment_count": comment_count,
                            "like_count": like_count,
                            "post_count": post_count,
                        }
                    ]
                }
        else:
            # GET 方法保持兼容旧格式
            browser_id = request.args.get("browser_id", "")
            if not browser_id:
                return jsonify(code=400, result="浏览器ID不能为空"), 400

            name = request.args.get("name", "")
            like_probability = float(request.args.get("like_probability", 0.8))
            time_limit = int(request.args.get("time_limit", 0))
            post_match = request.args.get("post_match", "")
            comment_count = int(request.args.get("comment_count", 0))
            like_count = int(request.args.get("like_count", 0))
            # 兼容旧参数名page_count，如果存在则使用，否则使用post_count
            post_count = int(
                request.args.get("post_count", request.args.get("page_count", 50))
            )

            obj = {
                "channels": [
                    {
                        "name": name,
                        "like_probability": like_probability,
                        "time_limit": time_limit,
                        "post_match": post_match,
                        "comment_count": comment_count,
                        "like_count": like_count,
                        "post_count": post_count,
                    }
                ]
            }

        # 先创建日志任务和状态任务
        log_manager.create_task(task_id)
        task_state_manager.create_task(task_id)
        log_manager.add_log(
            task_id, f"开始点赞任务，共 {len(obj['channels'])} 个频道... (浏览器ID: {browser_id})", "info"
        )

        # 在后台线程中异步执行任务，立即返回task_id
        def run_task():
            try:
                with LogCapture(task_id):

                    async def _run():
                        async with async_playwright() as playwright:
                            await run(playwright, "like", obj, task_id, browser_id)

                    # 在新的事件循环中运行
                    asyncio.run(_run())
            except Exception as e:
                log_manager.add_log(task_id, f"点赞任务失败：{str(e)}", "error")

        # 启动后台线程
        thread = threading.Thread(target=run_task, daemon=True)
        thread.start()

        # 立即返回task_id，让前端开始轮询日志
        return jsonify(code=200, result="", task_id=task_id)
    except Exception as e:
        log_manager.add_log(task_id, f"启动点赞任务失败：{str(e)}", "error")
        return (
            jsonify(code=500, result="启动点赞任务失败：" + str(e), task_id=task_id),
            500,
        )


@qq.route("/do_comment", methods=["post"])
def do_comment():
    task_id = str(uuid.uuid4())
    try:
        browser_id = request.json.get("browser_id", "")
        if not browser_id:
            return jsonify(code=400, result="浏览器ID不能为空"), 400

        channels = request.json.get("channels", [])
        post_match = request.json.get("post_match", "")
        comment_content = request.json.get("comment_content", "")

        if not comment_content or not comment_content.strip():
            return jsonify(code=400, result="评论内容不能为空"), 400

        if not channels:
            return jsonify(code=400, result="请至少选择一个频道"), 400

        # 构建频道配置列表
        channels_config = []
        for channel_name in channels:
            channels_config.append(
                {
                    "name": channel_name,
                    "post_match": post_match,
                    "comment_content": comment_content,
                    "comment_probability": float(
                        request.json.get("comment_probability", 1.0)
                    ),
                    "time_limit": int(request.json.get("time_limit", 0)),
                    "comment_count": int(request.json.get("comment_count", 0)),
                    "like_count": int(request.json.get("like_count", 0)),
                    "share_count": int(request.json.get("share_count", 0)),
                    "post_count": int(request.json.get("post_count", 50)),
                    "comment_interval_min": float(
                        request.json.get("comment_interval_min", 2)
                    ),
                    "comment_interval_max": float(
                        request.json.get("comment_interval_max", 5)
                    ),
                    "browse_mode": request.json.get("browse_mode", "new_post"),
                    "publisher_name": request.json.get("publisher_name", "小白兔"),
                }
            )

        obj = {
            "channels": channels_config,
        }

        # 先创建日志任务和状态任务
        log_manager.create_task(task_id)
        task_state_manager.create_task(task_id)
        log_manager.add_log(
            task_id, f"开始评论任务，共 {len(channels)} 个频道... (浏览器ID: {browser_id})", "info"
        )

        # 在后台线程中异步执行任务，立即返回task_id
        def run_task():
            try:
                with LogCapture(task_id):

                    async def _run():
                        async with async_playwright() as playwright:
                            await run(playwright, "comment", obj, task_id, browser_id)

                    # 在新的事件循环中运行
                    asyncio.run(_run())
            except Exception as e:
                log_manager.add_log(task_id, f"评论任务失败：{str(e)}", "error")

        # 启动后台线程
        thread = threading.Thread(target=run_task, daemon=True)
        thread.start()

        # 立即返回task_id，让前端开始轮询日志
        return jsonify(code=200, result="", task_id=task_id)
    except Exception as e:
        log_manager.add_log(task_id, f"启动评论任务失败：{str(e)}", "error")
        return (
            jsonify(code=500, result="启动评论任务失败：" + str(e), task_id=task_id),
            500,
        )


@qq.route("/get_logs", methods=["get"])
def get_logs():
    """获取指定任务的日志（兼容旧接口，保留用于向后兼容）"""
    task_id = request.args.get("task_id")
    since_index = int(request.args.get("since_index", 0))

    if not task_id:
        return jsonify(code=400, result="缺少task_id参数"), 400

    logs_data = log_manager.get_logs(task_id, since_index)
    return jsonify(code=200, **logs_data)


@qq.route("/logs_stream", methods=["get"])
def logs_stream():
    """SSE流式推送日志
    如果task_id为空，则推送所有任务的日志（全局模式）
    """
    task_id = request.args.get("task_id")  # 如果为空，则为全局模式

    def generate():
        """生成SSE事件流"""
        # 注册SSE客户端（task_id为None时注册为全局客户端）
        log_queue = log_manager.register_sse_client(task_id if task_id else None)

        try:
            # 发送初始连接成功消息
            if task_id:
                yield f"data: {json.dumps({'type': 'connected', 'message': f'SSE连接已建立（任务: {task_id}）'})}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'connected', 'message': 'SSE连接已建立（全局模式）'})}\n\n"

            # 如果指定了task_id，发送历史日志
            if task_id:
                history_logs = log_manager.get_logs(task_id, since_index=0)
                if history_logs.get("logs"):
                    recent_logs = history_logs["logs"][-50:]  # 只发送最近50条
                    for log_entry in recent_logs:
                        # 为历史日志添加task_id，以便前端过滤
                        log_entry_with_task = {**log_entry, "task_id": task_id}
                        yield f"data: {json.dumps({'type': 'log', 'data': log_entry_with_task})}\n\n"

            # 持续监听新日志
            import time as time_module

            last_activity_time = time_module.time()
            max_idle_time = 300  # 5分钟无日志后断开连接

            while True:
                try:
                    # 使用超时机制，每30秒发送一次心跳
                    log_entry = log_queue.get(timeout=30)

                    if log_entry is None:
                        # None是结束信号
                        yield f"data: {json.dumps({'type': 'end', 'message': '日志流已结束'})}\n\n"
                        break

                    # 发送日志
                    yield f"data: {json.dumps({'type': 'log', 'data': log_entry})}\n\n"
                    last_activity_time = time_module.time()  # 更新活动时间

                except queue.Empty:
                    # 超时，发送心跳
                    yield f"data: {json.dumps({'type': 'heartbeat', 'message': 'keepalive'})}\n\n"

                    # 检查是否超过最大空闲时间
                    if time_module.time() - last_activity_time > max_idle_time:
                        # 长时间无日志，断开连接
                        yield f"data: {json.dumps({'type': 'timeout', 'message': '连接超时，已断开'})}\n\n"
                        break

        except GeneratorExit:
            # 客户端断开连接
            pass
        except Exception as e:
            # 发生错误
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        finally:
            # 注销SSE客户端
            log_manager.unregister_sse_client(task_id if task_id else None, log_queue)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 禁用Nginx缓冲
            "Connection": "keep-alive",
        },
    )


@qq.route("/do_reply", methods=["post"])
def do_reply():
    task_id = str(uuid.uuid4())
    try:
        browser_id = request.json.get("browser_id", "")
        if not browser_id:
            return jsonify(code=400, result="浏览器ID不能为空"), 400

        channels = request.json.get("channels", [])
        post_match = request.json.get("post_match", "")
        comment_match = request.json.get("comment_match", "")
        reply_content = request.json.get("reply_content", "")

        if not reply_content or not reply_content.strip():
            return jsonify(code=400, result="回复内容不能为空"), 400

        if not channels:
            return jsonify(code=400, result="请至少选择一个频道"), 400

        obj = {
            "channels": channels,
            "post_match": post_match,
            "comment_match": comment_match,
            "reply_content": reply_content,
        }

        # 先创建日志任务和状态任务
        log_manager.create_task(task_id)
        task_state_manager.create_task(task_id)
        log_manager.add_log(
            task_id, f"开始回复任务，共 {len(channels)} 个频道... (浏览器ID: {browser_id})", "info"
        )

        # 在后台线程中异步执行任务，立即返回task_id
        def run_task():
            try:
                with LogCapture(task_id):

                    async def _run():
                        async with async_playwright() as playwright:
                            await run(playwright, "reply", obj, task_id, browser_id)

                    # 在新的事件循环中运行
                    asyncio.run(_run())
            except Exception as e:
                log_manager.add_log(task_id, f"回复任务失败：{str(e)}", "error")

        # 启动后台线程
        thread = threading.Thread(target=run_task, daemon=True)
        thread.start()

        # 立即返回task_id，让前端开始轮询日志
        return jsonify(code=200, result="", task_id=task_id)
    except Exception as e:
        log_manager.add_log(task_id, f"启动回复任务失败：{str(e)}", "error")
        return (
            jsonify(code=500, result="启动回复任务失败：" + str(e), task_id=task_id),
            500,
        )


@qq.route("/joined_channels", methods=["get", "post"])
async def joined_channels():
    try:
        browser_id = None
        if request.method == "POST":
            browser_id = request.json.get("browser_id", "")
        else:
            browser_id = request.args.get("browser_id", "")
        
        if not browser_id:
            return jsonify(code=400, result="浏览器ID不能为空"), 400

        async with async_playwright() as playwright:
            names = await run(playwright, "get_join_list", None, None, browser_id)
            return jsonify(code=200, result=names)
    except Exception as e:
        return jsonify(code=500, result=str(e)), 500


@qq.route("/user_name", methods=["get", "post"])
async def user_name():
    try:
        browser_id = None
        if request.method == "POST":
            browser_id = request.json.get("browser_id", "")
        else:
            browser_id = request.args.get("browser_id", "")
        
        if not browser_id:
            return jsonify(code=400, result="浏览器ID不能为空"), 400

        async with async_playwright() as playwright:
            user_name = await run(playwright, "get_user_name", None, None, browser_id)
            return jsonify(code=200, result=user_name)
    except Exception as e:
        return jsonify(code=500, result=str(e)), 500


@qq.route("/control_task", methods=["post"])
def control_task():
    """控制任务状态：暂停、恢复、停止"""
    try:
        task_id = request.json.get("task_id")
        action = request.json.get("action")  # pause, resume, stop

        if not task_id:
            return jsonify(code=400, result="缺少task_id参数"), 400

        if action == "pause":
            if task_state_manager.pause_task(task_id):
                log_manager.add_log(task_id, "任务已暂停", "warning")
                return jsonify(code=200, result="任务已暂停")
            else:
                return (
                    jsonify(code=400, result="无法暂停任务（任务可能不存在或未运行）"),
                    400,
                )
        elif action == "resume":
            if task_state_manager.resume_task(task_id):
                log_manager.add_log(task_id, "任务已恢复", "info")
                return jsonify(code=200, result="任务已恢复")
            else:
                return (
                    jsonify(code=400, result="无法恢复任务（任务可能不存在或未暂停）"),
                    400,
                )
        elif action == "stop":
            if task_state_manager.stop_task(task_id):
                log_manager.add_log(task_id, "任务已停止", "warning")
                return jsonify(code=200, result="任务已停止")
            else:
                return jsonify(code=400, result="无法停止任务（任务可能不存在）"), 400
        else:
            return (
                jsonify(code=400, result="无效的action参数，支持：pause, resume, stop"),
                400,
            )
    except Exception as e:
        return jsonify(code=500, result=str(e)), 500
