"""
一键登录：把每个抢票人的账号密码填进各自的比特浏览器窗口并点击登录。

登录页结构（2026-08-13 实测）：
    路由 `#/login`，会自动跳到 `#/login/account`
    邮箱：`input[name="userEmail"]`（placeholder「请输入电子邮箱」）
    密码：`input[name="password"]`
    登录按钮：`button.register-button`（文字是「登录」，class 名叫 register 是站点自己的历史包袱）
    顶部有「电子邮件 / 手机号码」切换 tab，默认就是电子邮件，不用切

**验证码由人工处理。** 本模块只负责填账号密码 + 点登录，之后就把窗口留在前台，
等人在窗口里把验证码点掉。登录成功与否通过轮询页面状态判断（跳离登录路由即视为成功）。
"""
import asyncio
from typing import Optional

from playwright.async_api import Playwright, Page

from . import ticket_api
from . import ticket_store as store
from .bit_api import (
    openBrowser,
    getOpenedBrowserIds,
    setSyncTabs,
    clearLoginStateBatch,
)
from ..utils.logger import task_state_manager

LOGIN_URL = "https://hkt.hkticketing.com/#/login"

EMAIL_INPUT = 'input[name="userEmail"]'
PASSWORD_INPUT = 'input[name="password"]'
LOGIN_BUTTON = "button.register-button"


async def _is_logged_in(page: Page) -> bool:
    """
    判断当前是否已登录。

    先用 `/api/user/loginUser` 接口精确判断——光看 URL 里有没有 `#/login`
    是不够的：那只能证明"不在登录页"，证明不了 cookie 还有效，
    登录态过期时页面照样停在首页，会被误判成已登录。

    接口调不通时（比如页面还没落到站点域名下）退回看 URL，保证不会卡死。
    """
    try:
        return await ticket_api.is_logged_in(page)
    except Exception:
        return "#/login" not in page.url


async def login_on_page(
    page: Page,
    email: str,
    password: str,
    label: str,
    wait_captcha_seconds: int = 180,
    task_id: Optional[str] = None,
    assume_logged_out: bool = False,
) -> bool:
    """
    在给定页面上完成一个账号的登录。

    :param label: 日志前缀，用账号邮箱或备注，方便多窗口并发时区分来源
    :param wait_captcha_seconds: 点完登录后，留给人工处理验证码的最长等待时间
    :param assume_logged_out: 调用方已确知该窗口是登出状态（刚清过），
        跳过开头那次登录态探测。省下的是「等 2 秒 + 一次受 3 秒节流约束的接口调用」，
        每个账号都要付一次，人多的时候很显眼。
    :return: 是否登录成功
    """
    await page.goto(LOGIN_URL, wait_until="domcontentloaded")

    # 已经是登录态的话会被弹回首页，那就没什么可做的了。
    # 刚清过的窗口不用查——查了必然是"未登录"，白等。
    if not assume_logged_out:
        await asyncio.sleep(2)
        if await _is_logged_in(page):
            print(f"[{label}] 已是登录状态，跳过登录")
            return True

    try:
        await page.wait_for_selector(EMAIL_INPUT, timeout=10000)
    except Exception:
        print(f"[{label}] 登录表单没加载出来，请检查窗口是否正常打开了登录页")
        return False

    # 填账号密码。用 fill 而不是逐字符 type：fill 会触发 Vue 的 input 事件，
    # 且不会因为页面上有输入防抖导致漏字符。
    await page.fill(EMAIL_INPUT, email)
    await asyncio.sleep(0.2)
    await page.fill(PASSWORD_INPUT, password)
    await asyncio.sleep(0.2)

    login_btn = page.locator(LOGIN_BUTTON).first
    if await login_btn.count() == 0:
        print(f"[{label}] 找不到登录按钮")
        return False

    await login_btn.click()
    print(f"[{label}] 已提交登录，若弹出验证码请在该窗口中手动完成")

    # 轮询等待登录完成。验证码要人点，所以这里给一个较长的宽限期，
    # 每秒查一次，中途支持被任务状态机叫停。
    waited = 0
    while waited < wait_captcha_seconds:
        if task_id and task_state_manager.get_state(task_id) == "stopped":
            print(f"[{label}] 任务已停止，放弃等待登录")
            return False
        await asyncio.sleep(1)
        waited += 1
        if await _is_logged_in(page):
            print(f"[{label}] 登录成功")
            return True
        if waited % 15 == 0:
            print(f"[{label}] 仍在等待登录完成（已等 {waited}s，多半是在等验证码）")

    print(f"[{label}] 等待 {wait_captcha_seconds}s 仍未登录成功，请检查该窗口")
    return False


async def open_and_login(
    playwright: Playwright,
    account: dict,
    wait_captcha_seconds: int = 180,
    task_id: Optional[str] = None,
) -> bool:
    """
    「一键启动」：打开该账号绑定的比特浏览器窗口，直接落到登录页。

    这是打开窗口后的第一件事——窗口刚起来时停在比特浏览器工作台页，
    先把它导到登录页登录好，登录态建立之后才谈得上解析和抢票。
    """
    return await login_single(playwright, account, wait_captcha_seconds, task_id)


async def collapse_to_single_page(browser, label: str):
    """
    把窗口收敛成**一个**干净标签页，返回它。

    为什么需要这一步：比特浏览器的窗口默认开着 `syncTabs`，关闭窗口时会把当时
    开着的所有标签页记进窗口配置的 `url` 字段，下次打开原样恢复。自动化每跑一轮
    都会在窗口里留下页面状态（登录页、首页、选座页…），于是标签页越积越多——
    实测一个窗口重开后直接恢复出 4 个页面。

    更麻烦的是 `context.pages[0]` 拿到的往往是某个历史遗留页
    （比如带 `loginBefore=...` 参数的旧登录页），而不是干净的起点。

    登录阶段只需要一个页面，所以这里主动收敛：留第一个、关掉其余。
    抢票阶段不做这件事——那时多标签是有用的。
    """
    pages = [p for ctx in browser.contexts for p in ctx.pages]
    if not pages:
        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
        return await ctx.new_page()

    keep, extra = pages[0], pages[1:]
    for p in extra:
        try:
            await p.close()
        except Exception:
            pass  # 关不掉就算了，不该为这个中断登录
    if extra:
        print(f"[{label}] 已关闭 {len(extra)} 个遗留标签页，只保留一个")
    return keep


async def purge_windows(browser_ids: list, reason: str = "") -> dict:
    """
    批量清掉窗口的登录态（关窗 + 清 cookie + 清 localStorage），并抹掉归属记录。

    **必须在开窗之前一次性做完。** 这是一键登录快慢的关键：
    清除要求窗口处于关闭状态，而关窗后要等进程真正退出才能清（几秒的墙钟等待）。
    如果放在"每个账号各自登录"的流程里做，就变成
    「开窗 → 发现要清 → 关窗 → 等 4 秒 → 清 → 再开窗」——
    每个窗口开两次，每个窗口各等一次，10 个人就是几十秒起步。
    提前批量清完，窗口只需要开一次，那几秒等待也只付一次。
    """
    ids = [b for b in browser_ids if b]
    if not ids:
        return {}
    print(f"正在清除 {len(ids)} 个窗口的登录态{reason}（关闭窗口 → 清 Cookie → 清本地存储）...")
    res = await asyncio.to_thread(clearLoginStateBatch, ids)
    for bid in ids:
        store.clear_window_owner(bid)
    bad = [b for b, r in res.items() if not (r["cookies"] and r["cache"])]
    if bad:
        print(f"其中 {len(bad)} 个窗口清除可能不完整：{bad}")
    else:
        print(f"{len(ids)} 个窗口的登录态已清除")
    return res


async def purge_window(browser_id: str, label: str = "") -> bool:
    """单个窗口版，供手动「清除登录态」按钮使用。"""
    res = await purge_windows([browser_id], f"（{label}）" if label else "")
    r = res.get(browser_id) or {}
    return bool(r.get("cookies") and r.get("cache"))


def _needs_purge(account: dict) -> bool:
    """
    这个账号的窗口在登录前要不要清。**只看归属记录，不连浏览器**，所以是零成本的。

    - 记录就是这个人 → 不清。他抢完这场接着抢下一场，清掉就要重过验证码，
      回流票那几十秒的窗口期直接废掉。
    - 记录是别人 → 清。10 人 5 窗口分两批时就是这种情况。不清的话
      _is_logged_in() 会看到上一个人的登录态、判定"已登录"从而跳过登录，
      然后拿着上一个人的账号去抢这个人配置的票——会真的下单成功，
      只是下到错的人头上，而且全程不报错。
    - 没有记录 → 清。程序每次启动都会清空归属表，所以"重启后所有人重新登录"
      就是这一条实现的。也顺带兜住了崩溃重启后窗口里残留上一轮登录态的情况。

      早先这里是「先开窗连上去看看到底登没登录，登了才清」，判断更精确，
      但代价是窗口要多开一次、还要多等一轮接口节流，是慢的主要来源。
      既然没有记录时本来就没有依据认为登录的是眼前这个人，直接清掉最省事。
    """
    owner = store.get_window_owner(account.get("browser_id"))
    if not owner:
        return True
    return owner.get("email") != (account.get("email") or "").strip()


async def login_single(
    playwright: Playwright,
    account: dict,
    wait_captcha_seconds: int = 180,
    task_id: Optional[str] = None,
    assume_logged_out: bool = False,
) -> bool:
    """
    打开该账号绑定的窗口并登录。

    :param assume_logged_out: 调用方（login_all）刚刚清过这个窗口，确定它是登出状态。
        传 True 可以省掉登录页那次"是不是已经登录了"的探测——那一次探测要
        goto + 等 2 秒 + 一个受 3 秒节流约束的接口调用，纯属白花时间。
        单独调用 login_single 时保持 False，该查还是要查。
    """
    label = account.get("remark") or account.get("email") or "?"
    browser_id = account.get("browser_id")
    if not browser_id:
        print(f"[{label}] 没有绑定比特浏览器窗口，跳过")
        return False

    email = (account.get("email") or "").strip()

    print(f"[{label}] 正在打开浏览器窗口...")
    # 必须 to_thread：openBrowser 内部是同步的 requests 调用，一次要几秒。
    # 直接在协程里调会把整个事件循环卡死，于是 login_all 的 asyncio.gather
    # 看着是并发、开窗这一步却完全串行——10 个账号就是一个个排队开，
    # 这是"一键登录特别慢"的主因。实测比特浏览器本身支持并发开窗
    # （2 个窗口并发 1.9s vs 串行 2.0s），瓶颈在我们这边。
    res = await asyncio.to_thread(openBrowser, browser_id)
    if not res.get("success"):
        print(f"[{label}] 打开窗口失败：{res.get('msg')}")
        return False
    browser = await playwright.chromium.connect_over_cdp(res["data"]["ws"])
    # 登录只需要一个页面，先把历史遗留的标签页清掉
    page = await collapse_to_single_page(browser, label)
    await page.bring_to_front()

    ok = await login_on_page(
        page,
        email,
        account.get("password", ""),
        label,
        wait_captcha_seconds=wait_captcha_seconds,
        task_id=task_id,
        assume_logged_out=assume_logged_out,
    )
    if ok:
        # 登录成功才记归属。失败时记了的话，下次会误以为窗口归他，跳过清除。
        store.set_window_owner(browser_id, email)
    return ok


async def check_login_status(
    playwright: Playwright,
    accounts: list,
    open_closed_windows: bool = False,
) -> list:
    """
    查每个抢票人的登录状态。

    尽量不拉起窗口：先问比特浏览器哪些窗口已经开着，开着的直接连上去查
    （几乎零成本）；没开的窗口读不到登录态——cookie 在窗口自己的 profile 里，
    浏览器不启动就拿不到——默认只标成 window_closed，不去打扰。

    :param open_closed_windows: 为 True 时把没开的窗口也拉起来查。
        默认 False：10 个人就意味着弹 10 个窗口，很吵，
        而且大多数时候你只想扫一眼谁在线。

    :return: [{account_id, label, status, detail}]
        status 取值：
            logged_in      已登录，一键登录会跳过他
            logged_out     窗口开着但没登录，需要登录
            window_closed  窗口没开，状态未知
            no_window      还没绑定窗口
            error          查询出错，detail 里有原因
    """
    opened = await asyncio.to_thread(getOpenedBrowserIds)
    out = []

    async def _check(acc):
        label = acc.get("remark") or acc.get("email") or "?"
        bid = acc.get("browser_id")
        item = {"account_id": acc.get("id"), "label": label,
                "email": acc.get("email"), "browser_id": bid}

        if not bid:
            return {**item, "status": "no_window", "detail": "未绑定浏览器窗口"}
        if bid not in opened and not open_closed_windows:
            return {**item, "status": "window_closed", "detail": "窗口未打开"}

        try:
            # 同上，同步的 requests 调用要放到线程里，否则并发查状态也会串行
            res = await asyncio.to_thread(openBrowser, bid)
            if not res.get("success"):
                return {**item, "status": "error", "detail": res.get("msg") or "打开窗口失败"}
            browser = await playwright.chromium.connect_over_cdp(res["data"]["ws"])
            ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()

            # 查登录态的接口是跨域 fetch，页面必须落在站点域名下才不会被 CORS 拦
            if "hkticketing.com" not in page.url:
                await page.goto("https://hkt.hkticketing.com/#/home",
                                wait_until="domcontentloaded")
                await asyncio.sleep(2)

            ok = await ticket_api.is_logged_in(page)
            return {**item,
                    "status": "logged_in" if ok else "logged_out",
                    "detail": "已登录" if ok else "未登录"}
        except Exception as e:
            return {**item, "status": "error", "detail": str(e)[:80]}

    out = await asyncio.gather(*[_check(a) for a in accounts])
    counts = {}
    for r in out:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print(f"登录状态检查完成：{counts}")
    return list(out)


async def login_all(
    playwright: Playwright,
    accounts: list,
    wait_captcha_seconds: int = 180,
    task_id: Optional[str] = None,
) -> dict:
    """
    并发给所有账号做登录，每个账号一个窗口。

    并发是有意的：验证码要人工点，串行的话得一个个等，
    并发时所有窗口会同时停在验证码上，人可以挨个点完。

    :return: {label: 是否成功}
    """
    if not accounts:
        raise ValueError("没有可登录的抢票人")

    # 兜底校验：一个窗口只能有一个抢票人。
    # 重复绑定时，多个并发任务会连到同一个浏览器、操作同一个页面，
    # 互相覆盖对方填的账号密码，而且另一个窗口完全没人动。
    # 存数据时已经拦过一道，这里再拦一次，防止历史遗留的坏数据直接跑起来。
    seen = {}
    for a in accounts:
        bid = a.get("browser_id")
        if not bid:
            continue
        label = a.get("remark") or a.get("email")
        if bid in seen:
            raise ValueError(
                f"「{seen[bid]}」和「{label}」绑定了同一个浏览器窗口，"
                "会导致两个账号的登录信息互相覆盖。请先给他们分配各自独立的窗口。"
            )
        seen[bid] = label

    # 从源头上关掉「同步标签页」：开着的话，窗口每次关闭都会记下当时的标签页，
    # 下次打开原样恢复，越积越多（collapse_to_single_page 只是每次登录时收拾残局）。
    # 只影响「打开窗口时恢复几个标签页」，抢票期间照样能开多标签。
    try:
        await asyncio.to_thread(setSyncTabs, list(seen.keys()), False)
    except Exception as e:
        # 关不掉不影响登录，登录时还会兜底收敛标签页
        print(f"[提示] 关闭窗口的同步标签页设置失败（不影响登录）：{e}")

    total = len(accounts)

    # 按归属记录把账号分成两拨。这一步不碰浏览器，零成本。
    #   need_purge —— 窗口要先清干净再登（换了人，或没有归属记录/程序刚重启）
    #   maybe_kept —— 归属记录就是他本人，登录态可能还有效，值得先查一下再决定
    need_purge, maybe_kept, no_window = [], [], []
    for a in accounts:
        if not a.get("browser_id"):
            no_window.append(a)
        elif _needs_purge(a):
            need_purge.append(a)
        else:
            maybe_kept.append(a)

    # 要清的一次性清完，**在任何窗口被打开之前**。
    # 这是这里最关键的顺序约束：清除要求窗口处于关闭状态，如果放到每个账号
    # 各自的登录流程里做，就变成「开窗 → 发现要清 → 关窗 → 等几秒 → 清 → 再开窗」，
    # 每个窗口开两次、每个窗口各等一次，10 个人就是几十秒。
    if need_purge:
        await purge_windows(
            [a["browser_id"] for a in need_purge], "（换人或程序重启后需重新登录）"
        )

    # 只对"可能还留着本人登录态"的那拨查状态。
    # 要清的那拨没必要查——刚清完必然是未登录，查一次还要多等一轮接口节流。
    results = {}
    already = set()
    if maybe_kept:
        status_list = await check_login_status(
            playwright, maybe_kept, open_closed_windows=False
        )
        for s in status_list:
            if s["status"] == "logged_in":
                already.add(s["account_id"])
                results[s["label"]] = True
        if already:
            skipped = [s["label"] for s in status_list if s["account_id"] in already]
            print(f"以下账号已是登录状态，跳过：{'、'.join(skipped)}")

    for a in no_window:
        label = a.get("remark") or a.get("email") or "?"
        print(f"[{label}] 没有绑定比特浏览器窗口，跳过")
        results[label] = False

    todo = [a for a in need_purge + maybe_kept if a.get("id") not in already]
    if not todo:
        print("所有账号都已登录，无需操作")
        return results

    print(f"开始一键登录，需要登录 {len(todo)} 个账号（共 {total} 个）")
    purged_ids = {a.get("id") for a in need_purge}

    async def _worker(account):
        label = account.get("remark") or account.get("email") or "?"
        try:
            results[label] = await login_single(
                playwright, account, wait_captcha_seconds, task_id,
                # 刚清过的窗口确定是登出状态，省掉登录页那次探测
                assume_logged_out=account.get("id") in purged_ids,
            )
        except Exception as e:
            print(f"[{label}] 登录异常：{e}")
            results[label] = False

    await asyncio.gather(*[_worker(a) for a in todo])

    ok = sum(1 for v in results.values() if v)
    print(f"一键登录结束：{ok} / {total} 个账号处于登录状态")
    return results
