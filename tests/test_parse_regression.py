"""
票务解析回归测试。

这个项目的解析逻辑已经因为"判断错票的有无"改过四轮，每一轮都是线上踩了坑才发现的。
失败方式有两种，都很恶劣：

    有票报成没票 —— 用户以为抢不了，直接放弃（最糟，因为看起来像"正常结论"）
    没票报成有票 —— 白跑一轮，还平白多打一遍站点

所以每个踩过的坑都必须在这里有一条用例。**新遇到问题时先加样本再改代码**：
只改代码不加样本，下一次数据形态变化就会把同一个坑再踩一遍。

跑法：
    ./venv/bin/python tests/test_parse_regression.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.Auto.ticket_operation import _find_index_by_text, _normalize_text  # noqa: E402
from app.Auto.ticket_parser import _extract_event  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

_results = []


def check(name, got, want, note=""):
    ok = got == want
    _results.append(ok)
    mark = "PASS" if ok else "FAIL"
    line = f"  {mark}  {name}"
    if not ok:
        line += f"\n        实际={got!r}  期望={want!r}"
    if note:
        line += f"\n        （{note}）"
    print(line)


def load(fname):
    with open(os.path.join(FIXTURES, fname), encoding="utf-8") as f:
        return _extract_event(json.load(f), "https://hkt.hkticketing.com/#/x?projectId=1")


# ------------------------------------------------------------------

def case_整场停售():
    """
    坑 1：只看票档级 sellOut 会把整场停售的活动报成有票。

    汪苏泷 2026 香港站实测：saleState=3（整场停售），18 个票档里有 5 个
    sellOut=false，但实际一张都买不到。当时的逻辑只看 sellOut，报成"有票"，
    抢票循环白跑。

    规则：场次 saleState != 2 时，票档说什么都不算数。
    """
    print("\n[坑1] 整场停售，但部分票档 sellOut=false")
    ev = load("整场停售但票档sellOut为false.json")
    s = ev["sessions"][0]
    check("场次标记为售罄", s["sell_out"], True)
    check("没有任何票档被判为可售", sum(1 for t in s["tiers"] if t["available"]), 0,
          "saleState=3 时票档 sellOut=false 也不算数")


def case_canAddCart():
    """
    坑 2：用 canAddCart 判断可买性，会把有票的活动报成全部售罄。

    EUNHYUK FANCON 实测：saleState=2、票档 sellOut=false、但 canAddCart 全是
    false，页面上票档明明可选、「下一步」也是亮的。canAddCart 是"能否加入购物车"，
    不是"能否购买"。当时报成"全部售罄"，用户看到的是一个像模像样的错误结论。
    """
    print("\n[坑2] 有票，但 canAddCart 全 false")
    ev = load("有票但canAddCart全false.json")
    s = ev["sessions"][0]
    check("场次不算售罄", s["sell_out"], False)
    check("全部票档可售", sum(1 for t in s["tiers"] if t["available"]), len(s["tiers"]),
          "canAddCart 与可买性无关，不能拿来判断")


def case_尾随空格():
    """
    坑 3：eventCaption 带尾随空格，页面元素上没有，直接拿去匹配会匹配不上。

    实测「2026年9月4日 (星期五) 晚上7时  」末尾有两个空格，抢票时报
    "未找到匹配场次"，看着像选择器坏了。
    """
    print("\n[坑3] 场次名带尾随空格")
    ev = load("场次名带尾随空格.json")
    text = ev["sessions"][0]["text"]
    check("解析结果已 strip", text, "2026年9月4日 (星期五) 晚上7时")
    check("末尾无空白", text == text.strip(), True)


def case_会员预售父子():
    """
    坑 4a：会员预售配置期，接口返回 12 个票档，页面只显示 6 个。

    会员档是"父"（带 childPriceIds 指向普通档），页面上不单独显示。
    留着它们会让人配出一个永远抢不到的方案（抢票时一直报"未找到票档"）。
    """
    print("\n[坑4a] 会员预售父子结构（12 个票档，页面只显示 6 个）")
    ev = load("会员预售_父子结构.json")
    s = ev["sessions"][0]
    check("过滤掉会员父项，留 6 个", len(s["tiers"]), 6)
    check("留下的都不是包装层", all(not t["is_wrapper"] for t in s["tiers"]), True)
    check("留下的都不含「会员预售」字样",
          all("会员预售" not in t["name"] for t in s["tiers"]), True)
    check("限购取到 eventOrderLimit", s["order_limit"], 2)
    check("会员档存在时普通档标记需会员码",
          all(t["need_member_code"] for t in s["tiers"]), True,
          "靠 accessCodeMultipleState，不是 isPermission")


def case_公开发售真实数据():
    """
    坑 4b：同一活动开卖后，接口形态变了——只剩 6 个票档且 isPermission 全为 True。

    上一版的规则是「只保留 isPermission != True 的」，在这份数据下**一个都留不下**，
    界面显示"全部售罄"。这是从站点抓的真实数据，也是这一条最有价值的地方。

    页面上的地面真相（2026-08-26 晚在登录状态下核对过）：
        6 个票档，E/标准门票 (看台座位) 售罄（带 disableClass + 「暂无可售」），
        其余 5 个可售。
    """
    print("\n[坑4b] 公开发售中的真实数据（isPermission 全 True）")
    ev = load("nct127_公开发售中.json")
    check("2 个场次", len(ev["sessions"]), 2)
    for s in ev["sessions"]:
        check(f"「{s['text'][:14]}…」有 6 个票档", len(s["tiers"]), 6,
              "isPermission 全 True 时一个都不能过滤掉")
        avail = [t for t in s["tiers"] if t["available"]]
        check(f"「{s['text'][:14]}…」5 个可售", len(avail), 5,
              "与页面核对：E/标准门票(看台座位) 售罄，其余 5 个可售")
        sold = [t for t in s["tiers"] if not t["available"]]
        check(f"「{s['text'][:14]}…」售罄的是 E 档",
              all("E/" in t["name"] for t in sold), True)
        # 公售期间不能标"需会员码"。这是同类错误的第三次：
        # 先用 canAddCart 判可买性、再用 isPermission 判页面显示、
        # 又用 isPermission 判会员码——每次都是拿一个字段在一份快照上推结论。
        # 公售中 isPermission 全是 True，但页面上会员图标全消失、根本不用码。
        check(f"「{s['text'][:14]}…」不标需会员码",
              any(t["need_member_code"] for t in s["tiers"]), False,
              "公售中 isPermission 仍为 True，但不需要会员码——不能用它判断")


def case_安全网():
    """
    最重要的一条：**过滤规则绝不允许把票档清空**。

    关于页面结构的启发式本来就可能过时，它只该锦上添花，不该有能力把一个健康的
    活动变成空的。"明明有票却报没票"比报错更糟——用户完全无从判断是真没票
    还是程序坏了。这一条是拿坑 4b 换来的，不能再丢。
    """
    print("\n[安全网] 过滤规则不能把票档清空")
    data = {
        "projectName": "全是包装层", "projectToken": "t",
        "eventVoList": [{
            "eventCaption": "场次A", "eventToken": "e", "saleState": 2,
            "sellOut": False, "eventOrderLimit": 4,
            "priceVoList": [
                {"priceId": f"w{i}", "priceName": f"包装{i}", "price": "100",
                 "sellOut": False, "isPermission": True, "childPriceIds": ["x"]}
                for i in range(3)
            ],
        }],
    }
    s = _extract_event(data, "u")["sessions"][0]
    check("全是包装层时也要保留", len(s["tiers"]), 3,
          "宁可多显示几个，也不能一个不剩")


def case_页面文本匹配():
    """
    坑 5：页面用 &nbsp; 分隔名称和价格，还挂了「站」角标，逐字符匹配全军覆没。

    NCT 127 实测：接口拼出 'B/标准门票 (企位) (HK$ 1799.00)'，
    页面是 '站B/标准门票 (企位)\\xa0(HK$ 1799.00)'。差一个字符，6 个票档一个都匹配不上。
    """
    print("\n[坑5] 页面 &nbsp; + 「站」角标导致文本匹配失败")
    页面 = [
        "站A/VIP标准门票 (企位)\xa0(HK$ 2199.00)",
        "站B/标准门票 (企位)\xa0(HK$ 1799.00)",
        "B/标准门票 (看台座位)\xa0(HK$ 1799.00)",
        "E/标准门票 (看台座位)\xa0(HK$ 899.00)暂无可售",
    ]

    def find(want):
        t = _normalize_text(want)
        for i, p in enumerate(页面):
            if t and t in _normalize_text(p):
                return i
        return -1

    check("企位档匹配到正确的一项", find("B/标准门票 (企位) (HK$ 1799.00)"), 1)
    check("看台档不会被企位档误匹配", find("B/标准门票 (看台座位) (HK$ 1799.00)"), 2)
    check("带角标的也能匹配", find("A/VIP标准门票 (企位) (HK$ 2199.00)"), 0)
    check("售罄档带额外文字也能匹配", find("E/标准门票 (看台座位) (HK$ 899.00)"), 3)
    check("不存在的票档返回 -1", find("Z/不存在 (HK$ 1.00)"), -1)
    # 旧行为对照：逐字符包含匹配在这些数据上是全灭的
    check("旧的逐字符匹配确实一个都匹配不上",
          any("B/标准门票 (企位) (HK$ 1799.00)" in p for p in 页面), False,
          "这条用来说明为什么必须做规范化")


if __name__ == "__main__":
    print("=" * 64)
    print("票务解析回归测试")
    print("=" * 64)
    for fn in (case_整场停售, case_canAddCart, case_尾随空格,
               case_会员预售父子, case_公开发售真实数据,
               case_安全网, case_页面文本匹配):
        fn()

    print("\n" + "=" * 64)
    passed, total = sum(_results), len(_results)
    print(f"{passed} / {total} 通过")
    sys.exit(0 if passed == total else 1)
