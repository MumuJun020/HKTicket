"""
抢票自动化引擎（目标站点：hkt.hkticketing.com）。

设计约定：
    - 本模块只负责"控制比特浏览器窗口"，即：批量打开/连接 BitBrowser 指纹窗口、
      在窗口对应的 Playwright Page 上执行抢票动作、汇报任务状态与日志。
    - 浏览器窗口里页面本身的登录状态由使用者提前在 BitBrowser 中手动登录好，
      本模块不负责登录逻辑。

hkticketing 购票流程（2026-08-13 在真实站点上逐步验证过）：
    1. 活动详情页 .../allEvents/detail?projectId=xxx
       **一进页面就会自动弹出"购票须知"弹窗**，遮罩会挡住"立即购买"，
       所以必须先处理弹窗，再点购买按钮（顺序不能反）。
       弹窗结构：`.bui-modal.modalAndDrawer___MMXN3`
                 > `.contentWrapper` > `.bui-scroll.bui-scroll-view-scroll-y`（须知正文，可滚动）
                 > `.modalAndDrawerFooter .defaultBtnWrapper button.defaultOkBtn`（"知晓并同意"）
       **`.defaultOkBtn` 初始带 `bui-btn-disabled`，必须把须知正文滚到底才会解锁。**
       注意：它监听的是真实滚轮事件，用 JS 直接赋值 scrollTop 不会触发解锁，
       因此这里用 `page.mouse.wheel()` 发真实滚轮事件。
       另外该按钮的 DOM 属性 `disabled` 始终为 False，只有 class 上有 `bui-btn-disabled`，
       所以 Playwright 的可点击性检查发现不了它没解锁，必须自己判断 class。
    2. 点击 `.buyNowBtn___YuGWG`（详情页"立即购买"）进入选票页
    3. 选票页 .../allEvents/detail/selectTicket?activityId=xxx
       页面是**逐级渐进渲染**的，下一级控件必须等上一级选完才会出现：
         选场次 → 票价类别才渲染 → 选票档 → 购买数量和底部提交按钮才渲染
       因此 session_text 和 tier_text 都是**必填**，留空会导致后续元素永远找不到。
       - 场次列表：`.sessionListWrapper___gDIN1 .sessionList___al29_`
         **没有默认选中项**；选中后该元素会多一个 `fouceStyle___Qr7dA` class
       - 票价类别：`.ticketLevel___Q0XFF .levelItem___rPZ55`，
         文字形如"B/标准门票 (地面座位) (HK$ 2199.00)"，按包含关系匹配。
         **售罄/未开售的票档会多一个 `disableClass___BDFqG` class，必须跳过**，
         否则会选中一个点不动的票档，导致后续控件不渲染。
       - 购买数量：`.numList___YhTa8 .buyNum___a5xrK` 的**直接子** span 有三个，
         依次是[减号, 当前数量, 加号]。注意加减号按钮内部还嵌了一层
         `<span class="anticon iconfont">` 图标，所以必须用 `> span` 限定直接子元素，
         否则后代选择器会拿到 5 个、下标全错。
         到达边界的一侧会置灰，且加减号用的是**不同** class：
         减号 `descGray___sLahL`、加号 `addGray___eA19J`。
       - 提交：`.sellTicketBottomBtnWrap___DBOGi button.mz-button`
         按钮文字随活动类型不同：选座类活动是"下一步"，非选座类（通行证/停车证）是"立即购买"，
         选择器本身两种都通用。未选票档时该按钮是禁用态。
    4. 确认订单页，路由 `#/confirmOrder?eventIds=xxx&projectId=xxx`
       "确认订单"按钮：`.SubmitInfoBar___Ats0a .confirmBtn___UKt6g button.mz-button`
       **注意**：根据站点购票须知，提交后系统会自动分配座位并开始五分钟付款倒计时，
       也就是说走到这一步已经产生真实占位。
       另外部分活动这一页还有必填字段（例如停车证要填车牌号码）。
       按当前需求，本模块走到这里就停：**只判断是否到达，不点击确认、不付款。**

并发模型：
    抢票通常需要 **多个** BitBrowser 窗口（多个已登录账号）并发同时抢，
    因此这里的调度以"browser_id 列表"为单位，为每个 browser_id 起一个
    独立的并发任务（asyncio.gather），谁先抢到谁成功，其余可选择自动停止。
"""
import asyncio
import random
import re
import time
from typing import Optional

from playwright.async_api import Playwright, Page

from . import ticket_api
from . import ticket_store as store
from .bit_api import openBrowser
from ..utils.logger import task_state_manager


# ------------------------------------------------------------------
# 单个浏览器窗口的抢票流程（骨架，需按目标活动页面结构补齐）
# ------------------------------------------------------------------

# 页面文本和接口文本对不齐的地方，匹配前必须先抹平（2026-08-26 在 NCT 127 实测）：
#
#   接口 priceName:  'B/标准门票 (企位)'          我们拼成 'B/标准门票 (企位) (HK$ 1799.00)'
#   页面 innerText:  '站B/标准门票 (企位)\xa0(HK$ 1799.00)'
#
# 两处差异：
#   1. 名称和价格之间页面用的是 &nbsp;（U+00A0），我们拼的是普通空格。
#      只差这一个字符，整个活动 6 个票档一个都匹配不上。
#   2. 页面在票档名前面挂了个角标 <span class="gaTag___rlXCy">站</span>，
#      innerText 会把它一起拼进来。这是页面加的装饰，不属于票档名。
#      用「包含」而不是「相等」来匹配就能容忍它。
#
# 所以匹配统一走：两边都把 \xa0 换成空格、压掉多余空白，再判断包含关系。
def _normalize_text(t: str) -> str:
    return " ".join((t or "").replace("\xa0", " ").split())


async def _find_index_by_text(page: Page, selector: str, want: str):
    """
    在一组元素里按**规范化后的包含关系**找匹配项，返回 (下标, 页面上所有候选文本)。

    没找到时返回 (-1, 候选列表)——候选列表是给错误日志用的：
    只说"没找到"没法排查，把页面上实际有什么一并打出来，一眼就能看出是
    文本对不上还是压根没这个票档。
    """
    texts = await page.eval_on_selector_all(
        selector, "els => els.map(e => e.innerText)"
    )
    target = _normalize_text(want)
    for i, t in enumerate(texts):
        if target and target in _normalize_text(t):
            return i, texts
    return -1, texts


async def grab_ticket_on_page(
    page: Page,
    browser_id: str,
    task_id: Optional[str] = None,
    obj: Optional[dict] = None,
) -> bool:
    """
    在单个已连接的 Page 上执行一次完整的抢票流程（hkticketing 站点）。

    :param page: 已通过 CDP 连接到某个 BitBrowser 窗口的 Playwright Page
                 （页面应已停留在目标活动详情页或选票页，且已登录）
    :param browser_id: 当前操作对应的 BitBrowser 窗口ID，仅用于日志区分来源
    :param task_id: 任务ID，用于配合 task_state_manager 支持暂停/停止
    :param obj: 抢票参数对象，例如：
                {
                    "event_url_fragment": "hkticketing",
                    "session_text": "2026年9月19日",  # 场次匹配文本（子串匹配）
                    "tier_text": "1日通行券",           # 票价类别匹配文本（子串匹配）
                    "quantity": 2,
                    "retry_interval_min": 2.0,
                    "retry_interval_max": 4.0,
                    "max_attempts": 0,  # 0 表示不限制，持续重试直到抢到或任务被停止
                }
    :return: 是否抢票成功
    """
    obj = obj or {}
    max_attempts = int(obj.get("max_attempts", 0))
    retry_interval_min = float(obj.get("retry_interval_min", 2.0))
    retry_interval_max = float(obj.get("retry_interval_max", 4.0))
    session_text = obj.get("session_text", "")
    tier_text = obj.get("tier_text", "")
    quantity = int(obj.get("quantity", 1))
    event_url = obj.get("event_url", "")
    project_id = extract_project_id(event_url)

    attempt = 0
    consecutive_failures = 0
    restocking = False        # 上一轮是不是在蹲回流票
    restock_since = None      # 从什么时候开始蹲的，用来算降频档位
    restock_stage_logged = None
    while True:
        attempt += 1

        # 任务状态检查：支持外部随时暂停/停止
        if task_id:
            if not await _wait_if_paused_or_stopped(task_id, browser_id):
                return False

        if max_attempts > 0 and attempt > max_attempts:
            print(f"[{browser_id}] 已达到最大尝试次数 {max_attempts}，放弃抢票")
            return False

        # 蹲回流票期间不再逐轮打印「第 N 次尝试」：
        # 这行是普通日志，会把原地刷新的蹲票状态行顶掉，
        # 结果就是刷了一屏「第 N 次尝试」却看不出到底在等什么。
        # 蹲票状态由下面的 [COUNTDOWN] 那条统一显示，自带次数。
        if not restocking:
            print(f"[{browser_id}] 第 {attempt} 次尝试抢票...")

        try:
            # 0) 一上来先确认登录态。选票页需要登录，未登录的话点场次会被弹到登录页，
            #    如果不识别这种情况，重试循环会一直空转到天荒地老。
            #    第一轮用接口精确判断（页面停在任意路由都测得准），
            #    后续轮次只看 URL——接口调用有开销，抢票循环里能省则省。
            if attempt == 1:
                if not await ticket_api.is_logged_in(page):
                    print(f"[{browser_id}] 该窗口未登录，请先执行「一键启动并登录」")
                    return False
            elif await is_on_login_page(page):
                print(f"[{browser_id}] 登录态已失效，停止本窗口")
                return False

            # 0.5) 确保停在目标活动页。上一轮失败后页面可能已经漂到别处，
            #      不拉回来的话后面找什么都找不到。
            if not await ensure_on_event_page(page, event_url, project_id, browser_id):
                return False

            # 底部 Cookie 条会盖住选票页底部的提交按钮，先关掉
            await dismiss_cookie_banner(page)

            # 1) 购票须知弹窗：进详情页就会自动弹出，遮罩会挡住"立即购买"，
            #    所以必须先处理它。不在详情页时该弹窗不存在，直接跳过。
            await _accept_notice_modal(page, browser_id)

            # 2) 若停在详情页，点"立即购买"进入选票流程
            if await _try_click(page, ".buyNowBtn___YuGWG", timeout=1500):
                print(f"[{browser_id}] 已点击详情页「立即购买」")
                await asyncio.sleep(0.5)

            # 3) 等待选票页加载
            try:
                await page.wait_for_selector(
                    ".sellTicketContent___PIY_T", timeout=5000
                )
            except Exception:
                # 把当前落点带出来，否则"未就绪"三个字看不出到底卡在哪
                where = page.url.split("?")[0].split("#")[-1] or page.url
                print(
                    f"[{browser_id}] 选票页未就绪（当前停在 {where}），"
                    f"可能仍在排队/等候区或未开售，重试..."
                )
                raise RuntimeError("选票页未就绪")

            # 4) 选场次。页面没有默认选中项，且不选场次票档就不会渲染，所以这一步是必须的。
            #    同样走规范化匹配：票档那边已经证实页面会用 &nbsp;，
            #    场次这边目前没遇到，但是同一类问题，不值得等它再咬一次。
            s_idx, s_texts = await _find_index_by_text(
                page, ".sessionListWrapper___gDIN1 .sessionList___al29_", session_text
            )
            if s_idx < 0:
                avail = "、".join(_normalize_text(t) for t in s_texts) or "（页面上没有场次）"
                print(f"[{browser_id}] 未找到匹配场次「{session_text}」，本次放弃")
                print(f"[{browser_id}] 页面上可选的场次是：{avail}")
                raise RuntimeError("场次未找到")
            await page.locator(
                ".sessionListWrapper___gDIN1 .sessionList___al29_"
            ).nth(s_idx).click()

            # 点场次是登录态失效最先暴露的地方，这里再确认一次，
            # 免得后面对着登录页找票档，白白重试到超时
            await asyncio.sleep(0.5)
            if await is_on_login_page(page):
                print(f"[{browser_id}] 选场次时被弹到登录页，登录态已失效，停止本窗口")
                return False

            # 票档是选完场次才渲染的，必须等它出现再去匹配。
            # 固定 sleep 不够稳：抢票时页面往往更慢，会出现"票档明明有却报没找到"
            # 然后白白空转一整轮。
            try:
                await page.wait_for_selector(TIER_ITEM_SELECTOR, timeout=5000)
            except Exception:
                print(f"[{browser_id}] 场次选中后票档没渲染出来，重试...")
                raise RuntimeError("票档未渲染")

            # 5) 选票价类别。同样没有默认选中项，不选票档数量和提交按钮就不会渲染。
            #    售罄/未开售的票档带 disableClass___BDFqG，点了也没反应，所以要排除掉。
            #
            #    **这里的"选不中"正是抢回流票的工作方式**：目标票档售罄时匹配不到，
            #    本轮直接结束、等间隔后重来；一旦有人退票、站点把 disableClass 摘掉，
            #    下一轮就能立刻点中并往下走。所以这不是异常，是预期中的轮询等待。
            #    匹配走 _find_index_by_text 而不是 Playwright 的 has_text：
            #    页面文本里名称和价格之间是 &nbsp;，has_text 是逐字符比对，
            #    差这一个字符就全都匹配不上（详见 _normalize_text 的说明）。
            idx, all_texts = await _find_index_by_text(
                page,
                ".ticketLevel___Q0XFF .levelItem___rPZ55:not(.disableClass___BDFqG)",
                tier_text,
            )
            if idx < 0:
                # 区分"票档存在但暂时卖不了"和"压根没这个票档"：
                # 前者继续等就行，后者是配置选错了，等到天荒地老也没用
                any_idx, _ = await _find_index_by_text(
                    page, ".ticketLevel___Q0XFF .levelItem___rPZ55", tier_text
                )
                if any_idx >= 0:
                    raise WaitingForRestock(f"票档「{tier_text}」售罄中，继续蹲回流票")
                # 把页面上实际有什么一并打出来。只说"没找到"没法排查，
                # 列出候选就能一眼看出是文本对不上、还是这个票档压根不在售票列表里
                # （比如会员预售票档要输会员码才会出现）。
                avail = "、".join(_normalize_text(t) for t in all_texts) or "（页面上一个票档都没有）"
                print(f"[{browser_id}] 未找到票档「{tier_text}」")
                print(f"[{browser_id}] 页面上可选的票档是：{avail}")
                raise RuntimeError("票档不存在")
            await page.locator(
                ".ticketLevel___Q0XFF .levelItem___rPZ55:not(.disableClass___BDFqG)"
            ).nth(idx).click()
            await asyncio.sleep(0.3)

            # 6) 设置购买数量。结构：.buyNum___a5xrK > span[减号, 当前数量, 加号]，
            #    到达边界的一侧会带 descGray___sLahL（置灰）。这里每点一次都确认数字真的变了，
            #    以免撞上限购上限后空点一堆。
            #
            #    **无论买几张都要调用。** 曾经写成 `if quantity > 1` 才调，
            #    理由是"买 1 张就是默认值"——那个假设是错的：2026-08-26 在 NCT 127
            #    实测，这个活动的数量默认是 **0** 不是 1，合计显示 HK$ 0.00。
            #    跳过设置的话就带着 0 张去点「下一步」，永远走不到确认订单页，
            #    而且外面看起来像"抢不到"，完全查不出原因。
            got = await _set_quantity(page, browser_id, quantity)
            if got < 1:
                print(f"[{browser_id}] 数量没能调上去（当前 {got}），本轮放弃")
                raise RuntimeError("数量为 0")
            if got < quantity:
                print(f"[{browser_id}] 受限购限制，实际只能买 {got} 张（目标 {quantity}）")

            # 7) 提交订单（选座类活动上面写"下一步"，非选座类写"立即购买"，选择器通用）
            submit_btn = page.locator(
                ".sellTicketBottomBtnWrap___DBOGi button.mz-button"
            ).first
            if await submit_btn.count() == 0:
                print(f"[{browser_id}] 未找到提交按钮，可能票已售罄或页面结构变化")
                raise RuntimeError("提交按钮未找到")

            await submit_btn.click()
            await asyncio.sleep(1)

            # 7.5) 会员码关卡。点完「下一步」如果弹出会员优先购票码输入框，
            #      说明这场是会员预售，没有码根本过不去（2026-08-26 在 NCT 127 实测）。
            #      这不是"没抢到"，是"这条路走不通"——不识别的话会一直重试到天荒地老，
            #      日志上还只显示"本次尝试未成功"，完全看不出真正的原因。
            if await _needs_privilege_code(page):
                member_code = (obj.get("member_code") or "").strip()
                if not member_code:
                    print(f"[{browser_id}] 该场次是会员预售，点「下一步」后要求填写会员优先购票码")
                    print(f"[{browser_id}] 该抢票人没有填会员码，无法继续；"
                          f"请在「抢票人」里补上会员码，或换一个不需要会员码的场次")
                    await _dismiss_privilege_dialog(page)
                    return False

                if not await _submit_privilege_code(page, browser_id, member_code):
                    # 码不对/已用完是**确定性失败**，重试多少次都一样，
                    # 而且反复提交错误的码很可能被站点盯上，所以直接停掉这个窗口
                    await _dismiss_privilege_dialog(page)
                    return False
                await asyncio.sleep(1)

            # 8) 判断是否成功进入确认订单页
            success = await _check_success(page)

            if success:
                print(f"[{browser_id}] 已进入确认订单页")
                if obj.get("auto_confirm"):
                    # 勾选条款并锁单。锁单到此为止，**不做任何支付动作**
                    locked = await confirm_order(page, browser_id)
                else:
                    print(f"[{browser_id}] 未开启自动确认，已停在确认订单页，请手动完成")
                    locked = False

                # 记战果。**锁没锁上都要记**：
                # 锁上了要留订单号去付款；没锁上（条款没勾上、有必填项没填）
                # 说明这一单已经占住位置、正等着人工去处理，
                # 这种情况反而更需要提醒——不记的话用户根本不知道有单等着他。
                await _record_result(page, browser_id, obj, locked)
                return True
            else:
                print(f"[{browser_id}] 本次尝试未成功（可能售罄/被顶掉），准备重试")

            consecutive_failures = 0
            restocking = False

        except WaitingForRestock as e:
            # 蹲回流票的正常状态，不是故障：
            # 不计入连续失败（否则会误触发限流退避，白白拉长轮询间隔），
            # 日志用 [COUNTDOWN] 前缀让前端原地刷新一行，不然蹲几小时会把日志刷爆。
            now = time.strftime("%H:%M:%S")
            if restock_since is None:
                restock_since = time.time()
            waited = int(time.time() - restock_since)
            print(
                f"[COUNTDOWN] [{browser_id}] {e} · 已查 {attempt} 次 · "
                f"已蹲 {waited // 60}分{waited % 60}秒 · 最近 {now}"
            )
            consecutive_failures = 0
            restocking = True

        except Exception as e:
            print(f"[{browser_id}] 抢票尝试出错：{e}")
            consecutive_failures += 1
            # 不再是"售罄等回流"的状态了，降频计时重来
            restocking = False
            restock_since = None
            restock_stage_logged = None

        # 限流退避。站点是按 IP 限流的，被限之后连站点自己的请求都会
        # ERR_CONNECTION_CLOSED，页面整个渲染不出来——这时候再密集重试
        # 只会让封禁一直续期，必须退下来等它放行。
        if consecutive_failures >= RATE_LIMIT_FAILURE_THRESHOLD:
            if await looks_rate_limited(page):
                backoff = min(
                    RATE_LIMIT_BACKOFF_BASE * (2 ** (consecutive_failures - RATE_LIMIT_FAILURE_THRESHOLD)),
                    RATE_LIMIT_BACKOFF_MAX,
                )
                print(
                    f"[{browser_id}] 疑似被站点限流（连续失败 {consecutive_failures} 次，"
                    f"页面请求被拒），退避 {int(backoff)} 秒后再试"
                )
                await asyncio.sleep(backoff)
                continue

        # 重试前随机等待，避免所有窗口同频请求过于规律。
        # 蹲回流票时按 RESTOCK_STAGES 逐级降频，理由见该常量的说明。
        lo, hi = retry_interval_min, retry_interval_max
        if restocking and restock_since is not None:
            waited = time.time() - restock_since
            for idx, (until, s_lo, s_hi) in enumerate(RESTOCK_STAGES):
                if until is None or waited < until:
                    if s_lo is not None:
                        lo, hi = s_lo, s_hi
                    if restock_stage_logged != idx and idx > 0:
                        print(
                            f"[{browser_id}] 已蹲 {int(waited // 60)} 分钟，"
                            f"轮询间隔调整为 {lo:.0f}~{hi:.0f} 秒"
                            f"（回流票出现时刻随机，降频不影响抢中概率，可显著降低被限流风险）"
                        )
                        restock_stage_logged = idx
                    break

        await asyncio.sleep(random.uniform(lo, hi))


# 确认订单页「已阅读并同意条款及细则和隐私政策」的勾选控件（实测）。
# 注意这里没有原生 input，选中状态只体现在图标 class 上。
TIER_ITEM_SELECTOR = ".ticketLevel___Q0XFF .levelItem___rPZ55"


class WaitingForRestock(Exception):
    """
    目标票档当前售罄，正在等回流票。

    单独一个类型是为了跟真正的故障区分开：蹲回流票时"这一轮没抢到"是常态，
    可能持续几小时，日志不该刷满红色的「出错」，也不该触发限流退避
    （站点没拒绝我们，只是没票）。
    """

# 限流退避参数。
# 站点按 IP 限流，触发后该窗口连正常浏览都不行（所有请求 ERR_CONNECTION_CLOSED）。
# 实测密集请求几十次就会中招，所以连续失败到一定次数就要停下来确认是不是被限了。
# 蹲回流票的阶梯降频。
#
# 为什么要分阶段：回流票不是均匀出现的。开票瞬间抢购失败释放的票集中在最初
# 几分钟，之后是零星退票，出现时刻完全随机。所以密集轮询只在前期有意义，
# 之后再密集也不会更早发现，白白增加请求量。
#
# 为什么必须做：蹲票走的是 WaitingForRestock 分支，那个分支会把
# consecutive_failures 归零（售罄是正常状态不是故障），因此**限流退避对蹲票
# 完全不生效**。不分阶段的话就是以 2~4 秒无限期轮询下去——蹲一晚上 8 小时
# 约 8000 次请求。这个项目已经因为请求过密被站点封过一次 IP。
# 分阶段后同样 8 小时约 600 次，抢中概率几乎不变，风控风险差一个数量级。
#
# 每档 (持续到第几秒, 间隔下限, 间隔上限)；超过最后一档就一直用最后一档。
RESTOCK_STAGES = (
    (120,   None, None),   # 前 2 分钟：用用户设的间隔（默认 2~4 秒）
    (900,   8.0,  15.0),   # 2~15 分钟
    (None,  30.0, 60.0),   # 15 分钟以后
)

RATE_LIMIT_FAILURE_THRESHOLD = 3   # 连续失败几次开始怀疑被限流
RATE_LIMIT_BACKOFF_BASE = 15       # 首次退避秒数
RATE_LIMIT_BACKOFF_MAX = 300       # 退避上限，5 分钟

# 订单号形如 1580400200000181：16 位数字。放宽到 14~20 位，
# 站点改了长度也还能抓到，不至于因为多一位少一位就整个失效。
ORDER_ID_RE = re.compile(r"\b(\d{14,20})\b")

# 支付页 URL 上带订单标识的参数名。站点用过 orderId，
# 但同类站点也常见 orderToken/orderNo，一并试，取第一个匹配上的。
ORDER_URL_KEYS = ("orderId", "orderToken", "orderNo", "orderid", "order_id")


async def extract_order_id(page: Page, label: str) -> Optional[str]:
    """
    锁单成功后从支付页上把订单号抓下来。

    **抓不到不是错误。** 这个函数依赖页面结构和 URL 参数，站点一改版就可能失效；
    但"这个人抢到了"这件事本身比订单号更重要——调用方必须在抓不到时照样记录战果，
    只是订单号留空、提示用户去窗口里自己看。为这个抓不到就丢掉整条记录，
    等于让用户以为没抢到，直接错过付款。

    三条路依次试，都很便宜：
        1. URL 参数（最可靠，锁单后一般会跳到 /pay?orderId=xxx 这类地址）
        2. URL 路径里的长数字（有些站点是 /order/1580400200000181 这种形式）
        3. 页面文本里「订单号」附近的长数字（前两条都没有时的兜底）
    """
    try:
        url = page.url or ""

        # 1) URL 查询参数
        for key in ORDER_URL_KEYS:
            m = re.search(rf"[?&]{key}=(\d{{14,20}})", url)
            if m:
                return m.group(1)

        # 2) URL 路径里的长数字。要排除 projectId——活动页 URL 里也有同样长度的数字，
        #    直接扫全 URL 会把活动 ID 当成订单号。
        path_part = re.sub(r"projectId=\d+", "", url)
        m = ORDER_ID_RE.search(path_part)
        if m:
            return m.group(1)

        # 3) 页面文本兜底：找「订单号/訂單編號/Order」后面跟着的长数字
        text = await page.inner_text("body", timeout=3000)
        m = re.search(r"(?:订单号|訂單編號|订单编号|Order\s*(?:No|Number)?)\D{0,10}(\d{14,20})", text)
        if m:
            return m.group(1)
    except Exception as e:
        print(f"[{label}] 抓取订单号时出错（不影响战果记录）：{e}")
    return None


AGREEMENT_BOX = ".agreementCheckBox___prwOG"
AGREEMENT_ICON = ".agreementIcon___l58tx"
AGREEMENT_SELECTED_MARK = "agreementIconSelected"


async def dismiss_cookie_banner(page: Page) -> bool:
    """
    关掉页面底部的 Cookie 提示条。

    这个条固定在页面底部，会盖住选票页底部的提交按钮，不关掉可能点不到。
    站点这个条只有一个「同意并关闭」按钮，没有拒绝选项——它是告知性质的
    （文案写明「继续浏览即表示同意」），关掉它不改变任何隐私选择。
    """
    try:
        btn = page.locator("text=同意并关闭").first
        if await btn.count() > 0 and await btn.is_visible():
            await btn.click()
            await asyncio.sleep(0.3)
            return True
    except Exception:
        pass
    return False


def extract_project_id(url: str) -> str:
    """从活动链接里抠出 projectId / activityId。抠不到返回空串。"""
    m = re.search(r"(?:projectId|activityId)=(\d+)", url or "")
    return m.group(1) if m else ""


def is_on_event_page(page_url: str, project_id: str) -> bool:
    """
    当前页面是不是**目标活动**的详情页或选票页。

    别只用 "hkticketing" 这种域名关键词判断——站点首页、我的账户、票夹
    统统都含这个词，一旦匹配上就会误以为"已经在活动页了"而跳过导航，
    然后在首页上死找「立即购买」，表现出来就是无限重试「选票页未就绪」。
    """
    if "allEvents/detail" not in page_url:
        return False
    if not project_id:
        return True
    return project_id in page_url


async def ensure_on_event_page(
    page: Page, event_url: str, project_id: str, label: str
) -> bool:
    """
    确保页面停在目标活动页，不在就导航过去。

    每轮重试都会调一次：上一轮失败后页面可能已经漂到别处
    （被弹回首页、跳到登录页、卡在中间态），不拉回来后面全是白跑。
    """
    if is_on_event_page(page.url, project_id):
        return True

    if not event_url:
        print(f"[{label}] 当前不在活动页，且没有配置活动链接，无法继续")
        return False

    print(f"[{label}] 当前不在活动页，正在导航到活动详情页...")
    try:
        await page.goto(event_url, wait_until="domcontentloaded")
        # SPA 要等真实元素渲染，domcontentloaded 时页面还是空的
        await page.wait_for_selector(".buyNowBtn___YuGWG", state="attached", timeout=20000)
        return True
    except Exception:
        if await is_on_login_page(page):
            print(f"[{label}] 打开活动页后被弹到登录页，该窗口未登录")
        else:
            print(f"[{label}] 活动页没加载出「立即购买」，可能被风控或链接有误")
        return False


async def looks_rate_limited(page: Page) -> bool:
    """
    判断该窗口是不是被站点限流了。

    被限流的表现不是返回 429，而是**直接掐连接**：站点自己的接口请求全部
    `net::ERR_CONNECTION_CLOSED`，从服务器直连则是 SSL EOF（握手都没完成）。
    页面因此整个渲染不出来。

    这里发一个最轻量的探测请求：能拿到响应（哪怕是业务错误）就说明网络通着，
    连响应都拿不到才判定为被限。
    """
    try:
        probe = await page.evaluate(
            """async () => {
                try {
                    const r = await fetch('https://rest-sig.imaitix.com/api/user/loginUser?langType=1',
                                          { credentials: 'include' });
                    return r.status;
                } catch (e) { return -1; }
            }"""
        )
        return probe == -1
    except Exception:
        # evaluate 本身都失败，页面多半已经不可用了
        return True


async def is_on_login_page(page: Page) -> bool:
    """
    是否被踢到了登录页。

    站点的选票页需要登录：未登录时能进到选票页看到场次列表，但**一点场次**
    就会跳到 `#/login/account?loginBefore=...`。这个跳转很容易被误判成
    "页面结构变了"，所以单独给个函数明确识别。
    """
    return "#/login" in page.url


async def _try_click(page: Page, selector: str, timeout: int = 1500) -> bool:
    """
    如果 selector 对应的元素在 timeout 内出现且可见则点击并返回 True，
    否则不报错、直接返回 False（用于处理"可能出现也可能不出现"的弹窗/按钮）。
    """
    try:
        locator = page.locator(selector).first
        await locator.wait_for(state="visible", timeout=timeout)
        await locator.click()
        return True
    except Exception:
        return False


async def _accept_notice_modal(page: Page, browser_id: str) -> bool:
    """
    处理详情页自动弹出的"购票须知"弹窗。

    这个弹窗有两个坑：
      1. 它一进详情页就弹，遮罩会挡住"立即购买"，所以必须先于购买按钮处理；
      2. "知晓并同意"按钮初始是禁用的，必须把须知正文滚到底才解锁。而且它监听的是
         真实滚轮事件，用 JS 赋值 scrollTop 不触发，所以这里发真实的 mouse.wheel。
         该按钮的 DOM `disabled` 属性恒为 False（只有 class 上带 bui-btn-disabled），
         Playwright 的可点击性检查看不出来，只能自己判断 class。

    :return: 是否确实处理了一个弹窗（不存在弹窗时返回 False，属正常情况）
    """
    ok_btn = page.locator("button.defaultOkBtn").first
    try:
        await ok_btn.wait_for(state="visible", timeout=2000)
    except Exception:
        return False  # 没有弹窗，正常跳过

    # 若按钮还锁着，滚动须知正文直到解锁
    scroller = page.locator(
        ".bui-modal .contentWrapper .bui-scroll.bui-scroll-view-scroll-y"
    ).first
    for _ in range(20):
        cls = await ok_btn.get_attribute("class") or ""
        if "bui-btn-disabled" not in cls:
            break
        if await scroller.count() == 0:
            break
        box = await scroller.bounding_box()
        if not box:
            break
        # 把鼠标移到须知正文区域中心，发真实滚轮事件
        await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        await page.mouse.wheel(0, 600)
        await asyncio.sleep(0.15)
    else:
        print(f"[{browser_id}] 购票须知滚动到底后按钮仍未解锁")

    cls = await ok_btn.get_attribute("class") or ""
    if "bui-btn-disabled" in cls:
        raise RuntimeError("购票须知弹窗未能解锁")

    await ok_btn.click()
    print(f"[{browser_id}] 已确认购票须知弹窗")
    await asyncio.sleep(0.5)
    return True


async def _set_quantity(page: Page, browser_id: str, quantity: int) -> int:
    """
    把购买数量调到 quantity。返回实际达到的数量。

    这里必须用 `> span` 只取直接子元素：加减号按钮里还嵌了一层
    `<span class="anticon iconfont">` 图标，用后代选择器会取到 5 个 span
    （[减号, 图标, 数字, 加号, 图标]），nth 下标全错位。
    直接子元素才是干净的三个：[减号, 当前数量, 加号]。

    另外置灰 class 加减号是分开的：减号 `descGray___sLahL`、加号 `addGray___eA19J`。
    每点一次加号还会回读数字确认真的加上去了，双保险，避免撞上限后空点一堆。
    """
    spans = page.locator(".numList___YhTa8 .buyNum___a5xrK > span")
    if await spans.count() < 3:
        print(f"[{browser_id}] 未找到数量控件，保持默认数量")
        return 1

    num_span = spans.nth(1)
    plus_btn = spans.nth(2)

    async def _read_num() -> int:
        try:
            return int((await num_span.inner_text()).strip())
        except Exception:
            return -1

    current = await _read_num()
    while current < quantity:
        cls = await plus_btn.get_attribute("class") or ""
        if "addGray___eA19J" in cls:
            print(f"[{browser_id}] 已到限购上限，实际数量 {current}（目标 {quantity}）")
            break
        await plus_btn.click()
        await asyncio.sleep(0.2)
        new_num = await _read_num()
        if new_num == current:
            print(f"[{browser_id}] 数量点击无效，停在 {current}（目标 {quantity}）")
            break
        current = new_num

    return current


# 会员优先购票码弹窗（2026-08-26 在 NCT 127 预售场实测）。
#
# 触发时机：选好场次票档、把数量调上去、点「下一步」之后弹出，
# **在此之前不会出现**，所以只能在提交动作之后判断。
# 弹窗里写明「每个会员优先购票码最多可购买每场 2 张门票」，取消则退回选票页，
# 不会产生任何订单。
PRIVILEGE_CODE_INPUT = ".privilegeCodeInput___YECG4 input"


async def _needs_privilege_code(page: Page) -> bool:
    """点完提交后是否弹出了会员优先购票码输入框。"""
    try:
        return await page.locator(PRIVILEGE_CODE_INPUT).count() > 0
    except Exception:
        return False


async def _dismiss_privilege_dialog(page: Page) -> None:
    """
    关掉会员码弹窗，把页面退回选票页。

    不关的话弹窗会一直挂在那儿，下一轮（或者人接手操作时）所有点击都被遮罩吃掉，
    看起来就像窗口卡死了。
    """
    try:
        cancel = page.locator("button.mz-button", has_text="取消").first
        if await cancel.count() > 0:
            await cancel.click()
            await asyncio.sleep(0.3)
    except Exception:
        pass


async def _submit_privilege_code(page: Page, label: str, code: str) -> bool:
    """
    把会员优先购票码填进弹窗并点「确定」。

    :return: 是否通过。**通过与否只能靠"弹窗还在不在"判断**——
        站点没有给出明确的成功/失败标识，码错了弹窗会留在原地（可能带错误提示），
        码对了弹窗关闭、流程继续往确认订单页走。

    码不对不做重试：那是确定性失败，重试多少次结果都一样，
    而且反复提交错误的码很容易被站点风控盯上。
    """
    try:
        inp = page.locator(PRIVILEGE_CODE_INPUT).first
        await inp.fill(code)
        await asyncio.sleep(0.2)

        ok_btn = page.locator("button.mz-button", has_text="确定").first
        if await ok_btn.count() == 0:
            print(f"[{label}] 会员码弹窗里没找到「确定」按钮")
            return False
        await ok_btn.click()
        await asyncio.sleep(1.5)

        if await _needs_privilege_code(page):
            # 弹窗还在 = 没过。把页面上的提示带出来，用户才知道是码错了还是用完了
            msg = ""
            try:
                box = page.locator(".mz-modal-content").first
                if await box.count() > 0:
                    msg = _normalize_text(await box.inner_text())[:120]
            except Exception:
                pass
            print(f"[{label}] 会员码未通过（可能填错或已用完）。弹窗提示：{msg or '（无）'}")
            return False

        print(f"[{label}] 会员码已通过")
        return True
    except Exception as e:
        print(f"[{label}] 填写会员码时出错：{e}")
        return False


async def _record_result(page: Page, label: str, obj: dict, locked: bool) -> None:
    """
    把一次抢中落到战果记录里。

    这是整个流程唯一的产出留存点。程序只锁单不付款，锁到的订单在等人去付、
    而且有付款时限；十个号并发抢完，谁抢到了、哪场、订单号多少，
    这些只存在于滚动过去的日志里等于没有。

    **绝不让这里的异常影响抢票结果。** 记录失败最多是少一条账，
    但如果因为写文件出错把已经抢到的流程搞崩，那是把大事搞砸了。
    """
    try:
        batch_id = obj.get("batch_id")
        if not batch_id:
            # 没有批次说明是直接调 run_single_browser 的调试路径，不记
            return

        order_id = await extract_order_id(page, label) if locked else None
        if locked and not order_id:
            print(
                f"[{label}] 锁单成功但没抓到订单号，已记录战果；"
                f"请到该窗口里查看订单号并尽快付款"
            )

        rec = store.add_result(
            batch_id,
            {
                "account_label": obj.get("account_label") or label,
                "email": obj.get("email"),
                "browser_id": obj.get("browser_id"),
                "browser_seq": obj.get("browser_seq"),
                "session_text": obj.get("session_text"),
                "tier_text": obj.get("tier_text"),
                "quantity": obj.get("quantity"),
                "order_id": order_id,
                "page_url": page.url,
            },
        )
        if not locked:
            store.set_result_status(rec["id"], "manual")
            print(f"[{label}] 已记入战果：需人工完成确认订单")
        else:
            tail = f"，订单号 {order_id}" if order_id else ""
            print(f"[{label}] 已记入战果：待支付{tail}")
    except Exception as e:
        print(f"[{label}] 写战果记录失败（不影响抢票结果）：{e}")


async def confirm_order(page: Page, label: str) -> bool:
    """
    在确认订单页勾选「已阅读并同意条款及细则和隐私政策」并点击「确认订单」，完成锁单。

    **注意：只锁单，不付款。** 锁单之后是支付环节，本模块不碰。

    确认订单页结构（2026-08-13 在真实订单页上实测）：
        同意勾选容器：`.agreementCheckBox___prwOG`（在 `.agreements___MO15u` 里）
        勾选图标：    `.agreementIcon___l58tx`，**选中时**多一个 `agreementIconSelected___U09FP`
        确认按钮：    `.SubmitInfoBar___Ats0a .confirmBtn___UKt6g button.mz-button`

    两个坑：
        1. 这里**没有原生 input**，是纯 div + class 标记，所以不能用 is_checked()，
           只能读图标的 class。
        2. Vue 的 DOM 更新是异步的，点完必须等一下再回读，
           点完立刻读会读到旧 class，误判成"没勾上"。
    """
    # 等确认订单页就位
    try:
        await page.wait_for_selector(".SubmitInfoBar___Ats0a .confirmBtn___UKt6g", timeout=10000)
    except Exception:
        print(f"[{label}] 没等到确认订单页，跳过锁单")
        return False

    # 等勾选框渲染出来再判断"有没有"。
    # 不等的话会在页面还没渲染完时就断定"该活动不需要勾选"，然后直接去点确认订单——
    # 万一它其实是必勾的，就会点在禁用按钮上白白浪费一整轮，最坏情况是漏勾下错单。
    try:
        await page.wait_for_selector(AGREEMENT_BOX, timeout=5000)
    except Exception:
        pass

    if await page.locator(AGREEMENT_BOX).count() == 0:
        print(f"[{label}] 确认订单页没找到同意勾选框，可能该活动不需要勾选")
    else:
        checked = await _tick_agreement(page, label)
        if not checked:
            print(
                f"[{label}] 没能勾上「已阅读并同意」，为避免下错单已停在确认订单页，"
                f"请手动勾选并点「确认订单」"
            )
            return False
        print(f"[{label}] 已勾选「已阅读并同意」")

    confirm_btn = page.locator(
        ".SubmitInfoBar___Ats0a .confirmBtn___UKt6g button.mz-button"
    ).first
    cls = await confirm_btn.get_attribute("class") or ""
    if "bui-btn-disabled" in cls or await confirm_btn.is_disabled():
        print(f"[{label}] 「确认订单」按钮仍是禁用态，可能还有必填项没填（如车牌号），已停手")
        return False

    await confirm_btn.click()
    print(f"[{label}] 已点击「确认订单」，锁单请求已发出")
    await asyncio.sleep(2)

    # 锁单成功通常会跳到支付页；这里只汇报，不做任何支付动作
    if "confirmOrder" not in page.url:
        print(f"[{label}] 已离开确认订单页，锁单成功。**请手动完成支付**")
        return True
    print(f"[{label}] 点击后仍停在确认订单页，请人工检查是否有未满足的必填项")
    return False


async def _tick_agreement(page: Page, label: str) -> bool:
    """
    勾选「已阅读并同意」，并回读确认真的勾上了。

    已经是选中态就直接返回，不会手贱再点一下把它取消掉。
    """
    if await _is_agreement_checked(page):
        return True

    # 图标和容器都可能是绑事件的那个元素，两个都试
    for target in (page.locator(AGREEMENT_ICON).first, page.locator(AGREEMENT_BOX).first):
        try:
            if await target.count() == 0:
                continue
            await target.click()
            # Vue 异步更新，必须等一下再回读，否则读到的是旧 class
            await asyncio.sleep(0.5)
            if await _is_agreement_checked(page):
                return True
        except Exception:
            continue

    return False


async def _is_agreement_checked(page: Page) -> bool:
    """
    回读「已阅读并同意」是否处于选中态。

    这里没有原生 input，选中与否只体现在图标的 class 上。
    """
    try:
        icon = page.locator(AGREEMENT_ICON).first
        if await icon.count() == 0:
            return False
        cls = await icon.get_attribute("class") or ""
        return AGREEMENT_SELECTED_MARK in cls
    except Exception:
        return False


async def _check_success(page: Page) -> bool:
    """
    判断是否抢票成功：进入确认订单页 `#/confirmOrder` 视为成功。

    优先看 URL 路由（最稳，不受文案/多语言影响），其次看确认订单按钮容器
    `.SubmitInfoBar___Ats0a .confirmBtn___UKt6g`。两者都是在真实站点上确认过的。

    注意：这里**只判断，不点击**「确认订单」。按当前需求，流程到提交订单为止，
    不自动确认、不自动付款。另外部分活动的确认订单页还有必填字段
    （例如停车证要填车牌号码），也需要人工处理。
    """
    try:
        await page.wait_for_url("**/confirmOrder*", timeout=3000)
        return True
    except Exception:
        pass
    try:
        await page.wait_for_selector(
            ".SubmitInfoBar___Ats0a .confirmBtn___UKt6g", timeout=2000
        )
        return True
    except Exception:
        return False


async def _wait_if_paused_or_stopped(task_id: str, browser_id: str) -> bool:
    """
    检查任务状态，处理暂停/停止。
    返回 True 表示可以继续执行，False 表示任务已停止（调用方应立即返回）。
    """
    state = task_state_manager.get_state(task_id)
    if state == "stopped":
        print(f"[{browser_id}] 任务已停止")
        return False
    elif state == "paused":
        print(f"[{browser_id}] 任务已暂停，等待恢复...")
        while True:
            current_state = task_state_manager.get_state(task_id)
            if current_state == "running":
                print(f"[{browser_id}] 任务已恢复，继续抢票...")
                return True
            elif current_state == "stopped":
                print(f"[{browser_id}] 任务已停止")
                return False
            await asyncio.sleep(0.5)
    return True


# ------------------------------------------------------------------
# 单个浏览器窗口：打开 BitBrowser 窗口 -> CDP 连接 -> 跑抢票流程
# ------------------------------------------------------------------

async def run_single_browser(
    playwright: Playwright,
    browser_id: str,
    task_id: Optional[str] = None,
    obj: Optional[dict] = None,
    label: Optional[str] = None,
) -> bool:
    """
    打开（连接）单个 BitBrowser 窗口并执行抢票流程。

    :param label: 日志前缀，多账号并发时用账号备注/邮箱比用窗口ID易读，
                  不传则退回窗口ID
    """
    label = label or browser_id
    print(f"[{label}] 正在连接浏览器窗口...")
    # to_thread：openBrowser 是同步 requests 调用，一次几秒。直接在协程里调
    # 会卡住事件循环，多账号并发开抢时开窗这一步会退化成串行排队。
    res = await asyncio.to_thread(openBrowser, browser_id)
    if not res.get("success"):
        print(f"[{label}] 打开窗口失败：{res.get('msg')}")
        return False
    ws = res["data"]["ws"]

    chromium = playwright.chromium
    browser = await chromium.connect_over_cdp(ws)

    obj = obj or {}
    event_url = obj.get("event_url") or ""
    project_id = extract_project_id(event_url)

    # 优先复用**已经停在目标活动页**的标签页。
    # 注意判断的是"是不是这个活动的详情页/选票页"，不是"是不是这个站点的页面"——
    # 后者会把首页、票夹、我的账户全都算进来，导致在错误的页面上跑流程。
    page = None
    for context in browser.contexts:
        for p in context.pages:
            if is_on_event_page(p.url, project_id):
                page = p
                break
        if page:
            break

    if page is None:
        # 没有现成的活动页就拿一个可用页面，稍后由 ensure_on_event_page 导航过去
        for context in browser.contexts:
            if context.pages:
                page = context.pages[0]
                break
        if page is None:
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await context.new_page()

    await page.bring_to_front()

    return await grab_ticket_on_page(page, label, task_id, obj)


# ------------------------------------------------------------------
# 多浏览器并发调度：多个已登录窗口同时抢票
# ------------------------------------------------------------------

async def _wait_until_start(start_at: float, task_id: Optional[str]) -> bool:
    """
    定时抢票：倒计时等到 start_at（Unix 时间戳）再放行。

    倒计时用 [COUNTDOWN] 前缀打日志，前端会把这类日志原地刷新成一行，
    不会把日志区刷爆。返回 False 表示等待期间任务被停掉了。

    暂停也必须在这里生效。早先只检查 stopped，于是暂停一个定时任务之后
    倒计时照走、到点照样开抢——界面上明明显示「已暂停」，窗口却全被拉起来了
    （虽然进抢票循环后会立刻停住，但窗口已经开了，行为和用户的预期完全相反）。
    """
    paused_notified = False
    while True:
        state = task_state_manager.get_state(task_id) if task_id else None
        if state == "stopped":
            print("任务已停止，取消定时抢票")
            return False
        if state == "paused":
            # 暂停期间不推进倒计时，也不放行。恢复后如果开抢时刻已经过了，
            # 下一轮 remaining <= 0 会立刻放行，不会因为暂停过就永远抢不了。
            if not paused_notified:
                print("定时抢票已暂停，倒计时停住；点「恢复」继续")
                paused_notified = True
            await asyncio.sleep(0.5)
            continue
        if paused_notified:
            print("定时抢票已恢复")
            paused_notified = False

        remaining = start_at - time.time()
        if remaining <= 0:
            print("到点，开始抢票！")
            return True

        mins, secs = divmod(int(remaining), 60)
        hours, mins = divmod(mins, 60)
        print(f"[COUNTDOWN] 距离开抢还有 {hours:02d}:{mins:02d}:{secs:02d}")
        # 临近开抢时收紧轮询精度，避免睡过头错过时间点
        await asyncio.sleep(0.2 if remaining < 2 else min(1.0, remaining))


async def run(
    playwright: Playwright,
    tasks: list,
    task_id: Optional[str] = None,
    common: Optional[dict] = None,
    stop_others_on_success: bool = False,
    start_at: Optional[float] = None,
) -> dict:
    """
    并发驱动多个 BitBrowser 窗口同时抢票，**每个窗口带各自的抢票配置**。

    :param tasks: 每项形如
        {
            "browser_id": "窗口ID",
            "label": "账号备注或邮箱，仅用于日志",
            "session_text": "该账号要抢的场次",
            "tier_text": "该账号要抢的票档",
            "quantity": 2,
        }
    :param common: 所有账号共用的参数（活动链接、重试间隔、最大尝试次数等）
    :param stop_others_on_success: 某个账号抢到后是否停掉其余账号。
        默认 **False**：多账号抢票的常见诉求是"每个账号各抢各的"，
        一个成功不代表别人不用抢了。只有在多号抢同一张票时才该开。
    :param start_at: Unix 时间戳，传了就等到该时刻再开抢（定时抢票）；
        不传则立即开抢。
    :return: {"success_labels": [...], "results": {label: bool}}
    """
    if not tasks:
        raise ValueError("没有可执行的抢票配置，请先在第二步给账号配置场次和票档")

    common = common or {}

    if start_at:
        if not await _wait_until_start(start_at, task_id):
            return {"success_labels": [], "results": {}}

    print(f"开始并发抢票，共 {len(tasks)} 个账号参与")
    for t in tasks:
        print(
            f"  - {t.get('label')}：{t.get('session_text')} / "
            f"{t.get('tier_text')} x{t.get('quantity', 1)}"
        )

    results = {}
    success_labels = []

    async def _worker(t: dict):
        label = t.get("label") or t.get("browser_id")
        # 每个账号自己的场次/票档/张数，叠加所有人共用的重试等参数
        obj = dict(common)
        obj.update(
            {
                "session_text": t.get("session_text", ""),
                "tier_text": t.get("tier_text", ""),
                "quantity": int(t.get("quantity", 1)),
                # 下面几项只用于战果记录，抢票流程本身不读
                "member_code": t.get("member_code", ""),
                "account_label": t.get("label", ""),
                "email": t.get("email", ""),
                "browser_id": t.get("browser_id", ""),
                "browser_seq": t.get("browser_seq"),
            }
        )
        try:
            ok = await run_single_browser(
                playwright, t["browser_id"], task_id, obj, label=label
            )
            results[label] = ok
            if ok:
                success_labels.append(label)
                if stop_others_on_success and task_id:
                    # 借用任务状态机，让其余窗口的重试循环下一次检查时自行退出
                    task_state_manager.stop_task(task_id)
        except Exception as e:
            print(f"[{label}] 抢票任务异常：{e}")
            results[label] = False

    await asyncio.gather(*[_worker(t) for t in tasks])

    if success_labels:
        print(f"抢票完成，成功账号：{'、'.join(success_labels)}")
    else:
        print("抢票结束，所有账号均未成功")

    return {"success_labels": success_labels, "results": results}
