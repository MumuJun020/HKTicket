"""
抢票配置的本地持久化（JSON 文件）。

存这几份数据，都在项目根的 data/ 目录下（已加入 .gitignore，不进版本库）：
    accounts.json       抢票人列表：账号、密码、备注、绑定的比特浏览器窗口
    event.json          第一步解析留存的活动票务信息（场次、票档）
    plans.json          第二步配置：每个账号各自要抢哪个场次、哪个票档、几张
    window_owner.json   窗口归属：某个窗口上次登录的是谁
    results.json        抢票结果：锁到的订单，**唯一不随启动清空的数据**

安全提醒：accounts.json 里的密码是**明文**存储的。这是本地单机工具的取舍
（登录时要把原文填进页面表单，做不了单向哈希）。因此：
    - data/ 已在 .gitignore 中，不会被提交
    - 不要把这个目录同步到网盘或打包分发
    - 这台机器如果是共享的，建议改用系统钥匙串之类的方案
"""
import json
import os
import sys
import threading
import uuid


def _data_dir() -> str:
    """
    数据目录。

    普通运行时是项目根的 data/（本文件往上三层）。

    **打包成单文件可执行程序时必须放在可执行文件旁边**，不能用相对本文件的路径：
    那时候代码被解压在系统临时目录里，程序一退出整个目录就被删了——
    抢到的订单记录会跟着一起消失。所以打包后按 sys.executable 定位。

    可以用环境变量 HKTICKET_DATA_DIR 覆盖（多份配置切换时有用）。
    """
    env = os.environ.get("HKTICKET_DATA_DIR")
    if env:
        return os.path.abspath(env)
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "data")
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "data")


DATA_DIR = _data_dir()

ACCOUNTS_FILE = os.path.join(DATA_DIR, "accounts.json")
EVENT_FILE = os.path.join(DATA_DIR, "event.json")
PLANS_FILE = os.path.join(DATA_DIR, "plans.json")
OWNERS_FILE = os.path.join(DATA_DIR, "window_owner.json")
RESULTS_FILE = os.path.join(DATA_DIR, "results.json")

# 读改写不是原子的，多个请求同时改会互相覆盖，这里用一把大锁串行化。
# 单机小工具的量级，够用。
_lock = threading.RLock()


def _read(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        # 文件损坏时不要让整个页面挂掉，退回默认值
        return default


def _write(path, data):
    os.makedirs(DATA_DIR, exist_ok=True)
    # 先写临时文件再替换，避免写到一半崩溃留下半个文件
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ------------------------------------------------------------------
# 抢票人账号
# ------------------------------------------------------------------

def get_accounts():
    """返回全部抢票人。密码原样带出，供登录模块使用。"""
    with _lock:
        return _read(ACCOUNTS_FILE, [])


def get_accounts_safe():
    """
    返回全部抢票人给前端展示，**密码明文带出**。

    这是刻意的：本工具是本机单人使用，密码遮成 ****** 反而看不出哪个账号配错了，
    改密码时也没法确认改对没有。数据本来就明文存在本地 data/accounts.json，
    遮显示并不增加任何实际安全性，只增加使用摩擦。
    """
    return [dict(a) for a in get_accounts()]


def save_account(account):
    """
    新增或更新一个抢票人。传入带 id 则更新，不带 id 则新增。

    更新时 password 传空字符串表示保留原密码不动（前端自动保存会频繁回传，
    密码框被清空时不应该把已存的密码洗掉）。要改密码就填新的。
    """
    email = (account.get("email") or "").strip()
    if not email:
        raise ValueError("账号（邮箱）不能为空")

    with _lock:
        accounts = _read(ACCOUNTS_FILE, [])
        acc_id = account.get("id")

        # 一个窗口只能绑一个抢票人。
        # 两个账号绑同一个窗口的话，一键登录时两个并发任务会连到同一个浏览器、
        # 操作同一个页面，互相覆盖对方填的账号密码——表现就是「两个人的密码进了
        # 同一个框，另一个窗口没人动」，而且看起来像「没拉起多个窗口」。
        # 这种状态在多账号抢票里没有任何合法用途，直接在入口拦掉。
        new_bid = (account.get("browser_id") or "").strip()
        if new_bid:
            for other in accounts:
                if other["id"] != acc_id and other.get("browser_id") == new_bid:
                    who = other.get("remark") or other.get("email")
                    raise ValueError(
                        f"这个浏览器窗口已经绑定给「{who}」了。"
                        "每个抢票人必须用各自独立的窗口，否则登录时会互相覆盖。"
                    )

        if acc_id:
            for existing in accounts:
                if existing["id"] == acc_id:
                    pwd = account.get("password") or ""
                    if pwd and pwd != "******":
                        existing["password"] = pwd
                    existing["email"] = email
                    existing["remark"] = (account.get("remark") or "").strip()
                    existing["browser_id"] = (account.get("browser_id") or "").strip()
                    # 会员优先购票码。选填——只有会员预售场用得上，
                    # 普通场次留空即可。跟密码一样是明文存的。
                    existing["member_code"] = (account.get("member_code") or "").strip()
                    _write(ACCOUNTS_FILE, accounts)
                    return existing
            raise ValueError(f"找不到 id 为 {acc_id} 的抢票人")

        password = account.get("password") or ""
        if not password:
            raise ValueError("密码不能为空")
        new_account = {
            "id": str(uuid.uuid4()),
            "email": email,
            "password": password,
            "remark": (account.get("remark") or "").strip(),
            "browser_id": (account.get("browser_id") or "").strip(),
            "member_code": (account.get("member_code") or "").strip(),
        }
        accounts.append(new_account)
        _write(ACCOUNTS_FILE, accounts)
        return new_account


def delete_account(acc_id):
    """删除一个抢票人，同时清掉他的抢票配置，避免留下孤儿数据。"""
    with _lock:
        accounts = _read(ACCOUNTS_FILE, [])
        remaining = [a for a in accounts if a["id"] != acc_id]
        if len(remaining) == len(accounts):
            return False
        _write(ACCOUNTS_FILE, remaining)

        plans = _read(PLANS_FILE, {})
        if acc_id in plans:
            del plans[acc_id]
            _write(PLANS_FILE, plans)
        return True


def delete_accounts(acc_ids):
    """
    批量删除抢票人，连带清掉他们的抢票配置。

    一次性写盘，不是循环调 delete_account——那样删 10 个人要读写 20 次文件，
    中途出错还会留下删一半的状态。

    :return: 实际删掉的个数
    """
    ids = set(acc_ids or [])
    if not ids:
        return 0

    with _lock:
        accounts = _read(ACCOUNTS_FILE, [])
        remaining = [a for a in accounts if a["id"] not in ids]
        removed = len(accounts) - len(remaining)
        if removed:
            _write(ACCOUNTS_FILE, remaining)
            plans = _read(PLANS_FILE, {})
            kept = {k: v for k, v in plans.items() if k not in ids}
            if len(kept) != len(plans):
                _write(PLANS_FILE, kept)
        return removed


def get_account(acc_id):
    for a in get_accounts():
        if a["id"] == acc_id:
            return a
    return None


# ------------------------------------------------------------------
# 第一步：解析留存的活动票务信息
# ------------------------------------------------------------------

def get_event():
    """返回上次解析留存的活动信息，没有则返回空结构。"""
    with _lock:
        return _read(EVENT_FILE, {})


def save_event(event):
    with _lock:
        _write(EVENT_FILE, event)
        return event


# ------------------------------------------------------------------
# 第二步：每个抢票人的抢票配置
# ------------------------------------------------------------------

def get_plans():
    """返回 {account_id: {session_text, tier_text, quantity}}"""
    with _lock:
        return _read(PLANS_FILE, {})


def save_plans(plans):
    """整表覆盖保存。前端每次提交的是完整配置，不做增量合并。"""
    with _lock:
        cleaned = {}
        for acc_id, plan in (plans or {}).items():
            cleaned[acc_id] = {
                "session_text": (plan.get("session_text") or "").strip(),
                "tier_text": (plan.get("tier_text") or "").strip(),
                "quantity": max(1, int(plan.get("quantity") or 1)),
            }
        _write(PLANS_FILE, cleaned)
        return cleaned


def get_plan(acc_id):
    return get_plans().get(acc_id, {})


# ------------------------------------------------------------------
# 窗口归属：某个比特浏览器窗口上一次登录的是谁
# ------------------------------------------------------------------
#
# 解决的问题：窗口比人少的时候要复用窗口（比如 10 个人 5 个窗口，分两批抢）。
# 复用时如果不清上一个人的登录态，会发生**静默串号**：
#   打开窗口 -> 检测到"已登录"（其实是上一个人）-> 跳过登录
#   -> 拿着 A 的账号去抢按 B 配置的票 -> 下单成功，但下到了 A 头上。
# 它不报错、不失败，比"两个密码填进同一个框"更难发现。
#
# 但也不能一律清：清了就要人工重过验证码，10 个人就是 10 次。
# 同一个人抢完这场接着抢下一场时清掉登录态，回流票那几十秒的窗口期就废了。
#
# 所以记一张归属表，只在**真的换人**时才清。

def get_window_owners() -> dict:
    """返回 {browser_id: {"email":..., "at": ISO时间}}"""
    with _lock:
        return _read(OWNERS_FILE, {})


def get_window_owner(browser_id: str):
    if not browser_id:
        return None
    return get_window_owners().get(browser_id)


def set_window_owner(browser_id: str, email: str):
    """记录某个窗口现在归谁。登录成功后调用。"""
    if not browser_id:
        return
    from datetime import datetime

    with _lock:
        owners = _read(OWNERS_FILE, {})
        owners[browser_id] = {
            "email": (email or "").strip(),
            "at": datetime.now().isoformat(timespec="seconds"),
        }
        _write(OWNERS_FILE, owners)


def clear_window_owner(browser_id: str):
    """清掉某个窗口的归属记录。清除登录态之后调用。"""
    if not browser_id:
        return
    with _lock:
        owners = _read(OWNERS_FILE, {})
        if browser_id in owners:
            del owners[browser_id]
            _write(OWNERS_FILE, owners)


# ------------------------------------------------------------------
# 抢票结果
# ------------------------------------------------------------------
#
# 这是整个流程真正的产出，也是唯一**不随启动清空**的数据。
#
# 为什么必须落盘：程序只锁单不付款，每个锁到的订单都在等人去付，而且有支付时限。
# 十个号并发抢完，谁抢到了、哪场、哪个档、订单号多少、还剩多久要付——
# 这些信息如果只存在于滚动过去的日志里，等于没有。漏付一单，前面全白做。
#
# 按「批次」分组：今天抢 A 活动、明天抢 B 活动，记录不该混在一起。
# 每次发起抢票生成一个批次，抢票结果按批次归档，导出可以只导某一批。

def _read_results() -> dict:
    data = _read(RESULTS_FILE, {})
    data.setdefault("batches", {})
    data.setdefault("items", [])
    return data


def start_batch(event: dict) -> str:
    """开抢时创建一个批次，返回 batch_id。"""
    from datetime import datetime

    batch_id = str(uuid.uuid4())
    with _lock:
        data = _read_results()
        data["batches"][batch_id] = {
            "batch_id": batch_id,
            "event_name": (event or {}).get("name") or "未知活动",
            "event_url": (event or {}).get("event_url") or "",
            "started_at": datetime.now().isoformat(timespec="seconds"),
        }
        _write(RESULTS_FILE, data)
    return batch_id


def add_result(batch_id: str, item: dict) -> dict:
    """
    记一条锁单成功。

    **订单号取不到也要记。** 抓取订单号依赖页面结构，站点改版就可能失效；
    但"这个人抢到了"这件事本身比订单号更重要，不能因为抓不到号就整条丢掉——
    那样用户会以为没抢到，直接错过付款。取不到时 order_id 为 None，
    界面上提示去对应窗口里自己看。
    """
    from datetime import datetime

    record = {
        "id": str(uuid.uuid4()),
        "batch_id": batch_id,
        "account_label": item.get("account_label") or "",
        "email": item.get("email") or "",
        "browser_id": item.get("browser_id") or "",
        "browser_seq": item.get("browser_seq"),
        "session_text": item.get("session_text") or "",
        "tier_text": item.get("tier_text") or "",
        "quantity": int(item.get("quantity") or 1),
        "order_id": item.get("order_id"),          # 可能是 None
        "page_url": item.get("page_url") or "",    # 兜底：留着人工去查
        "locked_at": datetime.now().isoformat(timespec="seconds"),
        # locked  已锁单，等人去付款（主要情况）
        # manual  到了确认订单页但没锁上（条款没勾上/有必填项），等人工处理
        # paid    已支付，人工标记
        # expired 已过期，人工标记
        "status": "locked",
    }
    with _lock:
        data = _read_results()
        data["items"].append(record)
        _write(RESULTS_FILE, data)
    return record


def get_results() -> dict:
    """返回全部抢票结果，items 按锁单时间倒序（最新的在最前面）。"""
    with _lock:
        data = _read_results()
    data["items"] = sorted(data["items"], key=lambda x: x.get("locked_at") or "", reverse=True)
    return data


def set_result_status(result_id: str, status: str) -> bool:
    """标记某条抢票结果的状态。付款是人工在浏览器里完成的，程序只能由人来标记。"""
    if status not in ("locked", "manual", "paid", "expired"):
        raise ValueError(f"未知状态：{status}")
    with _lock:
        data = _read_results()
        for it in data["items"]:
            if it["id"] == result_id:
                it["status"] = status
                _write(RESULTS_FILE, data)
                return True
    return False


def delete_batch(batch_id: str) -> int:
    """删掉一个批次及其全部记录，返回删掉的条数。"""
    with _lock:
        data = _read_results()
        kept = [i for i in data["items"] if i.get("batch_id") != batch_id]
        removed = len(data["items"]) - len(kept)
        data["items"] = kept
        data["batches"].pop(batch_id, None)
        _write(RESULTS_FILE, data)
        return removed


# ------------------------------------------------------------------
# 启动清理
# ------------------------------------------------------------------

def reset_runtime_data(keep_event: bool = False) -> dict:
    """
    清空运行时数据，让程序每次启动都是干净的。

    清掉的是**跟人绑定**的数据：抢票人（含账号密码）和他们的抢票配置。
    这两样是一次性的：这轮抢十个人的票，下轮换一批人，留着上一轮的残留
    只会带来误操作（比如拿旧账号去抢新活动）。

    :param keep_event: 是否保留解析好的活动信息。**默认不保留**——
        每次打开控制台，票务解析框应该是空的，而不是显示上一轮的活动链接。
        重新解析只要一个 HTTP 请求，成本很低。调试时可传 True 省掉重复解析。

    为什么在**启动时**清而不是退出时清：
        退出钩子在 kill -9、进程崩溃、断电时都不会执行，靠不住；
        启动时清是必然执行的，达到的效果一样（下次启动是干净的）。

    :return: 各项清理结果，供启动日志打印
    """
    cleared = {}
    with _lock:
        accounts = _read(ACCOUNTS_FILE, [])
        cleared["accounts"] = len(accounts)
        _write(ACCOUNTS_FILE, [])

        plans = _read(PLANS_FILE, {})
        cleared["plans"] = len(plans)
        _write(PLANS_FILE, {})

        # 归属记录跟着抢票人一起清。清完就是"无记录"状态——
        # 而登录时的规则是「无记录但窗口检测到已登录 → 也清」，
        # 正好兜住"程序崩溃重启后，窗口里还留着上一轮某个人登录态"这种情况。
        owners = _read(OWNERS_FILE, {})
        cleared["owners"] = len(owners)
        _write(OWNERS_FILE, {})

        # **results.json 刻意不清。** 它是整个流程的产出，不是运行时状态：
        # 锁到的订单还等着人去付款，清掉就等于把付款凭据丢了。
        # 要清抢票结果只能在界面上按批次手动删。

        if keep_event:
            cleared["event"] = "保留"
        else:
            event = _read(EVENT_FILE, {})
            cleared["event"] = "已清空" if event else "本来就没有"
            _write(EVENT_FILE, {})
    return cleared
