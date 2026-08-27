"""
第一步：解析活动的票务信息并留存。

实现方式：**直接 HTTP 请求活动数据接口**，完全不开浏览器。

    GET https://rest-sig.imaitix.com/api/pro/project?projectToken=<projectId>&reqType=1&langType=1

返回体里 `data.eventVoList` 就包含了全部场次和每个场次的票档（含价格和售罄标记），
一次拿全。

⚠️ 关于被封 IP（2026-08-13 踩坑记录，很重要）：

    这个接口不需要登录，但**滥用会导致整个出口 IP 被站点拉黑**：被封之后该 IP
    连正常浏览都不行，站点所有域名一律 ERR_CONNECTION_CLOSED（同一时刻访问百度
    正常，所以能确认是站点针对性封禁而非网络故障），持续时间很长。

    致命的不是请求次数，而是**请求模式**：正常用户打开活动页会连带几十个资源请求，
    接口调用夹在其中，两次浏览之间还隔着人的思考时间；而"纯 API 连打"——不加载
    页面、不加载资源、没有埋点，光秃秃的接口请求一次接一次——在风控眼里是教科书
    式的爬虫特征。实测二三十次这样的请求就足以中招。

    防护措施（都已落实在代码里）：
        1. ticket_api.MIN_API_INTERVAL 全局节流，同一窗口的接口调用强制拉开间隔，
           调用方就算写了循环也快不起来（本模块的直连路径同样受 _throttle_direct 约束）
        2. 解析全过程**只发一个请求**，不做任何试探性调用
        3. 抢票循环默认间隔 2~4 秒，并带限流识别 + 指数退避

    曾经做过"优先走浏览器窗口取数"的版本，理由是担心直连被限流——**那个理由是错的**：
    当时的对比里"服务器"和"窗口"根本不是同一个出口 IP，结论不成立。
    两条路径都从本机出去，走窗口挡不住限流，还平白弹出一个浏览器窗口。
    解析是只读操作，不该有这种副作用，所以只保留直连。

关于鉴权（2026-08-13 实测结论）：
    浏览器发这个请求时带了一堆头：hecate、epeius、_r、cna、coeus、mzoperateid、
    x-xsrf-token、project-id、site……其中 `hecate` 每次请求都不一样，看着很像动态签名。
    但实测**服务端根本不校验它**：删掉 hecate、把它改成 32 个 0、拿 80 秒前的旧值重放、
    甚至拿 A 活动的签名去请求 B 活动，全都照样返回完整数据。
    它是前端埋点/风控采集字段，不是鉴权凭据。

    用贪心法逐个删头做最小化，**最终必需的只有两个**：
        referer: https://hkt.hkticketing.com/     （缺了报「非法请求！未取到域名信息」）
        site: m                                   （缺了返回 data: null，最容易漏掉的就是它）
    不需要 cookie，不需要 User-Agent，不需要登录。

因此解析这一步是纯 HTTP、毫秒级、无状态的。相比早先"点立即购买进选票页再逐个点场次"：
    - **不需要登录**：选票页要登录，这个接口不用。
    - **不碰购买按钮**：有风控时点「立即购买」可能直接进不去，也就看不到票务信息。
    - **不占用浏览器窗口**：窗口可以留着干别的，也不受窗口状态影响。
    - **数据更全**：售罄标记、限购数量（eventOrderLimit）这些 UI 上未必显示的字段也有。
"""
import re
import threading
import time

import requests

from . import ticket_api

PROJECT_API = "https://rest-sig.imaitix.com/api/pro/project"

# 实测出来的最小必要请求头，少一个都拿不到数据
API_HEADERS = {
    "referer": "https://hkt.hkticketing.com/",
    "site": "m",
}


def _parse_input(project_id_or_url: str):
    """
    接受完整详情页链接或纯 projectId，返回 (projectId, 详情页URL)。
    """
    s = (project_id_or_url or "").strip()
    if not s:
        raise ValueError("请填写活动详情页链接或 projectId")

    if s.isdigit():
        return s, f"https://hkt.hkticketing.com/#/allEvents/detail?projectId={s}"

    m = re.search(r"(?:projectId|activityId)=(\d+)", s)
    if m:
        return m.group(1), s

    raise ValueError("活动链接里没找到 projectId，请确认链接是否完整")


_last_direct_call = 0.0
_direct_lock = threading.Lock()


def _throttle_direct():
    """服务器直连路径的节流，语义同 ticket_api._throttle，只是这条是同步的。"""
    global _last_direct_call
    with _direct_lock:
        wait = ticket_api.MIN_API_INTERVAL - (time.monotonic() - _last_direct_call)
        if wait > 0:
            time.sleep(wait)
        _last_direct_call = time.monotonic()


def fetch_project_data(project_id: str) -> dict:
    """
    拉取活动原始数据。纯 HTTP，无需登录、无需浏览器。

    :raises RuntimeError: 接口没返回数据时抛出，并区分常见原因
    """
    # 直连路径同样节流。这条路走的是本机 IP，被封的话影响的是整台机器上网，
    # 比窗口被封更麻烦，所以宁可慢。
    _throttle_direct()

    resp = requests.get(
        PROJECT_API,
        params={"projectToken": project_id, "reqType": 1, "langType": 1},
        headers=API_HEADERS,
        timeout=15,
    )
    payload = resp.json()

    data = payload.get("data")
    if not data:
        msg = payload.get("msg") or ""
        if "域名" in msg:
            # 兜底提示：真出现说明站点改了校验规则
            raise RuntimeError(f"接口拒绝了请求（{msg}），可能站点改了域名校验规则")
        raise RuntimeError(
            f"接口没返回活动数据（projectId={project_id}）。"
            "请确认这个 ID 是否正确、活动是否已下架。"
        )
    return data


def _extract_event(data: dict, event_url: str) -> dict:
    """
    把接口返回的 data 转成我们自己的结构。

    接口字段对应关系：
        data.projectName            活动名
        data.eventVoList[]          场次列表
            .eventCaption           场次显示名（就是页面上那一行文字）
            .eventToken             场次ID
            .saleState              场次售卖状态：2=在售，3=售罄/停售
            .sellOut                见下方警告，**不要单独用它判断**
            .eventOrderLimit        单笔限购张数
            .priceVoList[]          该场次的票档
                .priceName          票档名（含 A/B/C 字母编号，页面富文本里没有）
                .price              价格（字符串）
                .canAddCart         **能否购买——这才是可买性的真实标志**
                .sellOut            见下方警告
                .priceId            票档ID

    ⚠️ 关于售罄判断（用三个活动对照实测得出，别再改错了）：

    判断规则：
        场次在售 = saleState == 2        （3 = 售罄/停售）
        票档可买 = 场次在售 且 sellOut != True

    三个实测样本：

        活动                  saleState   票档 sellOut   canAddCart   实际
        SSwagger O Day        2           false          true         有票
        汪苏泷 2026 香港站     3           混合           false        全部售罄
        EUNHYUK FANCON        2           false          false        有票

    两个容易踩的坑：

    1. **`canAddCart` 不是"能否购买"**，它是"能否加入购物车"。EUNHYUK 这个活动
       页面上票档明明可选、「下一步」按钮也是亮的，但 canAddCart 全是 false——
       它只是不支持加购物车而已。曾经用它当可买性标志，导致有票的活动被误报成
       全部售罄。**不要再用这个字段判断可买性。**

    2. **票档级 `sellOut` 单独用也不够**。汪苏泷全场售罄时，18 个票档里有 5 个
       `sellOut=false`，但整场 `saleState=3`，实际一张都买不了。所以必须先看
       场次的 saleState，它是 3 的话票档 sellOut 说什么都不算数。

    另外 `marginCount` / `goodCount` 恒为 0，站点不暴露余票数，别指望用它算剩几张。
    """
    sessions = []
    for ev in data.get("eventVoList") or []:
        # 接口的票档列表和选票页显示的列表**不完全一致**，需要一点甄别。
        #
        # 2026-08-26 上午（会员预售配置好、尚未开卖）接口返回 12 个：
        #     6 个 isPermission=False、childPriceIds=None       → 页面上显示
        #     6 个 isPermission=True、childPriceIds=[对应的普通档] → 页面上不显示
        #   会员档是"父"，childPriceIds 指向普通档。
        #
        # 同日晚上（会员预售进行中）接口只剩 6 个：
        #     就是上午那 6 个普通档的 priceId，但 isPermission 全变成 True，
        #     childPriceIds 全是 None，eventOrderLimit 从 2 变成 4。
        #
        # 教训：**isPermission 不能用来判断"页面上显不显示"**。
        # 早先写成「只保留 isPermission != True 的」，是拿上午一个快照推的规则，
        # 晚上数据一变就把 6 个票档全过滤光了，界面上显示成"全部售罄"——
        # 明明有票却报没票，比报错更糟糕。
        #
        # 现在只过滤**确定是包装层**的那种：带 childPriceIds 的是父项，
        # 它在页面上不单独出现（页面显示子项，数量区才显示父项名字）。
        # 这条规则对上下两份快照都成立。
        wrapper_ids = {
            str(pv.get("priceId"))
            for pv in ev.get("priceVoList") or []
            if pv.get("childPriceIds")
        }

        # 场次是否在售。saleState==3 表示整场停售/售罄，此时票档说什么都不算数。
        sale_state = ev.get("saleState")
        session_on_sale = sale_state == 2 and not ev.get("sellOut", False)

        tiers = []
        for pv in ev.get("priceVoList") or []:
            price = pv.get("price")
            # 同样 strip，理由见下方场次那里
            name = (pv.get("priceName") or "").strip()
            # 页面上票档显示成「1日通行券 (HK$ 80.00)」，这里拼成同样的文本，
            # 让抢票引擎按文本匹配时能直接对上页面元素
            try:
                display = f"{name} (HK$ {float(price):.2f})" if price is not None else name
            except (TypeError, ValueError):
                display = name
            tiers.append(
                {
                    "text": display,
                    "name": name,
                    "price": float(price) if price not in (None, "") else None,
                    "price_id": pv.get("priceId"),
                    # 场次在售 + 该票档没售罄 才算可买。
                    # 不要用 canAddCart，那是购物车功能标志，跟可买性无关（见上方说明）
                    "available": session_on_sale and not pv.get("sellOut", False),
                    # 是不是"包装层"票档（带 childPriceIds 的父项），
                    # 这种在页面票档列表里不单独出现
                    "is_wrapper": str(pv.get("priceId")) in wrapper_ids,
                    # 选中它点「下一步」会不会要求会员优先购票码。
                    # isPermission 就是这个意思——两份快照都对得上：
                    # 上午是会员档为 True，晚上预售进行中时普通档也变成 True。
                    "need_member_code": bool(pv.get("isPermission")),
                    # 原始字段留着，出现判断分歧时方便核对
                    "raw_sell_out": bool(pv.get("sellOut", False)),
                    "raw_can_add_cart": bool(pv.get("canAddCart")),
                }
            )

        # 过滤掉包装层票档。
        #
        # **但绝不允许过滤到一个不剩。** 这是上一版最惨的教训：过滤规则一旦
        # 跟站点新的数据形态对不上，就会把所有票档抹掉，界面显示"全部售罄"——
        # 明明有票却报没票，用户完全无从判断是真没票还是程序坏了。
        # 关于页面结构的启发式规则本来就可能过时，所以它只该做"锦上添花"，
        # 不该有能力把一个健康的活动变成空的。宁可多显示几个，也不能一个不剩。
        kept = [t for t in tiers if not t["is_wrapper"]]
        if kept:
            tiers = kept
        elif tiers:
            print(
                f"[解析] 注意：{(ev.get('eventCaption') or '').strip()} 的票档"
                f"按规则会被全部过滤掉，已改为全部保留（站点数据形态可能变了）"
            )

        sessions.append(
            {
                # 必须 strip：接口返回的 eventCaption 有的带尾随空格
                # （实测「2026年9月4日 (星期五) 晚上7时  」末尾两个空格），
                # 而页面元素上没有。抢票时是拿这个文本去 has_text 匹配页面元素的，
                # 带空格会匹配不上，报成"未找到匹配场次"。
                "text": (ev.get("eventCaption") or "").strip(),
                "event_token": ev.get("eventToken"),
                # 场次售罄 = 不在售。saleState 2=在售，3=售罄/停售
                "sell_out": not session_on_sale,
                "sale_state": sale_state,
                "order_limit": ev.get("eventOrderLimit"),
                "tiers": tiers,
            }
        )

    return {
        "name": data.get("projectName") or "",
        "project_id": str(data.get("projectToken") or ""),
        "event_url": event_url,
        "min_price": data.get("minPrice"),
        "max_price": data.get("maxPrice"),
        "sessions": sessions,
    }


def parse_event(event_url: str) -> dict:
    """
    解析活动的全部场次和票档。**纯 HTTP，不开浏览器窗口、不需要登录。**

    曾经做过"优先走浏览器窗口取数、直连兜底"的版本，理由是担心服务器直连被限流。
    那个理由是错的：当时的对比里，"服务器"和"窗口"根本不是同一个出口 IP，
    结论不成立。实际上两条路径都从本机出去，走窗口既挡不住限流，
    还平白弹出一个浏览器窗口——解析这种只读操作不该有这种副作用。

    现在只保留直连：一个请求、几百毫秒、无副作用。防限流靠 _throttle_direct()。

    :param event_url: 详情页完整链接，或纯 projectId
    """
    project_id, url = _parse_input(event_url)
    print(f"[解析] 请求活动数据接口（projectId={project_id}，无需登录）...")
    data = fetch_project_data(project_id)

    event = _extract_event(data, url)

    total = sum(len(s["tiers"]) for s in event["sessions"])
    avail = sum(1 for s in event["sessions"] for t in s["tiers"] if t["available"])
    print(f"[解析] {event['name']}：{len(event['sessions'])} 个场次 / {total} 个票档（{avail} 个可售）")
    for s in event["sessions"]:
        mark = "（整场售罄）" if s["sell_out"] else ""
        print(f"[解析]   {s['text']}{mark}")
        for t in s["tiers"]:
            tag = "" if t["available"] else " ← 售罄"
            if t.get("need_member_code"):
                tag += " ← 需会员优先购票码"
            print(f"[解析]     · {t['text']}{tag}")

    return event
