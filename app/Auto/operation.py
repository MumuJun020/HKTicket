from .bit_api import *
import time
import random
from datetime import timedelta
import re
from typing import Union
import asyncio
from playwright.async_api import async_playwright, Playwright, Page, expect
from ..utils.logger import task_state_manager


# /browser/open 接口会返回 selenium使用的http地址，以及webdriver的path，直接使用即可
# 浏览器ID现在由前端传递，不再硬编码
# browser_id = "906f962508ba43deb1896bf855ddb911"  # 找人
# browser_id = "77eae086c94e44b687e54dd1234e7282"  # 论文
# browser_id = "f04225881a0b4a1786c2be29122b777f"  # 主播


async def run(playwright: Playwright, fun="", obj=None, task_id=None, browser_id=None):
    """
    :param playwright: Playwright对象
    :param fun: 功能名称
    :param obj: 参数对象
    :param task_id: 任务ID
    :param browser_id: 浏览器ID（必需）
    """
    if not browser_id:
        raise ValueError("浏览器ID不能为空，请在前端界面设置浏览器ID")

    res = openBrowser(browser_id)
    ws = res["data"]["ws"]

    chromium = playwright.chromium
    browser = await chromium.connect_over_cdp(ws)

    # 找到标题为“腾讯频道”的页面
    page = None
    for context in browser.contexts:
        for page in context.pages:
            if "腾讯频道" in await page.title():
                print("QQ频道已正常打开")
                page = page
                break
            else:
                print("暂时未找到腾讯频道，正在检测...")
                continue

    # 确保切换到该页面并将其显示到前台
    await page.bring_to_front()
    # time.sleep(2)

    if fun == "public":
        names = [
            name.strip()
            for name in obj["name"].replace(",", "，").replace("\n", "，").split("，")
            if name.strip()
        ]
        wait_time_min = obj.get("wait_time_min", 3)  # 默认最小等待3秒
        wait_time_max = obj.get("wait_time_max", 8)  # 默认最大等待8秒

        # 确保最小值不大于最大值
        if wait_time_min > wait_time_max:
            wait_time_min, wait_time_max = wait_time_max, wait_time_min

        for index, name in enumerate(names):
            # 检查任务状态
            if task_id:
                state = task_state_manager.get_state(task_id)
                if state == "stopped":
                    print("任务已停止")
                    return
                elif state == "paused":
                    print("任务已暂停，等待恢复...")
                    # 等待恢复（异步等待）
                    while True:
                        current_state = task_state_manager.get_state(task_id)
                        if current_state == "running":
                            print("任务已恢复，继续发布...")
                            break
                        elif current_state == "stopped":
                            print("任务已停止")
                            return
                        await asyncio.sleep(0.5)

            await public(page, name, obj["content"], obj["img"])

            # 如果不是最后一个频道，则随机等待指定时间范围内的时间
            if index < len(names) - 1:
                wait_time = random.uniform(wait_time_min, wait_time_max)
                for i in range(int(wait_time) + 1):
                    # 在等待过程中检查任务状态
                    if task_id:
                        state = task_state_manager.get_state(task_id)
                        if state == "stopped":
                            print("任务已停止")
                            return
                        elif state == "paused":
                            print("任务已暂停，等待恢复...")
                            # 异步等待恢复
                            while True:
                                current_state = task_state_manager.get_state(task_id)
                                if current_state == "running":
                                    print("任务已恢复，继续等待...")
                                    break
                                elif current_state == "stopped":
                                    print("任务已停止")
                                    return
                                await asyncio.sleep(0.5)

                    remaining = wait_time - i
                    if remaining > 0:
                        print(f"[COUNTDOWN]等待 {remaining:.1f} 秒后发布下一个频道...")
                        await asyncio.sleep(min(1.0, remaining))
    elif fun == "like":
        # 支持多个频道依次执行点赞任务
        channels = obj.get("channels", [])
        if not channels:
            print("未找到频道列表")
            return

        for index, channel_config in enumerate(channels):
            # 检查任务状态
            if task_id:
                state = task_state_manager.get_state(task_id)
                if state == "stopped":
                    print("任务已停止")
                    return
                elif state == "paused":
                    print("任务已暂停，等待恢复...")
                    # 等待恢复（异步等待）
                    while True:
                        current_state = task_state_manager.get_state(task_id)
                        if current_state == "running":
                            print("任务已恢复，继续点赞...")
                            break
                        elif current_state == "stopped":
                            print("任务已停止")
                            return
                        await asyncio.sleep(0.5)

            channel_name = channel_config.get("name", "")
            like_probability = float(channel_config.get("like_probability", 0.8))
            time_limit = int(channel_config.get("time_limit", 0))
            post_match = channel_config.get("post_match", "") or channel_config.get(
                "keywords", ""
            )  # 兼容旧字段名
            comment_count = int(channel_config.get("comment_count", 0))
            like_count = int(channel_config.get("like_count", 0))
            # 兼容旧参数名page_count，如果存在则使用，否则使用post_count
            post_count = int(
                channel_config.get("post_count", channel_config.get("page_count", 50))
            )
            like_interval_min = float(channel_config.get("like_interval_min", 10))
            like_interval_max = float(channel_config.get("like_interval_max", 30))
            browse_mode = channel_config.get("browse_mode", "new_post")  # 默认新发表

            # 确保最小值不大于最大值
            if like_interval_min > like_interval_max:
                like_interval_min, like_interval_max = (
                    like_interval_max,
                    like_interval_min,
                )

            # 浏览方式映射
            browse_mode_map = {
                "new_post": "新发表",
                "new_reply": "新回复",
                "hot": "热门",
            }
            browse_mode_text = browse_mode_map.get(browse_mode, "新发表")

            print(f"开始处理频道 [{index + 1}/{len(channels)}]: {channel_name}")
            print(f"  点赞概率: {like_probability}")
            print(
                f"  时间限制: {time_limit}小时 (0=不限制)"
                if time_limit > 0
                else "  时间限制: 不限制"
            )
            print(f"  关键词匹配: {post_match if post_match else '无'}")
            print(
                f"  评论数要求: {comment_count} (0=无要求)"
                if comment_count > 0
                else "  评论数要求: 无要求"
            )
            print(
                f"  点赞数要求: {like_count} (0=无要求)"
                if like_count > 0
                else "  点赞数要求: 无要求"
            )
            print(f"  浏览帖子数量: {post_count} (0=不限制)")
            print(f"  点赞间隔: {like_interval_min}~{like_interval_max}秒")
            print(f"  浏览方式: {browse_mode_text}")

            await like(
                page,
                channel_name,
                like_probability,
                time_limit,
                post_match,
                comment_count,
                like_count,
                0,  # min_share 暂时保留为0
                post_count,
                like_interval_min,
                like_interval_max,
                browse_mode,
                task_id,  # 传递task_id参数
            )

            # 如果不是最后一个频道，等待一小段时间
            if index < len(channels) - 1:
                wait_time = random.uniform(2, 5)
                for i in range(int(wait_time) + 1):
                    # 在等待过程中检查任务状态
                    if task_id:
                        state = task_state_manager.get_state(task_id)
                        if state == "stopped":
                            print("任务已停止")
                            return
                        elif state == "paused":
                            print("任务已暂停，等待恢复...")
                            # 异步等待恢复
                            while True:
                                current_state = task_state_manager.get_state(task_id)
                                if current_state == "running":
                                    print("任务已恢复，继续等待...")
                                    break
                                elif current_state == "stopped":
                                    print("任务已停止")
                                    return
                                await asyncio.sleep(0.5)

                    remaining = wait_time - i
                    if remaining > 0:
                        print(f"[COUNTDOWN]等待 {remaining:.1f} 秒后处理下一个频道...")
                        await asyncio.sleep(min(1.0, remaining))
                    else:
                        break
                print(f"等待完成，继续处理下一个频道...")

        print("所有频道的点赞任务已完成")
        # 任务完成后自动停止任务状态
        if task_id:
            task_state_manager.stop_task(task_id)
    elif fun == "comment":
        # 支持多个频道依次执行评论任务
        channels = obj.get("channels", [])
        if not channels:
            print("未找到频道列表")
            return

        for index, channel_config in enumerate(channels):
            # 检查任务状态
            if task_id:
                state = task_state_manager.get_state(task_id)
                if state == "stopped":
                    print("任务已停止")
                    return
                elif state == "paused":
                    print("任务已暂停，等待恢复...")
                    # 等待恢复（异步等待）
                    while True:
                        current_state = task_state_manager.get_state(task_id)
                        if current_state == "running":
                            print("任务已恢复，继续评论...")
                            break
                        elif current_state == "stopped":
                            print("任务已停止")
                            return
                        await asyncio.sleep(0.5)

            channel_name = channel_config.get("name", "")
            post_match = channel_config.get("post_match", "") or channel_config.get(
                "keywords", ""
            )  # 兼容旧字段名
            comment_content = channel_config.get("comment_content", "")

            # 使用默认值或从配置中获取
            comment_probability = float(channel_config.get("comment_probability", 1.0))
            time_limit = int(channel_config.get("time_limit", 0))
            comment_count = int(channel_config.get("comment_count", 0))
            like_count = int(channel_config.get("like_count", 0))
            share_count = int(channel_config.get("share_count", 0))
            post_count = int(
                channel_config.get("post_count", channel_config.get("page_count", 50))
            )
            comment_interval_min = float(channel_config.get("comment_interval_min", 10))
            comment_interval_max = float(channel_config.get("comment_interval_max", 30))
            browse_mode = channel_config.get("browse_mode", "new_post")  # 默认新发表
            publisher_name = channel_config.get("publisher_name", "")  # 默认发布者名称

            # 确保最小值不大于最大值
            if comment_interval_min > comment_interval_max:
                comment_interval_min, comment_interval_max = (
                    comment_interval_max,
                    comment_interval_min,
                )

            # 浏览方式映射
            browse_mode_map = {
                "new_post": "新发表",
                "new_reply": "新回复",
                "hot": "热门",
            }
            browse_mode_text = browse_mode_map.get(browse_mode, "新发表")

            print(f"开始处理频道 [{index + 1}/{len(channels)}]: {channel_name}")
            print(f"  评论概率: {comment_probability}")
            print(
                f"  时间限制: {time_limit}小时 (0=不限制)"
                if time_limit > 0
                else "  时间限制: 不限制"
            )
            print(f"  关键词匹配: {post_match if post_match else '无'}")
            print(f"  评论内容: {comment_content[:30]}...")
            print(
                f"  评论数要求: {comment_count} (0=无要求)"
                if comment_count > 0
                else "  评论数要求: 无要求"
            )
            print(
                f"  点赞数要求: {like_count} (0=无要求)"
                if like_count > 0
                else "  点赞数要求: 无要求"
            )
            print(
                f"  分享数要求: {share_count} (0=无要求)"
                if share_count > 0
                else "  分享数要求: 无要求"
            )
            print(f"  浏览帖子数量: {post_count} (0=不限制)")
            print(f"  评论间隔: {comment_interval_min}~{comment_interval_max}秒")
            print(f"  浏览方式: {browse_mode_text}")

            await comment(
                page,
                channel_name,
                comment_probability,
                time_limit,
                post_match,
                comment_content,
                comment_count,
                like_count,
                share_count,  # 传递share_count参数
                post_count,
                comment_interval_min,
                comment_interval_max,
                browse_mode,
                task_id,  # 传递task_id参数
                publisher_name,  # 传递发布者名称
            )

            # 如果不是最后一个频道，等待一小段时间
            if index < len(channels) - 1:
                wait_time = random.uniform(2, 5)
                for i in range(int(wait_time) + 1):
                    # 在等待过程中检查任务状态
                    if task_id:
                        state = task_state_manager.get_state(task_id)
                        if state == "stopped":
                            print("任务已停止")
                            return
                        elif state == "paused":
                            print("任务已暂停，等待恢复...")
                            # 异步等待恢复
                            while True:
                                current_state = task_state_manager.get_state(task_id)
                                if current_state == "running":
                                    print("任务已恢复，继续等待...")
                                    break
                                elif current_state == "stopped":
                                    print("任务已停止")
                                    return
                                await asyncio.sleep(0.5)

                    remaining = wait_time - i
                    if remaining > 0:
                        print(f"[COUNTDOWN]等待 {remaining:.1f} 秒后处理下一个频道...")
                        await asyncio.sleep(min(1.0, remaining))
                    else:
                        break
                print(f"等待完成，继续处理下一个频道...")

        print("所有频道的评论任务已完成")
        # 任务完成后自动停止任务状态
        if task_id:
            task_state_manager.stop_task(task_id)
    elif fun == "get_join_list":
        names = await get_join_list(page)
        return names
    elif fun == "get_user_name":
        user_name = await get_user_name(page)
        return user_name
    else:
        print("未找到功能")
        return

    # print('new page and goto baidu')
    #
    # page = await default_context.new_page()
    # await page.goto('https://baidu.com')

    # time.sleep(2)
    #
    # print('clsoe page and browser')
    # await page.close()

    # time.sleep(2)
    # closeBrowser(browser_id)


# 获取加入的频道列表
async def get_join_list(page: Page):
    print("正在获取加入的频道列表...", end="\n\n")
    await page.wait_for_selector(".my-guild-list")
    # 在class属性为my-guild-list的div标签中找到全部有class有my-guild-item这个属性的div标签，获取里面的title属性值
    guild_list = await page.locator(".my-guild-list .my-guild-item").all()
    names = []
    for guild in guild_list:
        guild_name = await guild.get_attribute("title")
        if guild_name:
            names.append(guild_name)
    print("获取到的频道列表数量为：", len(names), end="\n\n")
    return names
 

# 获取登入用户的昵称
async def get_user_name(page: Page):
    print("正在获取登入用户的昵称...", end="\n\n")
    try:
        await page.wait_for_selector(".user-card", timeout=5000)
        user_name = await page.locator(".user-card .user-info").text_content()
        # 清理用户名：去除首尾空白字符和换行符
        if user_name:
            user_name = user_name.strip()
            print(f"获取到的用户昵称：{user_name}")
            return user_name
        else:
            print("未能获取到用户昵称（返回内容为空）")
            return ""
    except Exception as e:
        print(f"获取用户昵称失败: {str(e)}")
        raise e


# 发布
async def public(page: Page, name="", content="", img=""):
    """
    发布帖子
    :param page:
    :param name: 频道名称
    :param content: 发帖内容
    :param img: 图片
    :return:
    """
    # 1️⃣ 点击“xxx频道”
    try:
        print(name, content, img)
        await page.wait_for_selector(f'div.my-guild-item[title="{name}"]', timeout=2000)
        await page.locator(f'div.my-guild-item[title="{name}"]').click()
        time.sleep(1)
    except Exception as e:
        print("未找到频道，请检查是否已加入该频道", e)
        return

    try:
        # 2️⃣ 等待发帖输入框出现
        await page.wait_for_selector(".editor-header.pointer")
        await page.locator(".editor-header.pointer").click()
        time.sleep(1)

        # 3️⃣ 等待真正的编辑区域（contenteditable="true"）出现
        await page.wait_for_selector('.ProseMirror[contenteditable="true"]')
        time.sleep(1)

        # 4️⃣ 输入文字
        await page.locator('.ProseMirror[contenteditable="true"]').fill(content)
        time.sleep(1)

        # 5️⃣ 上传图片
        if img:
            await page.set_input_files('input[type="file"]', [img])
            time.sleep(1)

        # 6️⃣ 等待“发表”按钮可用并点击
        await page.wait_for_selector(".g-button.g-button--primary.btn:not([disabled])")
        await page.locator(".g-button.g-button--primary.btn:not([disabled])").click()
        time.sleep(1)
    except Exception as e:
        print("程序执行异常，发布失败")
        return

    print("发布成功")


# 评论
async def comment(
    page: Page,
    title="",
    comment_probability=0.8,
    time_limit=0,
    keywords=None,
    content="",
    min_comments=0,
    min_like=0,
    min_share=0,
    post_count=50,
    comment_interval_min=2,
    comment_interval_max=5,
    browse_mode="new_post",
    task_id=None,
    publisher_name="小白兔",  # 发布者名称，用于检查是否已评论
):
    """
    评论帖子功能：
    - title: 频道名称
    - comment_probability: 评论的概率（0到1之间）
    - time_limit: 只评论指定时间范围内的帖子（以小时为单位，x小时内，0表示不限制）
    - keywords: 关键字（用逗号或#隔开，指定需要匹配的关键字，默认为None表示没有限制）
    - content: 评论内容（可使用#分隔多个评论内容，每次评论随机选择一个）
    - min_comments: 评论数量限制，只有评论数量高于这个数字才进行评论（0表示无要求）
    - min_like: 点赞量限制，只有点赞数量高于这个数字才进行评论（0表示无要求）
    - min_share: 转发量限制，只有转发数量高于这个数字才进行评论（0表示无要求）
    - post_count: 浏览帖子数量，当浏览的帖子数量达到此值时停止（0表示不限制）
    - comment_interval_min: 评论间隔时间最小值（秒）
    - comment_interval_max: 评论间隔时间最大值（秒）
    - browse_mode: 浏览方式，"new_post"(新发表), "new_reply"(新回复), "hot"(热门)
    - task_id: 任务ID，用于检查任务状态
    - publisher_name: 发布者名称，用于检查是否已评论（默认为"小白兔"）
    """

    if not content or not content.strip():
        print("评论内容不能为空")
        return

    # 处理评论内容：如果包含#分隔符，则分割成列表
    comment_contents = []
    if "#" in content:
        comment_contents = [c.strip() for c in content.split("#") if c.strip()]
    else:
        comment_contents = [content.strip()]

    if not comment_contents:
        print("评论内容不能为空")
        return

    # 1️⃣ 点击"xxx频道"
    try:
        await page.wait_for_selector(
            f'div.my-guild-item[title="{title}"]', timeout=2000
        )
        await page.locator(f'div.my-guild-item[title="{title}"]').click()
        # 等待页面加载
        await asyncio.sleep(2)
        print(f"已切换到频道：{title}")
    except Exception as e:
        print(f"未找到频道，请检查是否已加入该频道: {e}")
        return

    # 2️⃣ 选择浏览方式
    await select_browse_mode(page, browse_mode)

    # 记录已浏览的帖子（用于控制停止条件）
    save_browsed_posts = []

    # 首次加载，等待帖子出现
    try:
        await page.wait_for_selector(".msgBox", timeout=5000)
        await asyncio.sleep(1)
    except Exception as e:
        print("等待帖子加载超时，尝试滚动页面...")
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(2)

    while True:
        # 检查任务状态（如果提供了task_id）
        if task_id:
            state = task_state_manager.get_state(task_id)
            if state == "stopped":
                print("任务已停止")
                return
            elif state == "paused":
                print("任务已暂停，等待恢复...")
                # 等待恢复（异步等待）
                while True:
                    current_state = task_state_manager.get_state(task_id)
                    if current_state == "running":
                        print("任务已恢复，继续评论...")
                        break
                    elif current_state == "stopped":
                        print("任务已停止")
                        return
                    await asyncio.sleep(0.5)

        # 检查是否达到浏览数量限制
        if post_count > 0 and len(save_browsed_posts) >= post_count:
            print(
                f"已浏览 {len(save_browsed_posts)} 个帖子，达到目标数量 {post_count}，停止浏览"
            )
            break

        # 获取当前页面的所有帖子
        posts = await page.locator(".msgBox").all()

        # 遍历当前页面的所有帖子
        for index, post in enumerate(posts):
            # 检查任务状态（如果提供了task_id）
            if task_id:
                state = task_state_manager.get_state(task_id)
                if state == "stopped":
                    print("任务已停止")
                    return
                elif state == "paused":
                    print("任务已暂停，等待恢复...")
                    # 等待恢复（异步等待）
                    while True:
                        current_state = task_state_manager.get_state(task_id)
                        if current_state == "running":
                            print("任务已恢复，继续评论...")
                            break
                        elif current_state == "stopped":
                            print("任务已停止")
                            return
                        await asyncio.sleep(0.5)

            # 检查是否达到浏览数量限制
            if post_count > 0 and len(save_browsed_posts) >= post_count:
                break

            try:
                # 获取 a 标签中的 href 属性，提取帖子的唯一 ID
                href = await post.locator("a").first.get_attribute("href")
                if not href or "post/" not in href:
                    continue  # 跳过无效的帖子

                post_id = href.split("post/")[1].split("?")[0]  # 提取 post/ 后面的 ID

                if post_id not in save_browsed_posts:
                    save_browsed_posts.append(post_id)
                else:
                    continue

                # 随机间隔时间等待（在等待过程中检查任务状态）
                wait_time = random.uniform(comment_interval_min, comment_interval_max)
                # 分段等待，每次检查任务状态
                wait_steps = int(wait_time) + 1
                for i in range(wait_steps):
                    # 检查任务状态（如果提供了task_id）
                    if task_id:
                        state = task_state_manager.get_state(task_id)
                        if state == "stopped":
                            print("任务已停止")
                            return
                        elif state == "paused":
                            print("任务已暂停，等待恢复...")
                            # 等待恢复（异步等待）
                            while True:
                                current_state = task_state_manager.get_state(task_id)
                                if current_state == "running":
                                    print("任务已恢复，继续等待...")
                                    break
                                elif current_state == "stopped":
                                    print("任务已停止")
                                    return
                                await asyncio.sleep(0.5)

                    remaining = wait_time - i
                    if remaining > 0:
                        # 输出倒计时日志（在同一行更新）
                        print(f"[COUNTDOWN]等待 {remaining:.1f} 秒后继续评论...")
                        await asyncio.sleep(min(1.0, remaining))
                    else:
                        break
                # 等待完成，输出普通日志清除倒计时行
                print("等待完成，继续处理下一个帖子...")

                # 滚动到帖子
                try:
                    # 滚动到当前帖子
                    await page.evaluate(
                        f'document.getElementsByClassName("msgBox")[{index}].scrollIntoView({{ behavior: "smooth", block: "start" }})'
                    )
                    print("滚动到当前帖子", end="\n\n")
                    await asyncio.sleep(1)

                    # 滚动后检查任务状态
                    if task_id:
                        state = task_state_manager.get_state(task_id)
                        if state == "stopped":
                            print("任务已停止")
                            return
                except Exception as e:
                    print(f"滚动到帖子失败: {e}，继续处理...")

                # 获取帖子的内容
                post_detail = await post.locator(
                    ".game-guild-main__short-content__media-detail, .game-guild-main__short-content__detail"
                ).text_content()

                # 获取帖子的发布时间
                post_time_text = await post.locator(".edit-time").text_content()
                post_time = parse_post_time(post_time_text)

                # 获取帖子的点赞数、评论数、分享数
                operation_num = []
                post_comments_text = await post.locator(
                    ".game-guild-main__short-content__text"
                ).all()
                for tag in post_comments_text:
                    post_comments = str_to_number(await tag.text_content())
                    operation_num.append(post_comments)

                print(
                    f"[{len(save_browsed_posts)}/{post_count if post_count > 0 else '∞'}]",
                    "浏览帖子 ==>>> ",
                    post_detail[:30].replace("\n", " ") if post_detail else "",
                    "帖子发布于：",
                    post_time_text,
                    "点赞、评论、转发数：",
                    operation_num,
                )

                # 获取发布人的头衔
                user_info = await post.locator(".user-info").text_content()
                # 检查发布人的头衔是否包含"频道主"，如果有则跳过该帖子
                if (
                    "频道主" in user_info
                    or "管理员" in user_info
                    or "官方" in user_info
                ):
                    print("该帖子由频道主发布，跳过", end="\n\n")
                    continue

                # 3️⃣ 判断是否满足评论概率
                if random.random() > comment_probability:
                    print("评论概率不足，跳过该帖子", end="\n\n")
                    continue

                # 4️⃣ 判断帖子发布时间是否符合限制（time_limit=0表示不限制）
                if time_limit > 0 and post_time > timedelta(hours=time_limit):
                    print("帖子发布时间超出限制，跳过该帖子", end="\n\n")
                    continue

                # 5️⃣ 判断帖子内容是否包含关键词
                if keywords and keywords.strip():
                    # 支持逗号和#分隔的关键词
                    keyword_list = [
                        k.strip()
                        for k in keywords.replace("#", ",").split(",")
                        if k.strip()
                    ]
                    if keyword_list:
                        if not any(keyword in post_detail for keyword in keyword_list):
                            print("帖子内容不包含关键词，跳过该帖子", end="\n\n")
                            continue

                # 6️⃣ 判断数量是否符合限制（0表示无要求）
                if len(operation_num) > 0:
                    if min_like > 0 and operation_num[0] < min_like:
                        print("帖子点赞数量低于限制，跳过该帖子", end="\n\n")
                        continue
                    if (
                        len(operation_num) > 1
                        and min_comments > 0
                        and operation_num[1] < min_comments
                    ):
                        print("帖子评论数量低于限制，跳过该帖子", end="\n\n")
                        continue
                    if (
                        len(operation_num) > 2
                        and min_share > 0
                        and operation_num[2] < min_share
                    ):
                        print("帖子分享数量低于限制，跳过该帖子", end="\n\n")
                        continue

                # 在执行评论操作前再次检查任务状态
                if task_id:
                    state = task_state_manager.get_state(task_id)
                    if state == "stopped":
                        print("任务已停止")
                        return
                    elif state == "paused":
                        print("任务已暂停，等待恢复...")
                        # 等待恢复（异步等待）
                        while True:
                            current_state = task_state_manager.get_state(task_id)
                            if current_state == "running":
                                print("任务已恢复，继续评论...")
                                break
                            elif current_state == "stopped":
                                print("任务已停止")
                                return
                            await asyncio.sleep(0.5)

                # 7️⃣ 执行评论操作
                try:
                    # 点击帖子，打开弹窗
                    await post.locator("a").first.click()
                    await asyncio.sleep(1)  # 等待弹窗出现

                    # 检查任务状态
                    if task_id:
                        state = task_state_manager.get_state(task_id)
                        if state == "stopped":
                            print("任务已停止")
                            return

                    # 等待弹窗出现（class="modal-content"）
                    try:
                        await page.wait_for_selector(".modal-content", timeout=3000)
                        await asyncio.sleep(0.5)
                    except Exception:
                        print("弹窗未出现，跳过该帖子", end="\n\n")
                        continue

                    # 获取modal-content的引用，用于后续操作
                    modal_content = page.locator(".modal-content").first

                    # 查询class="comment-list"的div，获取里面的文字
                    try:
                        # 在modal-content内部查找comment-list
                        comment_list = await modal_content.locator(
                            ".comment-list-item__info__title-name"
                        ).all()
                        public_user_name = [
                            await i.text_content() for i in comment_list
                        ]
                        print(public_user_name)
                        if publisher_name in public_user_name:
                            print(
                                f"已在该帖子下发布过评论，跳过",
                                end="\n\n",
                            )
                            # 关闭弹窗
                            try:
                                close_btn = modal_content.locator(".close-icon").first
                                if await close_btn.is_visible():
                                    await close_btn.click()
                                    await asyncio.sleep(0.5)
                            except Exception:
                                # 如果关闭失败，尝试按ESC键关闭弹窗
                                try:
                                    await page.keyboard.press("Escape")
                                    await asyncio.sleep(0.5)
                                except Exception:
                                    pass
                            continue
                    except Exception as e:
                        pass

                    # 点击class="bottom-input"的div
                    try:
                        await page.wait_for_selector(".bottom-input", timeout=3000)
                        await modal_content.locator(".bottom-input").first.click()
                        await asyncio.sleep(0.5)  # 等待编辑器出现
                    except Exception as e:
                        print(f"无法找到评论输入框: {e}，跳过该帖子", end="\n\n")
                        # 关闭弹窗
                        try:
                            close_btn = modal_content.locator(".close-icon").first
                            if await close_btn.is_visible():
                                await close_btn.click()
                                await asyncio.sleep(0.5)
                        except Exception:
                            # 如果关闭失败，尝试按ESC键关闭弹窗
                            try:
                                await page.keyboard.press("Escape")
                                await asyncio.sleep(0.5)
                            except Exception:
                                pass
                        continue

                    # 等待编辑器出现（comment-editor-container）
                    try:
                        await page.wait_for_selector(
                            ".comment-editor-container", timeout=3000
                        )
                        await asyncio.sleep(0.5)
                    except Exception:
                        print("编辑器未出现，跳过该帖子", end="\n\n")
                        # 关闭弹窗
                        try:
                            close_btn = modal_content.locator(".close-icon").first
                            if await close_btn.is_visible():
                                await close_btn.click()
                                await asyncio.sleep(0.5)
                        except Exception:
                            # 如果关闭失败，尝试按ESC键关闭弹窗
                            try:
                                await page.keyboard.press("Escape")
                                await asyncio.sleep(0.5)
                            except Exception:
                                pass
                        continue

                    # 查找可编辑的输入框（contenteditable="true"）
                    try:
                        editor = page.locator(
                            '.comment-editor-container [contenteditable="true"]'
                        ).first
                        if await editor.is_visible():
                            # 清空现有内容（如果有）
                            await editor.click()
                            await asyncio.sleep(0.3)
                            # 随机选择一个评论内容
                            selected_content = random.choice(comment_contents)
                            # 输入评论内容
                            await editor.fill("")
                            await editor.type(
                                selected_content, delay=50
                            )  # 使用type模拟真实输入
                            await asyncio.sleep(0.5)
                        else:
                            print("无法找到可编辑的输入框，跳过该帖子", end="\n\n")
                            # 关闭弹窗
                            try:
                                close_btn = modal_content.locator(".close-icon").first
                                if await close_btn.is_visible():
                                    await close_btn.click()
                                    await asyncio.sleep(0.5)
                            except Exception:
                                # 如果关闭失败，尝试按ESC键关闭弹窗
                                try:
                                    await page.keyboard.press("Escape")
                                    await asyncio.sleep(0.5)
                                except Exception:
                                    pass
                            continue
                    except Exception as e:
                        print(f"输入评论内容失败: {e}，跳过该帖子", end="\n\n")
                        # 关闭弹窗
                        try:
                            close_btn = modal_content.locator(".close-icon").first
                            if await close_btn.is_visible():
                                await close_btn.click()
                                await asyncio.sleep(0.5)
                        except Exception:
                            # 如果关闭失败，尝试按ESC键关闭弹窗
                            try:
                                await page.keyboard.press("Escape")
                                await asyncio.sleep(0.5)
                            except Exception:
                                pass
                        continue

                    # 点击发送按钮
                    try:
                        send_btn = page.locator(
                            ".comment-editor-container button.btn"
                        ).first
                        # 等待按钮可用（disabled属性消失）
                        await page.wait_for_timeout(500)  # 等待按钮状态更新
                        # 检查按钮是否可用
                        is_disabled = await send_btn.get_attribute("disabled")
                        if is_disabled:
                            print("发送按钮不可用，跳过该帖子", end="\n\n")
                            # 关闭弹窗
                            try:
                                close_btn = modal_content.locator(".close-icon").first
                                if await close_btn.is_visible():
                                    await close_btn.click()
                                    await asyncio.sleep(0.5)
                            except Exception:
                                # 如果关闭失败，尝试按ESC键关闭弹窗
                                try:
                                    await page.keyboard.press("Escape")
                                    await asyncio.sleep(0.5)
                                except Exception:
                                    pass
                            continue

                        await send_btn.click()
                        await asyncio.sleep(1)  # 等待发送完成
                        print("成功发表评论", end="\n\n")
                    except Exception as e:
                        print(f"点击发送按钮失败: {e}，跳过该帖子", end="\n\n")
                        # 关闭弹窗
                        try:
                            close_btn = modal_content.locator(".close-icon").first
                            if await close_btn.is_visible():
                                await close_btn.click()
                                await asyncio.sleep(0.5)
                        except Exception:
                            # 如果关闭失败，尝试按ESC键关闭弹窗
                            try:
                                await page.keyboard.press("Escape")
                                await asyncio.sleep(0.5)
                            except Exception:
                                pass
                        continue

                    # 发送完成后点击关闭按钮
                    try:
                        await asyncio.sleep(0.5)  # 等待评论发送完成
                        close_btn = modal_content.locator(".close-icon").first
                        if await close_btn.is_visible():
                            await close_btn.click()
                            await asyncio.sleep(0.5)
                    except Exception as e:
                        # 如果关闭失败，尝试按ESC键关闭弹窗
                        try:
                            await page.keyboard.press("Escape")
                            await asyncio.sleep(0.5)
                        except Exception:
                            pass
                except Exception as e:
                    print(f"评论失败: {e}，跳过该帖子", end="\n\n")
                    # 尝试关闭弹窗（如果打开的话）
                    try:
                        modal_content = page.locator(".modal-content").first
                        close_btn = modal_content.locator(".close-icon").first
                        if await close_btn.is_visible():
                            await close_btn.click()
                            await asyncio.sleep(0.5)
                    except Exception:
                        # 如果关闭失败，尝试按ESC键关闭弹窗
                        try:
                            await page.keyboard.press("Escape")
                            await asyncio.sleep(0.5)
                        except Exception:
                            pass

                break
            except Exception as e:
                print(f"处理帖子时出错: {e}，跳过该帖子")
                continue

        # 如果已达到浏览数量限制，退出循环
        if post_count > 0 and len(save_browsed_posts) >= post_count:
            break


# 点赞
async def like(
    page,
    title="",
    like_probability=0.8,
    time_limit=0,
    keywords=None,
    min_comments=0,
    min_like=0,
    min_share=0,
    post_count=50,
    like_interval_min=2,
    like_interval_max=5,
    browse_mode="new_post",
    task_id=None,
):
    """
    点赞帖子功能：
    - title: 频道名称
    - like_probability: 点赞的概率（0到1之间）
    - time_limit: 只点赞指定时间范围内的帖子（以小时为单位，x小时内，0表示不限制）
    - keywords: 关键字（用逗号或#隔开，指定需要匹配的关键字，默认为None表示没有限制）
    - min_comments: 评论数量限制，只有评论数量高于这个数字才进行点赞（0表示无要求）
    - min_like: 点赞量限制，只有点赞数量高于这个数字才进行点赞（0表示无要求）
    - min_share: 转发量限制，只有转发数量高于这个数字才进行点赞（0表示无要求）
    - post_count: 浏览帖子数量，当浏览的帖子数量达到此值时停止（0表示不限制）
    - like_interval_min: 点赞间隔时间最小值（秒）
    - like_interval_max: 点赞间隔时间最大值（秒）
    - browse_mode: 浏览方式，"new_post"(新发表), "new_reply"(新回复), "hot"(热门)
    - task_id: 任务ID，用于检查任务状态
    """

    # 1️⃣ 点击"xxx频道"
    try:
        await page.wait_for_selector(
            f'div.my-guild-item[title="{title}"]', timeout=2000
        )
        await page.locator(f'div.my-guild-item[title="{title}"]').click()
        # 等待页面加载
        await asyncio.sleep(2)
        print(f"已切换到频道：{title}")
    except Exception as e:
        print(f"未找到频道，请检查是否已加入该频道: {e}")
        return

    # 2️⃣ 选择浏览方式
    await select_browse_mode(page, browse_mode)

    # 记录已浏览的帖子（用于控制停止条件）
    save_browsed_posts = []

    # 首次加载，等待帖子出现
    try:
        await page.wait_for_selector(".msgBox", timeout=5000)
        await asyncio.sleep(1)
    except Exception as e:
        print("等待帖子加载超时，尝试滚动页面...")
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(2)

    while True:
        # 检查任务状态（如果提供了task_id）
        if task_id:
            state = task_state_manager.get_state(task_id)
            if state == "stopped":
                print("任务已停止")
                return
            elif state == "paused":
                print("任务已暂停，等待恢复...")
                # 等待恢复（异步等待）
                while True:
                    current_state = task_state_manager.get_state(task_id)
                    if current_state == "running":
                        print("任务已恢复，继续点赞...")
                        break
                    elif current_state == "stopped":
                        print("任务已停止")
                        return
                    await asyncio.sleep(0.5)

        # 检查是否达到浏览数量限制
        if post_count > 0 and len(save_browsed_posts) >= post_count:
            print(
                f"已浏览 {len(save_browsed_posts)} 个帖子，达到目标数量 {post_count}，停止浏览"
            )
            break

        # 获取当前页面的所有帖子
        posts = await page.locator(".msgBox").all()

        # 遍历当前页面的所有帖子
        for index, post in enumerate(posts):
            # 检查任务状态（如果提供了task_id）
            if task_id:
                state = task_state_manager.get_state(task_id)
                if state == "stopped":
                    print("任务已停止")
                    return
                elif state == "paused":
                    print("任务已暂停，等待恢复...")
                    # 等待恢复（异步等待）
                    while True:
                        current_state = task_state_manager.get_state(task_id)
                        if current_state == "running":
                            print("任务已恢复，继续点赞...")
                            break
                        elif current_state == "stopped":
                            print("任务已停止")
                            return
                        await asyncio.sleep(0.5)

            # 检查是否达到浏览数量限制或点赞次数限制
            if post_count > 0 and len(save_browsed_posts) >= post_count:
                break

            try:
                # 获取 a 标签中的 href 属性，提取帖子的唯一 ID
                href = await post.locator("a").first.get_attribute("href")
                if not href or "post/" not in href:
                    continue  # 跳过无效的帖子

                post_id = href.split("post/")[1].split("?")[0]  # 提取 post/ 后面的 ID

                if post_id not in save_browsed_posts:
                    save_browsed_posts.append(post_id)
                else:
                    continue

                # 随机间隔时间等待（在等待过程中检查任务状态）
                wait_time = random.uniform(like_interval_min, like_interval_max)
                # 分段等待，每次检查任务状态
                wait_steps = int(wait_time) + 1
                for i in range(wait_steps):
                    # 检查任务状态（如果提供了task_id）
                    if task_id:
                        state = task_state_manager.get_state(task_id)
                        if state == "stopped":
                            print("任务已停止")
                            return
                        elif state == "paused":
                            print("任务已暂停，等待恢复...")
                            # 等待恢复（异步等待）
                            while True:
                                current_state = task_state_manager.get_state(task_id)
                                if current_state == "running":
                                    print("任务已恢复，继续等待...")
                                    break
                                elif current_state == "stopped":
                                    print("任务已停止")
                                    return
                                await asyncio.sleep(0.5)

                    remaining = wait_time - i
                    if remaining > 0:
                        # 输出倒计时日志（在同一行更新）
                        print(f"[COUNTDOWN]等待 {remaining:.1f} 秒后继续点赞...")
                        await asyncio.sleep(min(1.0, remaining))
                    else:
                        break
                # 等待完成，输出普通日志清除倒计时行
                print("等待完成，继续处理下一个帖子...")

                # 获取帖子的内容
                post_detail = await post.locator(
                    ".game-guild-main__short-content__media-detail, .game-guild-main__short-content__detail"
                ).text_content()

                # 获取帖子的发布时间
                post_time_text = await post.locator(".edit-time").text_content()
                post_time = parse_post_time(post_time_text)

                # 获取帖子的点赞数、评论数、分享数
                operation_num = []
                post_comments_text = await post.locator(
                    ".game-guild-main__short-content__text"
                ).all()
                for tag in post_comments_text:
                    post_comments = str_to_number(await tag.text_content())
                    operation_num.append(post_comments)

                    print(
                        f"[{len(save_browsed_posts)}/{post_count if post_count > 0 else '∞'}]",
                        "浏览帖子 ==>>> ",
                        post_detail[:30].replace("\n", " ") if post_detail else "",
                        "帖子发布于：",
                        post_time_text,
                        "点赞、评论、转发数：",
                        operation_num,
                    )

                    # 获取发布人的头衔
                    user_info = await post.locator(".user-info").text_content()
                    # 检查发布人的头衔是否包含"频道主"，如果有则跳过该帖子
                    if (
                        "频道主" in user_info
                        or "管理员" in user_info
                        or "官方" in user_info
                    ):
                        print("该帖子由频道主发布，跳过", end="\n\n")
                        continue

                    # 3️⃣ 判断是否满足点赞概率
                    if random.random() > like_probability:
                        print("点赞概率不足，跳过该帖子", end="\n\n")
                        continue

                    # 4️⃣ 判断帖子发布时间是否符合限制（time_limit=0表示不限制）
                    if time_limit > 0 and post_time > timedelta(hours=time_limit):
                        print("帖子发布时间超出限制，跳过该帖子", end="\n\n")
                        continue

                    # 5️⃣ 判断帖子内容是否包含关键词
                    if keywords and keywords.strip():
                        # 支持逗号和#分隔的关键词
                        keyword_list = [
                            k.strip()
                            for k in keywords.replace("#", ",").split(",")
                            if k.strip()
                        ]
                        if keyword_list:
                            if not any(
                                keyword in post_detail for keyword in keyword_list
                            ):
                                print("帖子内容不包含关键词，跳过该帖子", end="\n\n")
                                continue

                    # 6️⃣ 判断数量是否符合限制（0表示无要求）
                    if len(operation_num) > 0:
                        if min_like > 0 and operation_num[0] < min_like:
                            print("帖子点赞数量低于限制，跳过该帖子", end="\n\n")
                            continue
                        if (
                            len(operation_num) > 1
                            and min_comments > 0
                            and operation_num[1] < min_comments
                        ):
                            print("帖子评论数量低于限制，跳过该帖子", end="\n\n")
                            continue
                        if (
                            len(operation_num) > 2
                            and min_share > 0
                            and operation_num[2] < min_share
                        ):
                            print("帖子分享数量低于限制，跳过该帖子", end="\n\n")
                            continue

                # 7️⃣ 防止重复点赞，检查是否已经点赞
                if await is_liked(post):
                    print(f"帖子已点赞，跳过", end="\n\n")
                    continue  # 已经点赞，跳过该帖子

                # 在执行点赞操作前再次检查任务状态
                if task_id:
                    state = task_state_manager.get_state(task_id)
                    if state == "stopped":
                        print("任务已停止")
                        return
                    elif state == "paused":
                        print("任务已暂停，等待恢复...")
                        # 等待恢复（异步等待）
                        while True:
                            current_state = task_state_manager.get_state(task_id)
                            if current_state == "running":
                                print("任务已恢复，继续点赞...")
                                break
                            elif current_state == "stopped":
                                print("任务已停止")
                                return
                            await asyncio.sleep(0.5)

                # 8️⃣ 执行点赞操作
                try:
                    await post.locator(
                        ".game-guild-main__short-content__operation > :nth-child(1)"
                    ).click()
                    # 等待点赞操作完成（网络请求和DOM更新）
                    await asyncio.sleep(1)

                    # 在验证点赞结果前检查任务状态
                    if task_id:
                        state = task_state_manager.get_state(task_id)
                        if state == "stopped":
                            print("任务已停止")
                            return

                    # 验证点赞是否真的成功
                    if await is_liked(post):
                        print(f"成功点赞帖子", end="\n\n")
                    else:
                        print(
                            f"点赞操作已执行，但验证失败（可能网络延迟或操作失败）",
                            end="\n\n",
                        )
                except Exception as e:
                    print(f"点赞失败: {e}")

                    # 当执行完成最后一个帖子后，进行滚动
                    if index == len(posts) - 1:
                        # # 滚动到当前帖子
                        # await page.evaluate(
                        #     f'document.getElementsByClassName("msgBox")[{index}].scrollIntoView({{ behavior: "smooth", block: "start" }})'
                        # )
                        # await asyncio.sleep(1)

                        # 滚动到当前帖子（使用 post_id 定位元素，不依赖 index）
                        try:
                            # 滚动前将鼠标移动到页面安全位置（左上角），避免触发头像hover效果
                            try:
                                await page.mouse.move(
                                    10, 10
                                )  # 移动到页面左上角安全位置
                                await asyncio.sleep(0.1)  # 短暂等待确保鼠标移动完成
                            except Exception:
                                pass  # 如果鼠标移动失败，继续执行滚动

                            # 使用 post_id 来定位元素并滚动
                            await page.evaluate(
                                """
                                (postId) => {
                                    const links = document.querySelectorAll('a[href*="post/"]');
                                    for (let link of links) {
                                        if (link.getAttribute('href').includes(postId)) {
                                            // 找到包含该链接的最近的 .msgBox 父元素
                                            const msgBox = link.closest('.msgBox');
                                            if (msgBox) {
                                                msgBox.scrollIntoView({ behavior: 'smooth', block: 'center' });
                                                return;
                                            }
                                        }
                                    }
                                }
                                """,
                                post_id,
                            )
                            await asyncio.sleep(1)

                            # 滚动后再次确保鼠标在安全位置，避免悬停在元素上
                            try:
                                await page.mouse.move(10, 10)
                            except Exception:
                                pass

                            # 滚动后检查任务状态
                            if task_id:
                                state = task_state_manager.get_state(task_id)
                                if state == "stopped":
                                    print("任务已停止")
                                    return
                        except Exception as e:
                            print(f"滚动到帖子失败: {e}，继续处理...")

            except Exception as e:
                print(f"处理帖子时出错: {e}，跳过该帖子")
                continue

        # 如果已达到浏览数量限制，退出循环
        if post_count > 0 and len(save_browsed_posts) >= post_count:
            break


# 选择浏览方式
async def select_browse_mode(page: Page, browse_mode, timeout: int = 5000) -> bool:
    """
    选择浏览方式（新发表/新回复/热门）

    :param page: Playwright Page
    :param browse_mode: "new_post" | "new_reply" | "hot"
    :param timeout: 超时时间（毫秒）
    :return: 是否成功选择
    """

    # 滚动前将鼠标移动到页面安全位置（左上角），避免触发头像hover效果
    try:
        await page.mouse.move(10, 10)  # 移动到页面左上角安全位置
        await asyncio.sleep(0.1)  # 短暂等待确保鼠标移动完成
    except Exception:
        pass  # 如果鼠标移动失败，继续执行滚动

    LABEL_MAP = {
        "new_post": "新发表",
        "new_reply": "新回复",
        "hot": "热门",
    }
    assert browse_mode in LABEL_MAP, f"Unsupported browse_mode: {browse_mode}"
    label = LABEL_MAP[browse_mode]

    # 关键定位器
    trigger = page.locator(".feeds-arrange")
    menu = page.locator(".tab-seq-drop-down-menu")
    option = page.locator(".tab-seq-drop-down-menu__item").filter(has_text=label)

    try:
        # 若菜单未显示，点击触发器；若已显示则跳过
        if not await menu.is_visible():
            await trigger.click()
            await menu.wait_for(state="visible", timeout=timeout)

        # 选择目标项（用文本过滤，避免 nth-child 易碎）
        await option.first.click()

        # 期望菜单收起（有些实现是立即移除，有些是隐藏）
        try:
            # 优先等待隐藏（display:none 或不可见）
            await expect(menu).to_be_hidden(timeout=timeout)
        except Exception:
            # 如未隐藏，至少确认点击后仍然可见但选项已不可点击（容错）
            pass

        # 滚动前将鼠标移动到页面安全位置（左上角），避免触发头像hover效果
        try:
            await page.mouse.move(10, 10)  # 移动到页面左上角安全位置
            await asyncio.sleep(0.1)  # 短暂等待确保鼠标移动完成
        except Exception:
            pass  # 如果鼠标移动失败，继续执行滚动

        return True

    except Exception as e:
        # 轻量回退：若文本选择失败，尝试按顺序点击（保持与你原逻辑一致）
        try:
            index_map = {"new_post": 1, "new_reply": 2, "hot": 3}
            idx = index_map[browse_mode]
            if not await menu.is_visible():
                await trigger.click()
                await menu.wait_for(state="visible", timeout=timeout)
            await page.locator(
                f".tab-seq-drop-down-menu div:nth-child({idx})"
            ).first.click()
            try:
                await expect(menu).to_be_hidden(timeout=timeout)
            except Exception:
                pass
            return True
        except Exception:
            print(f"选择浏览方式时发生错误，将使用默认浏览方式继续执行: {e}")
            return False


# 辅助函数：解析发布时间文本
def parse_post_time(post_time_text):
    """
    解析帖子时间，将其转换为timedelta（相对于当前时间的差值）。
    例如：'2天前' 转换为 timedelta(2 * 24 * 60 * 60)。
    """
    if "小时前" in post_time_text:
        hours_ago = int(post_time_text.split("小时前")[0])
        return timedelta(hours=hours_ago)
    elif "天前" in post_time_text:
        days_ago = int(post_time_text.split("天前")[0])
        return timedelta(days=days_ago)
    else:
        return timedelta(days=365)  # 假设超出时间范围，返回非常久远的时间


# 辅助函数：将字符串转换为数字
def str_to_number(s: object) -> Union[int, float]:
    """
    将传入的值安全地转换为数字：
      - 整数字符串 -> 返回 int
      - 小数字符串（含小数点或科学计数法） -> 返回 float
      - 非数字 -> 返回 0
    绝对不会抛出异常，始终返回 int 或 float。
    """
    try:
        if s is None:
            return 0
        text = str(s).strip()
        if text == "":
            return 0

        # 整数模式（仅包含可选符号 + 或 - 和数字）
        int_re = re.compile(r"^[+-]?\d+$")
        # 小数/科学计数法模式：
        #  - \d*\.\d+  -> .5 或 0.5 或 1.23
        #  - \d+\.\d*  -> 5. 或 5.0
        #  - (?: ... )(?:[eE][+-]?\d+)? 可选指数部分
        float_re = re.compile(r"^[+-]?(?:\d*\.\d+|\d+\.\d*)(?:[eE][+-]?\d+)?$")

        if int_re.match(text):
            # 虽然正则保证了安全性，但仍用 try/except 防止任何意外
            try:
                return int(text, 10)
            except Exception:
                return 0

        if float_re.match(text):
            try:
                return float(text)
            except Exception:
                return 0

        # 其余情况视为非数字
        return 0

    except Exception:
        # 兜底：任何意外都返回 0
        return 0


# 判断是否已经点赞的函数
async def is_liked(post):
    """
    判断帖子是否已经点赞：通过检查 svg 中的 path 标签是否有 fill-rule="evenodd" 来判断。
    - 如果有 fill-rule="evenodd" 属性，表示已点赞；
    - 否则表示未点赞。
    """
    try:
        # 获取 svg 中的 path 元素，检查是否包含 fill-rule="evenodd"
        path_element = await post.locator(
            ".game-guild-main__short-content__operation__item.game-guild-main__short-content__like svg path"
        ).get_attribute("fill-rule")
        return path_element == "evenodd"  # 如果存在该属性，表示已点赞
    except Exception as e:
        print(f"检查点赞状态失败: {e}")
        return False  # 如果判断出错或元素未找到，认为未点赞


async def main():
    async with async_playwright() as playwright:
        await run(playwright)


if __name__ == "__main__":
    asyncio.run(main())
