"""
用**页面上的真实内容**核对接口解析结果。

为什么接口和页面都要有：

    接口（ticket_parser）  不用登录、不用开窗口、开票前就能拿到，
                          所以配置方案只能靠它。
    页面（这里）           是抢票时真正操作的对象，是地面真相，
                          但要登录、要开窗口、要点进购票流程。

两者的**接缝**是这个项目所有解析 bug 的发源地：配置用接口的文本，
抢票拿它去页面上找元素，两边对不上就"抢不到"，而且看起来像"没票"。

这个模块把接缝显式化：开抢前跑一次，逐条比对，不一致就直接列出来。
这样"接口能不能预测页面"不再取决于谁的推断更准，而是可以当场验证。

**全程只读**：进到选票页、读票档文本就停，不调数量、不点「下一步」、不下单。
"""
import asyncio
from typing import Optional

from playwright.async_api import Playwright

from . import ticket_api
from .bit_api import openBrowser
from .ticket_operation import (
    TIER_ITEM_SELECTOR,
    _accept_notice_modal,
    _normalize_text,
    _try_click,
    dismiss_cookie_banner,
    ensure_on_event_page,
    extract_project_id,
    is_on_login_page,
)

SESSION_SELECTOR = ".sessionListWrapper___gDIN1 .sessionList___al29_"
SOLD_OUT_CLASS = "disableClass___BDFqG"


async def read_page_tiers(page, session_text: str, label: str) -> dict:
    """
    在选票页上选中指定场次，读出页面**实际显示**的票档。

    :return: {"ok": bool, "detail": str, "tiers": [{"text","sold_out"}]}
    """
    try:
        await page.wait_for_selector(".sellTicketContent___PIY_T", timeout=8000)
    except Exception:
        # 先看是不是被弹回登录页了。选票页要登录，未登录时点「立即购买」会跳登录，
        # 只报"没进到选票页"或"票档没渲染"会让人去查选择器，而真实原因是没登录——
        # 报错指错方向比不报错还费时间。
        if await is_on_login_page(page):
            return {"ok": False,
                    "detail": "该窗口未登录（点购买后被弹到登录页），请先执行「一键启动并登录」",
                    "tiers": []}
        where = page.url.split("?")[0].split("#")[-1] or page.url
        return {"ok": False, "detail": f"没进到选票页（当前在 {where}）", "tiers": []}

    # 选场次。票档是选完场次才渲染的，不选就一个都读不到。
    texts = await page.eval_on_selector_all(
        SESSION_SELECTOR, "els => els.map(e => e.innerText)"
    )
    want = _normalize_text(session_text)
    idx = next((i for i, t in enumerate(texts) if want and want in _normalize_text(t)), -1)
    if idx < 0:
        return {
            "ok": False,
            "detail": f"页面上没有场次「{session_text}」",
            "page_sessions": [_normalize_text(t) for t in texts],
            "tiers": [],
        }
    await page.locator(SESSION_SELECTOR).nth(idx).click()

    try:
        await page.wait_for_selector(TIER_ITEM_SELECTOR, timeout=8000)
    except Exception:
        # 同上：点场次是登录态失效最先暴露的地方
        if await is_on_login_page(page):
            return {"ok": False,
                    "detail": "该窗口未登录（点场次后被弹到登录页），请先执行「一键启动并登录」",
                    "tiers": []}
        return {"ok": False, "detail": "选中场次后票档没渲染出来", "tiers": []}

    rows = await page.eval_on_selector_all(
        TIER_ITEM_SELECTOR,
        """els => els.map(e => ({
             text: e.innerText,
             soldOut: e.className.includes('%s'),
             hasPrivilegeIcon: !!e.querySelector('[class*=privilegeIcon]')
           }))""" % SOLD_OUT_CLASS,
    )
    return {
        "ok": True,
        "detail": f"读到 {len(rows)} 个票档",
        "tiers": [
            {
                "text": _normalize_text(r["text"]),
                "sold_out": bool(r["soldOut"]),
                # 页面上的会员图标才是"要不要会员码"的可靠信号：
                # 会员预售期每个票档后面都挂着它，公售后全部消失。
                # 接口字段试过两个都不可靠，页面这个是直接可见的事实。
                "member_icon": bool(r["hasPrivilegeIcon"]),
            }
            for r in rows
        ],
    }


def compare(api_tiers: list, page_tiers: list) -> dict:
    """
    把接口解析结果和页面实际内容逐条比对。

    比对用规范化后的**包含关系**，和抢票时的匹配逻辑保持一致——
    这里要回答的正是"抢票时能不能找到这个票档"，用别的比法就没意义了。
    """
    page_norm = [_normalize_text(t["text"]) for t in page_tiers]

    matched, missing = [], []
    for t in api_tiers:
        want = _normalize_text(t["text"])
        hit = next((i for i, p in enumerate(page_norm) if want and want in p), -1)
        if hit < 0:
            missing.append(t["text"])
        else:
            matched.append({
                "text": t["text"],
                "api_available": bool(t.get("available")),
                "page_sold_out": page_tiers[hit]["sold_out"],
                # 两边对售罄的判断不一致——这个比"找不到"更隐蔽：
                # 元素找得到，但状态判断反了，抢票会白跑或错过
                "state_mismatch": bool(t.get("available")) == page_tiers[hit]["sold_out"],
                "member_icon": page_tiers[hit]["member_icon"],
            })

    api_norm = [_normalize_text(t["text"]) for t in api_tiers]
    extra = [
        t["text"] for t in page_tiers
        if not any(a and a in _normalize_text(t["text"]) for a in api_norm)
    ]

    mismatches = [m for m in matched if m["state_mismatch"]]
    return {
        "api_count": len(api_tiers),
        "page_count": len(page_tiers),
        "matched": matched,
        "missing_on_page": missing,     # 接口有、页面没有 → 配了也抢不到
        "extra_on_page": extra,         # 页面有、接口没有 → 漏配了可抢的票
        "state_mismatches": mismatches,  # 售罄判断对不上
        "ok": not missing and not extra and not mismatches,
    }


async def verify_event(
    playwright: Playwright,
    browser_id: str,
    event: dict,
    session_text: Optional[str] = None,
    label: str = "核对",
) -> dict:
    """
    打开窗口、进选票页、把页面票档和解析结果比对。**只读，不下单。**

    :param session_text: 要核对的场次；不传则用第一个场次
    """
    sessions = event.get("sessions") or []
    if not sessions:
        return {"ok": False, "detail": "还没有解析过活动"}
    sess = next((s for s in sessions if s["text"] == session_text), sessions[0])

    res = await asyncio.to_thread(openBrowser, browser_id)
    if not res.get("success"):
        return {"ok": False, "detail": f"打开窗口失败：{res.get('msg')}"}

    browser = await playwright.chromium.connect_over_cdp(res["data"]["ws"])
    ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()

    event_url = event.get("event_url", "")
    project_id = extract_project_id(event_url)
    if not await ensure_on_event_page(page, event_url, project_id, label):
        return {"ok": False, "detail": "没能导航到活动页"}
    if await is_on_login_page(page):
        return {"ok": False, "detail": "该窗口未登录，无法进入选票页核对"}

    await dismiss_cookie_banner(page)
    await _accept_notice_modal(page, label)
    if await _try_click(page, ".buyNowBtn___YuGWG", timeout=2000):
        await asyncio.sleep(0.8)

    read = await read_page_tiers(page, sess["text"], label)
    if not read["ok"]:
        return {"ok": False, "detail": read["detail"],
                "page_sessions": read.get("page_sessions")}

    result = compare(sess["tiers"], read["tiers"])
    result["session_text"] = sess["text"]
    result["detail"] = read["detail"]
    return result
