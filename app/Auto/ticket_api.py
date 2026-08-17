"""
在**浏览器页面上下文里**调用站点的**只读**接口。

=== 这个模块的边界（重要，别越线）===

**只用于只读查询，绝不用来替代下单流程里的任何一步。**

下单链路（选场次 → 选票档 → 调数量 → 提交 → 勾选条款 → 确认订单）是有状态、
有顺序依赖的，必须**全程走 UI 自动化**，原因有三：
    1. 页面状态会脱节：用接口直接建单，页面还停在选票页，它不知道单已经建了，
       后续任何依赖页面状态的步骤都会错乱；
    2. 后端很可能要求前面的步骤按序调过，有些参数（token/nonce）是上一步接口
       返回的，跳步拿不到；
    3. 风控看的是行为轨迹。没有正常的浏览点击过程、凭空冒出一个下单请求，
       比慢一点更容易被拦。

本模块里的接口调用都满足：**不改变任何服务端状态**，调不调都不影响流程正确性，
只是让"要不要动手"这个判断更快更准。查完之后照样老老实实点完整的 UI 流程。

=== 为什么不从 Flask 进程直连 ===

比特浏览器每个窗口有自己的出口 IP 和指纹，账号登录态（cookie）也在窗口里。
从服务器直连的话 IP 和指纹全对不上，等于把指纹浏览器这层保护绕过去了。
所以这里用 page.evaluate 在页面里发 fetch：
    - 请求从该窗口的出口 IP 发出
    - 自动带上该窗口的 cookie
    - TLS 指纹、UA 都是这个窗口真实的

不需要登录、也不关联任何账号的接口（活动详情 /api/pro/project）例外，
那个放在 ticket_parser.py 里用 requests 直连，省掉开窗口的开销。

=== 鉴权 ===

服务端只认 `referer` 和 `site: m` 两个头，`hecate` 之类的字段实测不校验
（详见 ticket_parser 模块文档）。页面上下文里 referer 是自动带的，只需补 `site`。
"""
import asyncio
import json
import time

API_BASE = "https://rest-sig.imaitix.com"

# ------------------------------------------------------------------
# 全局请求节流
# ------------------------------------------------------------------
# 2026-08-13 的教训：为了验证接口字段，短时间内做了二三十次**纯 API 连打**
# （不加载页面、不加载资源、没有埋点，光秃秃的接口请求），结果整个出口 IP 被
# 站点拉黑，连正常浏览都 ERR_CONNECTION_CLOSED，持续了很久。
#
# 致命的不是"次数多"，而是这种请求模式：正常用户打开活动页会连带几十个资源
# 请求，接口调用夹在其中，两次浏览之间还隔着人的思考时间；纯接口连打在风控
# 眼里是教科书式的爬虫特征。
#
# 所以这里从机制上兜底：同一个窗口对站点的接口调用强制拉开间隔，
# 就算调用方写了循环也快不起来。
MIN_API_INTERVAL = 3.0

_last_call = {}          # {key: 上次请求的时间戳}
_key_locks = {}          # {key: 该 key 专用的锁}


async def _throttle(key: str):
    """
    确保**同一个** key（窗口ID）的两次接口调用间隔不小于 MIN_API_INTERVAL。

    锁必须按 key 分开：节流针对的是单个窗口（单条出口 IP）的请求密度，
    不同窗口之间毫无关系。用一把全局锁的话，10 个账号并发登录会被强行串成
    一条队列，每个等 3 秒——光节流就要 30 秒，把并发的意义全抵消了。

    `setdefault` 在这里是安全的：asyncio 单线程事件循环里，
    两个 await 之间的代码不会被打断。
    """
    lock = _key_locks.setdefault(key, asyncio.Lock())
    async with lock:
        now = time.monotonic()
        last = _last_call.get(key, 0.0)
        wait = MIN_API_INTERVAL - (now - last)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call[key] = time.monotonic()

# 在页面里执行的取数脚本。用站点自己的 fetch，credentials 跟随页面（带 cookie）。
_FETCH_JS = """
async ({ url }) => {
  try {
    const r = await fetch(url, {
      method: 'GET',
      credentials: 'include',
      headers: { 'site': 'm' },
    });
    return { ok: true, status: r.status, text: await r.text() };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}
"""


async def call_api(page, path: str, params: dict = None, throttle_key: str = None):
    """
    在页面上下文里调一个站点的**只读** GET 接口，返回解析后的 JSON。

    刻意只支持 GET：这个模块不做任何写操作，写操作一律走 UI 自动化。
    需要下单时不要来改这个函数，去 ticket_operation 里走点击流程。

    :param page: 已连到目标窗口的 Playwright Page
    :param path: 接口路径，如 "/api/pro/event"
    :param params: query 参数
    :raises RuntimeError: 网络失败或返回不是合法 JSON 时抛出
    """
    url = API_BASE + path
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        url = f"{url}?{qs}"

    # 节流兜底，防止调用方无意间打出连发（见模块顶部说明）。
    # 默认按 page 对象分组：一个 page 就是一个窗口、一条出口 IP，
    # 正好是节流该管的粒度。用固定字符串当 key 会把所有窗口串成一队。
    await _throttle(throttle_key or f"page-{id(page)}")

    res = await page.evaluate(_FETCH_JS, {"url": url})

    if not res.get("ok"):
        raise RuntimeError(f"请求 {path} 失败：{res.get('error')}")
    try:
        return json.loads(res["text"])
    except (json.JSONDecodeError, TypeError):
        raise RuntimeError(f"接口 {path} 返回的不是 JSON：{str(res.get('text'))[:120]}")


async def is_logged_in(page) -> bool:
    """
    用 `/api/user/loginUser` 判断该窗口是否登录。

    比看 URL 里有没有 `#/login` 可靠得多：页面可能停在任意路由上
    （首页、票夹、订单页），光看路由只能证明"不在登录页"，
    证明不了登录态还有效——cookie 过期时页面照样停在原地。
    """
    try:
        payload = await call_api(page, "/api/user/loginUser", {"langType": 1})
    except Exception:
        return False
    return bool(payload.get("data"))


async def fetch_event_stock(page, event_token: str) -> dict:
    """
    查某个场次的实时库存（需要登录态）。

    返回 {price_name: {"price_id":..., "sell_out": bool, "margin": 剩余数量或None}}
    抢票前先查一下，没票就不用白跑一趟 UI 流程了。
    """
    payload = await call_api(
        page, "/api/pro/event", {"eventToken": event_token, "langType": 1}
    )
    data = payload.get("data")
    if not data:
        raise RuntimeError(payload.get("msg") or "查询场次库存失败（多半是登录态失效）")

    out = {}
    for pv in data.get("priceVoList") or []:
        out[pv.get("priceName") or ""] = {
            "price_id": pv.get("priceId"),
            "sell_out": bool(pv.get("sellOut", False)),
            # marginCount 是余票数，站点不一定每个活动都返回
            "margin": pv.get("marginCount"),
        }
    return out
