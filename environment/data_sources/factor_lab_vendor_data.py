"""因子实验室长期数据源适配与审计合同。

本文件只描述供应商优先级、包可用性、环境变量开关、限额和缓存策略；
绝不保存账号、密码、token、cookie 或数据库连接串。
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any


PROVIDER_PRIORITY = [
    {
        "id": "wind_sql",
        "中文名": "Wind 量化研究/财富管理数据库",
        "优先级": 1,
        "定位": "权威主源；优先用于行情、估值、财务、指数成分、行业、停牌涨跌停等核心字段补全",
        "凭据环境变量": ["WIND_SQL_UID", "WIND_SQL_PWD"],
        "启用变量": "FACTOR_MINING_ENABLE_PAID_PROBES",
        "限额策略": "默认只做表级/字段级/样本日期探针；批量抽取必须显式设置运行开关并落本地缓存",
    },
    {
        "id": "ifind_quantapi",
        "中文名": "同花顺 iFinD QuantAPI",
        "优先级": 2,
        "定位": "权威补充源；适合事件、资金、行业、专题标签和 Wind 缺口核验",
        "凭据环境变量": ["IFIND_ACCESS_TOKEN", "IFIND_REFRESH_TOKEN"],
        "启用变量": "FACTOR_MINING_ENABLE_PAID_PROBES",
        "限额策略": "默认关闭高频调用；只允许小样本元数据探针、断点续传和本地缓存命中优先",
    },
    {
        "id": "ricequant_rqdata",
        "中文名": "米筐 RQData/RQFactor",
        "优先级": 3,
        "定位": "因子库、指数成分、复权行情和组合优化参数补充源",
        "凭据环境变量": ["RQDATA_LICENSE", "RQDATAC2_CONF", "RQDATAC_LICENSE"],
        "启用变量": "FACTOR_MINING_ENABLE_RQDATA_PROBES",
        "限额策略": "许可证只从本机环境读取；优先同步到本地 SQLite/Parquet 后供模型复用",
    },
    {
        "id": "tushare_pro",
        "中文名": "Tushare Pro",
        "优先级": 4,
        "定位": "公共/半公共补充源；适合日行情、复权、财务基础字段、指数日线校验",
        "凭据环境变量": ["TUSHARE_TOKEN"],
        "启用变量": "FACTOR_MINING_ENABLE_PUBLIC_API_REFRESH",
        "限额策略": "按接口积分和日期窗口限流；不得在网页请求中同步大批量刷新",
    },
    {
        "id": "akshare_baostock",
        "中文名": "AKShare / baostock",
        "优先级": 5,
        "定位": "开放源兜底；用于公开行情、指数、宏观和字段交叉校验",
        "凭据环境变量": [],
        "启用变量": "FACTOR_MINING_ENABLE_PUBLIC_API_REFRESH",
        "限额策略": "只做缺口修复和抽样核验；大表先写缓存再入仓库",
    },
]


UPDATE_CONTRACT = {
    "主路径": "外部源 -> 原始缓存层 -> 标准化 staging -> research_warehouse.db -> 因子暴露面板",
    "刷新频率": {
        "行情成交": "交易日收盘后增量刷新",
        "估值资金": "交易日收盘后增量刷新",
        "财务披露": "按 visible_date 点时写入，严禁用报告期倒灌",
        "指数成分行业": "按生效区间维护，回测只使用当时可见成分",
        "新闻事件专题": "按 publish_date 点时写入，文本只转特征不进标签",
    },
    "质量门": [
        "字段覆盖率、日期连续性、重复键、异常值、停牌涨跌停可交易性",
        "复权一致性、成交额非负、行业/指数成分生效区间不穿越",
        "财报 visible_date 不晚于使用日且不得引用未来公告",
        "供应商交叉校验只写审计摘要，不把密钥或原始会话写入仓库",
    ],
    "缓存策略": "所有付费源先落本地只读缓存并记录水位；模型训练只读仓库，不直接高频访问 API",
}


def _package_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _env_present(name: str) -> bool:
    return bool(os.environ.get(name))


def audit_vendor_data_layer(project_root: str | Path | None = None) -> dict[str, Any]:
    root = Path(project_root or Path.cwd())
    package_status = {
        "pyodbc": _package_available("pyodbc"),
        "rqdatac": _package_available("rqdatac"),
        "tushare": _package_available("tushare"),
        "akshare": _package_available("akshare"),
        "baostock": _package_available("baostock"),
    }
    providers = []
    for item in PROVIDER_PRIORITY:
        secret_names = item["凭据环境变量"]
        providers.append({
            **item,
            "凭据已配置": all(_env_present(name) for name in secret_names) if secret_names else True,
            "已配置变量名": [name for name in secret_names if _env_present(name)],
            "未配置变量名": [name for name in secret_names if not _env_present(name)],
            "运行开关已打开": os.environ.get(item["启用变量"], "0") == "1",
        })
    return {
        "status": "ready",
        "secret_policy": "所有凭据只从环境变量或本机私密配置读取；不得写入代码、JSON、日志、网页、Release 或 GitHub。",
        "project_root": str(root),
        "provider_priority": providers,
        "package_status": package_status,
        "update_contract": UPDATE_CONTRACT,
        "default_runtime_policy": {
            "模型训练": "只读本地 research_warehouse.db 和缓存，不直接打付费接口",
            "缺口修复": "按供应商优先级小批量增量刷新，必须记录水位和字段审计",
            "额度保护": "Wind/iFinD/米筐默认仅允许元数据和小样本探针；批量刷新需显式运行开关",
        },
    }
