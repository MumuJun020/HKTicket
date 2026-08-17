import requests
import json
import time

# 官方文档地址
# https://doc2.bitbrowser.cn/jiekou/ben-di-fu-wu-zhi-nan.html

# 此demo仅作为参考使用，以下使用的指纹参数仅是部分参数，完整参数请参考文档

url = "http://127.0.0.1:54345"
headers = {"Content-Type": "application/json"}


def createBrowser():  # 创建或者更新窗口，指纹参数 browserFingerPrint 如没有特定需求，只需要指定下内核即可，如果需要更详细的参数，请参考文档
    json_data = {
        "name": "google",  # 窗口名称
        "remark": "",  # 备注
        "proxyMethod": 2,  # 代理方式 2自定义 3 提取IP
        # 代理类型  ['noproxy', 'http', 'https', 'socks5', 'ssh']
        "proxyType": "noproxy",
        "host": "",  # 代理主机
        "port": "",  # 代理端口
        "proxyUserName": "",  # 代理账号
        "browserFingerPrint": {  # 指纹对象
            "coreVersion": "124"  # 内核版本，注意，win7/win8/winserver 2012 已经不支持112及以上内核了，无法打开
        },
    }

    res = requests.post(
        f"{url}/browser/update", data=json.dumps(json_data), headers=headers
    ).json()
    browserId = res["data"]["id"]
    print(browserId)
    return browserId


def updateBrowser():  # 更新窗口，支持批量更新和按需更新，ids 传入数组，单独更新只传一个id即可，只传入需要修改的字段即可，比如修改备注，具体字段请参考文档，browserFingerPrint指纹对象不修改，则无需传入
    json_data = {
        "ids": ["93672cf112a044f08b653cab691216f0"],
        "remark": "我是一个备注",
        "browserFingerPrint": {},
    }
    res = requests.post(
        f"{url}/browser/update/partial", data=json.dumps(json_data), headers=headers
    ).json()
    print(res)


class BitBrowserNotRunning(RuntimeError):
    """比特浏览器客户端没启动。单独一个类型，方便上层给出可操作的提示。"""


def _post(path, payload, timeout=15):
    """
    统一的接口调用。把「客户端没开」这种最常见的故障翻译成人话——
    否则前端会收到一长串 ConnectionError 堆栈，看不出到底该干什么。
    """
    try:
        return requests.post(
            f"{url}{path}", data=json.dumps(payload), headers=headers, timeout=timeout
        ).json()
    except requests.exceptions.ConnectionError:
        raise BitBrowserNotRunning(
            "连不上比特浏览器（127.0.0.1:54345），请先打开比特浏览器客户端并保持登录"
        )


def getBrowserList(page=0, pageSize=100, **filters):
    """
    分页获取浏览器窗口列表。

    :param page: 页码，**从 0 开始**（0 是第一页）
    :param pageSize: 每页条数，官方上限 100，传更大也只返回 100 条
    :param filters: 可选过滤条件，如 groupId / name / remark / seq /
                    minSeq / maxSeq / sort / ownedByMe / opened
    :return: 接口原始返回，形如 {"success": True, "data": {"list": [...], "totalNum": N}}
    """
    json_data = {"page": page, "pageSize": pageSize}
    json_data.update({k: v for k, v in filters.items() if v is not None})
    return _post("/browser/list", json_data)


def getAllBrowsers(**filters):
    """
    翻页取回**所有**浏览器窗口，返回精简后的列表。

    单页上限 100 条，所以这里按 totalNum 自动翻页拼接。
    每项只保留抢票面板用得上的字段：id / seq / name / remark。

    :raises RuntimeError: 接口返回 success=false 时抛出，msg 原样带出。
                          最常见的是"token 失效，请检查登录状态"——
                          说明比特浏览器客户端没登录，需要在客户端里重新登录。
    """
    browsers = []
    page = 0
    while True:
        res = getBrowserList(page=page, pageSize=100, **filters)
        if not res.get("success"):
            raise RuntimeError(res.get("msg") or "获取窗口列表失败")

        data = res.get("data") or {}
        rows = data.get("list") or []
        for b in rows:
            browsers.append(
                {
                    "id": b.get("id"),
                    "seq": b.get("seq"),
                    "name": b.get("name") or "",
                    "remark": b.get("remark") or "",
                }
            )

        total = data.get("totalNum")
        # 没有 totalNum 时退化成"取到空页就停"，避免死循环
        if not rows or (total is not None and len(browsers) >= total):
            break
        page += 1

    return browsers


def getOpenedBrowserIds():
    """
    返回当前**已经打开**的窗口ID集合。

    用来避免无谓地拉起窗口：查登录状态时，已开的窗口可以直接连上去查，
    没开的窗口就得先拉起来才读得到登录态（cookie 在窗口自己的 profile 里，
    浏览器不启动就读不出来）。

    接口会自动过滤掉已经死掉的进程，比自己记状态可靠。
    """
    try:
        res = requests.post(
            f"{url}/browser/pids/all", data=json.dumps({}), headers=headers, timeout=10
        ).json()
    except Exception:
        return set()
    if not res.get("success"):
        return set()
    return set((res.get("data") or {}).keys())


def getBrowserDetail(id):
    """取单个窗口的完整配置，用来核对 syncTabs / syncCookies 这类开关。"""
    return _post("/browser/detail", {"id": f"{id}"})


def setSyncTabs(ids, enabled=False):
    """
    批量开关窗口的「同步标签页」。

    为什么默认要关掉：syncTabs 开着时，比特浏览器在关闭窗口的瞬间会把当时开着的
    所有标签页地址记进窗口配置，下次打开原样恢复。自动化每跑一轮都会留下页面状态，
    于是标签页越积越多——实测一个窗口重开后直接恢复出 4 个页面，
    而且 `pages[0]` 拿到的是历史遗留页而不是干净起点。

    ticket_login.collapse_to_single_page() 是在登录时把多余标签页关掉，治标；
    把 syncTabs 关掉才是从源头上不让它们产生。

    ⚠️ 这**不影响抢票时的多标签**：syncTabs 只管「打开窗口时是否恢复上次的标签页」，
    运行期间该开几个标签页照样开几个。

    :param ids: 窗口ID列表
    :return: 接口原始返回
    """
    ids = [i for i in (ids or []) if i]
    if not ids:
        return {"success": True, "data": None}
    return _post("/browser/update/partial", {"ids": ids, "syncTabs": bool(enabled)})


def openBrowser(id):  # 直接指定ID打开窗口，也可以使用 createBrowser 方法返回的ID
    # queue=True 是官方文档明确建议的：「是否以队列方式打开，设置为 true 后，
    # 可有效防止多线程同时启动时导致的并发报错」。
    # 一键登录/并发抢票会同时拉起多个窗口，正是这个参数的适用场景。
    return _post("/browser/open", {"id": f"{id}", "queue": True}, timeout=60)


def closeBrowser(id):  # 关闭窗口
    return _post("/browser/close", {"id": f"{id}"})


def clearLoginState(browser_id, wait_after_close=4.0):
    """
    彻底清掉一个窗口的站点登录态。**窗口会被关闭，调用方需要自己重新打开。**

    ⚠️ 必须先关窗口，这是实测出来的（2026-08-17）：

        窗口开着时调这两个接口，**都返回 success: True，但什么都不会清**。
        实测往 localStorage 写一个标记，不关窗直接清 cache + cookie，两个接口
        都报成功，reload 之后标记原样还在。接口在这件事上是会骗人的，
        不能靠返回值判断清没清干净——只能靠"先关窗"这个前提。
        （合理：数据在浏览器进程手里，进程还活着就轮不到外部去删。）

    为什么 cookie 和 cache 两个都要清（实测依据）：

        cookie 里是会话（MZCONSUMERJSESSIONID 等），清掉后 /api/user/loginUser
        立刻判定未登录——**但这不等于干净**。站点在 localStorage 里存了一份
        `ACCOUNT_INFO`，字段包括 email、phone、userToken、isLogin，
        也就是上一个人的身份和令牌。

        实测只清 cookie 时，站点前端在下次加载时会自己把 ACCOUNT_INFO 清空
        （它发现会话没了，走了登出逻辑）。但那是**依赖站点前端行为**的副作用，
        站点改版就可能不成立，而这里出错的后果是拿 A 的账号抢 B 的票。
        所以不赌前端，自己动手清：/cache/clear 实测能抹掉整个 localStorage。

    两个接口的参数形状不一样，很容易写错（写错时报「请传入 browserId」）：
        /browser/cookies/clear   {"browserId": "<id>"}   单数、字符串
        /cache/clear             {"ids": ["<id>"]}       复数、数组

    :return: {"closed": bool, "cookies": bool, "cache": bool}
    """
    return clearLoginStateBatch([browser_id], wait_after_close)[browser_id]


def clearLoginStateBatch(browser_ids, wait_after_close=4.0):
    """
    批量清除多个窗口的登录态。语义同 clearLoginState，但**只等一次**。

    为什么要有批量版：关窗之后必须等进程真正退出才能清（原因见 clearLoginState），
    而这个等待是墙钟时间，跟窗口数量无关——10 个窗口一个个各等 4 秒是 40 秒，
    先把 10 个窗口全关掉、统一等 4 秒、再逐个清，总共还是 4 秒。
    一键登录时这就是几十秒的差别。

    :return: {browser_id: {"closed":..., "cookies":..., "cache":...}}
    """
    ids = [f"{i}" for i in (browser_ids or []) if i]
    if not ids:
        return {}

    result = {i: {"closed": False, "cookies": False, "cache": False} for i in ids}

    for bid in ids:
        try:
            result[bid]["closed"] = bool(closeBrowser(bid).get("success"))
        except Exception:
            pass  # 窗口本来就没开时关闭会失败，不影响后面清

    # 统一等一次：关闭是异步的，接口返回时进程未必已经退出，
    # 没退干净就清会退化成「返回成功但其实没清」。
    time.sleep(wait_after_close)

    # /cache/clear 本身支持数组，一次调用清全部
    try:
        cache_ok = bool(_post("/cache/clear", {"ids": ids}).get("success"))
    except Exception:
        cache_ok = False

    for bid in ids:
        result[bid]["cache"] = cache_ok
        # cookie 接口只收单个 browserId，只能逐个调——但它是纯网络往返，不带等待，
        # 10 个也就几十毫秒，不是瓶颈。
        try:
            result[bid]["cookies"] = bool(
                _post("/browser/cookies/clear", {"browserId": bid}).get("success")
            )
        except Exception:
            pass

    return result


def deleteBrowser(id):  # 删除窗口
    json_data = {"id": f"{id}"}
    print(
        requests.post(
            f"{url}/browser/delete", data=json.dumps(json_data), headers=headers
        ).json()
    )


if __name__ == "__main__":
    browser_id = createBrowser()
    openBrowser(browser_id)

    time.sleep(10)  # 等待10秒自动关闭窗口

    closeBrowser(browser_id)

    time.sleep(10)  # 等待10秒自动删掉窗口

    deleteBrowser(browser_id)
