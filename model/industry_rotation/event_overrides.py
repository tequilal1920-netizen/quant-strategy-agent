"""Executable v4 release with cached core events and five small gap queries."""

from __future__ import annotations

import re

import engine as worker
import event_cache as release


EVENTS = dict(release.ROBUST_EVENTS)
EVENTS.update({
    "纺织服饰": [("纺织服装出口订单事件", ["服装出口", "纺织订单"])],
    "公用事业": [("火电机组运营事件", ["火电"]), ("水电来水发电事件", ["水电"]), ("燃气供应负荷事件", ["燃气供应"]), ("水务供水运营事件", ["水务", "供水"])],
    "商贸零售": [("电商平台促销动销事件", ["电商", "促销"])],
    "社会服务": [("旅游客流恢复事件", ["旅游", "游客"]), ("酒店入住经营事件", ["酒店", "入住率"])],
    "汽车": [("新能源汽车交付事件", ["新能源汽车", "新能源车"]), ("汽车出口订单事件", ["汽车出口", "海外销量"])],
})
GAP_INDUSTRIES = {"纺织服饰", "公用事业", "商贸零售", "社会服务", "汽车"}


_original_select = worker._select_direct_contracts
_original_event_rows = worker._event_rows


def _select(frames):
    selected = _original_select(frames)
    for items in selected.values():
        for item in items:
            item.name = re.sub(r"_+", " · ", item.observation_field.replace("原保险保费收入", "原保险保费规模"))
    return selected


def _event_rows(industry, blueprints):
    if industry in GAP_INDUSTRIES:
        return _original_event_rows(industry, blueprints)
    return release._event_rows(industry, blueprints)


def main() -> int:
    worker.EVENT_BLUEPRINTS = EVENTS
    worker._event_rows = _event_rows
    worker._select_direct_contracts = _select
    return worker.main()


if __name__ == "__main__":
    raise SystemExit(main())
