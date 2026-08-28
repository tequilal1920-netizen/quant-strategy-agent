"""Build a reproducible visual-grammar inventory from WarrenQ broker reports.

The report PDFs are downloaded only to ``tmp/pdfs`` for inspection.  The
durable artifact contains metadata, extracted figure/table captions, visual
page statistics and a page selected for human visual review.  It deliberately
does not copy report images or report prose into the product.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pdfplumber
from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WARREN_ROOT = Path(
    r"C:\Users\Rye\Desktop\Program\ELSE\SHH\research_learning_framework_20260717"
)
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "framework"
    / "reference_artifacts"
    / "broker_report_visual_grammar_20260727.json"
)
TEMP_ROOT = PROJECT_ROOT / "tmp" / "pdfs"

REPORT_TITLES: dict[str, list[str]] = {
    "资产配置": [
        "BL宏观量化策略模型主动配置展望(202503)：A股配置价值凸显 金融风格有望回归",
        "2025年资产配置及量化策略展望：财政政策与经济周期的对抗与统一",
        "大类资产配置研究系列(14)：权益择时的多策略框架-从宏观驱动到微观验证",
        "量化分析报告：六周期框架下的多资产ETF配置",
        "量化专题报告：中国经济六周期模型与多资产策略应用",
        "大类资产配置量化模型研究系列之三：桥水全天候策略和风险平价模型全解析",
        "基于宏观风险因子的资产配置：全天候宏观风险平价模型",
        "华西金工全天候资产配置框架之一：风险平价模型风险测度探讨",
        "大类资产配置择时方法：隐马尔可夫市场状态识别方法",
        "隐马尔可夫市场状态识别方法：大类资产 配置择时",
    ],
    "资金面跟踪": [
        "海外ETF资金流因子影响下的行业风格轮动策略：当前环境下如何日频跟踪外资“聪明钱”？",
        "跟踪资金流系列专题(四)：基于ETF持有人结构资金流因子的行业轮动策略",
        "跟踪资金流系列专题(三)：早提示拥挤风险 高胜率左侧止盈：基于ETF建仓资金流因子的行业轮动策略",
        "宏观视角：复盘历史上的央行资本市场流动性工具",
        "银行业存款搬家历史复盘：宽货币铺路 关注实体修复进程",
        "北交所策略专题报告：从融资融券解析北交所的情绪变化和资金变化",
        "A股流动性与风格跟踪月报：均衡配置 重回哑铃策略",
    ],
    "行业风格轮动": [
        "量化资产配置系列报告之五：成长价值、大小盘风格轮动规律与策略展望",
        "重构量化行业轮动框架：宏观篇(下)：多维宏观状态下的行业轮动策略",
        "情景模拟、一般规律、未来预测与配置策略：如何从容应对不同的行业轮动速度？",
        "量化点评报告：行业ETF轮动模型2025年超额9.3%",
        "量化策略研究之七：周频多因子行业轮动模型",
        "基于量化多因子的行业配置策略之三：机器学习算法下的行业轮动",
        "金融工程研究：DFQ机器学习行业轮动模型",
        "行业风格轮动及公募指基月报(2024年8月期)：维持大盘风格 聚焦新质生产力+周期",
    ],
    "K线与量价学习": [
        "智能量化：量价因子策略库",
        "量化深度：量价因子策略库",
        "“量价淘金”选股因子系列研究(三)：如何基于RSI技术指标构建有效的选股因子？",
        "AI系列研究之四：混合频率量价因子模型初探",
        "量化专题报告：深度学习模型如何控制策略风险？",
        "权益配置因子研究09：基于GRU、TCN模型的深度学习因子选股效果研究",
        "因子选股系列之一一六：NEURALODE：时序动力系统重构下深度学习因子挖掘模型",
    ],
    "因子实验室": [
        "量化研究参考系列之九：大语言模型驱动因子挖掘的模型演进与框架梳理",
        "量化选股系列报告之十四：因子分域初探：确定分域方式",
        "多因子选股(十九)：因子合成的动量到反转：从多策略配置反推多因子合成",
        "LLM赋能资产配置：基于新闻数据的AI宏观因子构建与应用",
        "AI辅助投研深度案例系列之二：大模型赋能情绪、政策指标构建",
        "ALPHA掘金系列之五：如何利用CHATGPT挖掘高频选股因子？",
        "ALPHA掘金系列之十一：基于BERT-TEXTCNN的中证1000舆情增强策略",
        "以空间换时间：多目标基本面选股因子挖掘框架",
        "因子选股系列之一一五：DFQ-DIVERSIFY：解决分布外泛化问题的自监督领域识别与对抗解耦模型",
    ],
    "组合优化与指数增强": [
        "量化策略演进手记系列之一：中证500指数增强超额难度提升 传统多因子框架如何应对？",
        "中银证券量化多因子选股系列(七)：指数增强组合优化器：从零构建全攻略",
        "基金经理人物画像系列之七：新华沪深300指数增强产品投资价值分析：以多因子模型为总框架 大盘与量化增强大显身手",
        "金融工程专题报告：公司治理专题报告系列二：基于多因子框架的中证500指数增强模型",
        "权益配置因子研究系列06：基于BARRA CNE6的A股风险模型实践：风险因子篇",
        "多因子模型：基于行业相关性因子的指数增强策略",
        "金工量化专题报告：BARRA(CNE6)长期投资风险模型的复现及应用(上)",
        "金工量化专题报告：BARRA(CNE6)长期投资风险模型的复现及应用(下)",
        "金融工程专题报告：主动暴露的得与失—从Barra框架到私募指增因子分析方法",
    ],
}

EXTRA_REPORTS = [
    {
        "module": "资产配置",
        "title": "AI视角驱动的Black-Litterman资产配置",
        "organ": "国信证券",
        "date": "2026-01-12",
        "url": "https://pdf.dfcfw.com/pdf/H3_AP202601121816952139_1.pdf",
    },
    {
        "module": "K线与量价学习",
        "title": "AI预测股价指南：以TrendIQ为例",
        "organ": "国信证券",
        "date": "2025-12-03",
        "url": "https://pdf.dfcfw.com/pdf/H3_AP202512031793281951_1.pdf",
    },
    {
        "module": "K线与量价学习",
        "title": "深度学习系列之二：绝对收益视角下的技术形态专家模型-选股择时与多资产轮动的统一框架",
        "organ": "东吴证券",
        "date": "2026-03-24",
        "url": "https://cloud.gildata.com/queryservice/research/attachment/827696785997.pdf",
    },
    {
        "module": "因子实验室",
        "title": "深度学习系列之一：AI重塑量化 基于大语言模型驱动的因子改进与情绪ALPHA挖掘",
        "organ": "东吴证券",
        "date": "2026-01-10",
        "url": "https://cloud.gildata.com/queryservice/research/attachment/821383980303.pdf",
    },
]

CAPTION_RE = re.compile(
    r"^\s*(?:图表|图|表)\s*[0-9一二三四五六七八九十百A-Za-z\-\.：:]*\s*[^。]{2,90}$"
)

ARCHETYPE_RULES = {
    "机理流程图": re.compile(r"框架|流程|体系|架构|网络结构|模型结构|传导"),
    "状态时序图": re.compile(r"走势|时序|历史|周期|状态|信号|净值|回撤"),
    "截面比较图": re.compile(r"排名|分组|截面|暴露|分位|分层|行业|风格"),
    "条件矩阵表": re.compile(r"情景|矩阵|状态下|不同.*环境|分组收益|年度收益"),
    "模型诊断图": re.compile(r"预测|拟合|误差|IC|相关|稳定|衰减|重要性|损失"),
    "组合归因图": re.compile(r"归因|贡献|风险贡献|超额|换手|成本|胜率"),
    "优化求解图": re.compile(r"有效前沿|约束|权重|优化器|风险预算|风险平价"),
}


def load_records() -> list[dict[str, Any]]:
    selected = json.loads(
        (WARREN_ROOT / "warrenq_selected.json").read_text(encoding="utf-8")
    )["records"]
    fulltext = json.loads(
        (WARREN_ROOT / "warrenq_fulltext_sample.json").read_text(encoding="utf-8")
    )["reports"]
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for row in [*selected, *fulltext]:
        title = str(row.get("title") or "").strip()
        url = str(row.get("url") or row.get("file_url") or "").split("?")[0]
        if not title or not url.lower().endswith(".pdf"):
            continue
        merged[(title, url)] = {
            "title": title,
            "organ": row.get("organ") or "",
            "date": row.get("date") or "",
            "url": url,
            "warrenq_queries": row.get("queries") or [row.get("query")],
            "warrenq_score": row.get("score", row.get("selection_score")),
        }
    return list(merged.values())


def selected_reports() -> list[dict[str, Any]]:
    records = load_records()
    by_title: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        by_title[row["title"]].append(row)
    result: list[dict[str, Any]] = []
    for module, titles in REPORT_TITLES.items():
        for title in titles:
            matches = by_title.get(title) or []
            if not matches:
                result.append(
                    {
                        "module": module,
                        "title": title,
                        "status": "metadata_missing",
                    }
                )
                continue
            row = max(
                matches,
                key=lambda item: (
                    int(item.get("warrenq_score") or 0),
                    str(item.get("date") or ""),
                ),
            )
            result.append({**row, "module": module, "status": "pending"})
    result.extend({**row, "status": "pending"} for row in EXTRA_REPORTS)
    return result


def download(url: str, target: Path) -> None:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/136 Safari/537.36"
            )
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        target.write_bytes(response.read())
    if target.stat().st_size < 10_000 or target.read_bytes()[:4] != b"%PDF":
        raise ValueError("download_is_not_a_pdf")


def page_statistics(
    pdf_path: Path, max_pages: int = 80
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    captions: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_number, page in enumerate(pdf.pages[:max_pages], 1):
            text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
            lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
            page_captions = [line for line in lines if CAPTION_RE.match(line)]
            captions.extend(page_captions)
            rows.append(
                {
                    "page": page_number,
                    "characters": len(text),
                    "images": len(page.images),
                    "rectangles": len(page.rects),
                    "lines": len(page.lines),
                    "captions": page_captions[:12],
                }
            )
    return rows, captions


def choose_visual_page(rows: list[dict[str, Any]]) -> int:
    candidates = [
        row
        for row in rows
        if row["page"] > 2 and (row["images"] or row["captions"])
    ] or rows[2:] or rows
    selected = max(
        candidates,
        key=lambda row: (
            min(row["images"], 8) * 8
            + min(len(row["captions"]), 4) * 6
            + min(row["rectangles"], 40) * 0.15
            + min(row["lines"], 60) * 0.08
            + min(row["characters"], 2500) / 1000
        ),
    )
    return int(selected["page"])


def classify_captions(captions: list[str]) -> dict[str, int]:
    counts = Counter()
    text = "\n".join(captions)
    for name, rule in ARCHETYPE_RULES.items():
        counts[name] = len(rule.findall(text))
    return dict(counts)


def render_page(pdf_path: Path, page: int, output_prefix: Path) -> Path:
    command = [
        "pdftoppm",
        "-f",
        str(page),
        "-l",
        str(page),
        "-r",
        "100",
        "-png",
        "-singlefile",
        str(pdf_path),
        str(output_prefix),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)
    return output_prefix.with_suffix(".png")


def build_contact_sheet(module: str, images: list[tuple[Path, str]], target: Path) -> None:
    if not images:
        return
    thumb_width, thumb_height = 520, 740
    label_height = 82
    columns = 3
    rows = (len(images) + columns - 1) // columns
    sheet = Image.new(
        "RGB",
        (columns * thumb_width, rows * (thumb_height + label_height)),
        "white",
    )
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, (path, label) in enumerate(images):
        image = Image.open(path).convert("RGB")
        image.thumbnail((thumb_width - 12, thumb_height - 12))
        left = (index % columns) * thumb_width + (thumb_width - image.width) // 2
        top = (index // columns) * (thumb_height + label_height) + 6
        sheet.paste(image, (left, top))
        wrapped = [label[i : i + 40] for i in range(0, min(len(label), 120), 40)]
        draw.multiline_text(
            ((index % columns) * thumb_width + 8, top + thumb_height),
            "\n".join(wrapped),
            fill="black",
            font=font,
            spacing=3,
        )
    draw.text((10, 5), module, fill="#c00000", font=font)
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, quality=90)


def process_report(index: int, report: dict[str, Any]) -> dict[str, Any]:
    if report.get("status") == "metadata_missing":
        return report
    stem = f"{index:02d}_{report['module']}"
    pdf_path = TEMP_ROOT / f"{stem}.pdf"
    try:
        if not pdf_path.exists():
            download(str(report["url"]), pdf_path)
        rows, captions = page_statistics(pdf_path)
        visual_page = choose_visual_page(rows)
        png_path = render_page(
            pdf_path, visual_page, TEMP_ROOT / f"{stem}_p{visual_page}"
        )
        report.update(
            {
                "status": "inspected",
                "bytes": pdf_path.stat().st_size,
                "pages_inspected": len(rows),
                "caption_count": len(captions),
                "caption_sample": captions[:30],
                "visual_archetype_counts": classify_captions(captions),
                "visual_review_page": visual_page,
                "visual_review_png": str(png_path),
                "visual_page_statistics": rows[visual_page - 1],
            }
        )
    except Exception as exc:  # noqa: BLE001
        report.update(
            {
                "status": "failed",
                "error": f"{type(exc).__name__}:{exc}"[:300],
            }
        )
    return report


def worker(index: int) -> None:
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    reports = selected_reports()
    report = process_report(index, reports[index - 1])
    part = TEMP_ROOT / f"part_{index:02d}.json"
    part.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{index}/{len(reports)}] {report['status']} {report['title']}", flush=True)


def run(
    output: Path,
    keep_temp: bool = False,
    limit: int | None = None,
    per_report_timeout: int = 120,
) -> None:
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    reports = selected_reports()
    if limit:
        reports = reports[:limit]
    completed: list[dict[str, Any]] = []
    visual_images: dict[str, list[tuple[Path, str]]] = defaultdict(list)
    for index, report in enumerate(reports, 1):
        part = TEMP_ROOT / f"part_{index:02d}.json"
        try:
            subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--worker-index", str(index)],
                check=True,
                timeout=per_report_timeout,
                capture_output=True,
                text=True,
            )
            report = json.loads(part.read_text(encoding="utf-8"))
        except subprocess.TimeoutExpired:
            report.update(
                {
                    "status": "failed",
                    "error": f"TimeoutExpired:single_report_exceeded_{per_report_timeout}s",
                }
            )
        except Exception as exc:  # noqa: BLE001
            report.update(
                {
                    "status": "failed",
                    "error": f"{type(exc).__name__}:{exc}"[:300],
                }
            )
        completed.append(report)
        png = Path(str(report.get("visual_review_png") or ""))
        if report.get("status") == "inspected" and png.exists():
            visual_images[report["module"]].append(
                (
                    png,
                    f"{report['organ']} | {report['title']} | p.{report['visual_review_page']}",
                )
            )
        print(f"[{index}/{len(reports)}] {report['status']} {report['title']}", flush=True)

    contact_sheets = {}
    for module, images in visual_images.items():
        target = TEMP_ROOT / f"contact_{module}.png"
        build_contact_sheet(module, images, target)
        contact_sheets[module] = str(target)

    payload = {
        "schema_version": "broker-report-visual-grammar/1.0",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "selection_policy": (
            "WarrenQ direct-PDF broker reports, predominantly 2021+, plus four "
            "public direct-PDF additions; visual grammar only, no copied images "
            "or prose enters the product."
        ),
        "report_count": len(completed),
        "inspected_count": sum(row.get("status") == "inspected" for row in completed),
        "module_counts": dict(Counter(row["module"] for row in completed)),
        "contact_sheets": contact_sheets,
        "reports": completed,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if not keep_temp:
        shutil.rmtree(TEMP_ROOT, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--keep-temp", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--per-report-timeout", type=int, default=120)
    parser.add_argument("--worker-index", type=int)
    args = parser.parse_args()
    if args.worker_index:
        worker(args.worker_index)
        return
    run(
        args.output,
        keep_temp=args.keep_temp,
        limit=args.limit,
        per_report_timeout=args.per_report_timeout,
    )


if __name__ == "__main__":
    main()
