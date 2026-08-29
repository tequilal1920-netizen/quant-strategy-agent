from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FRONTENDS = (
    ROOT / "quant_strategy_agent" / "static" / "js" / "app.js",
    ROOT / "quant_strategy_agent_vnext" / "static" / "js" / "app.js",
)
STATIC_DIRS = (
    ROOT / "quant_strategy_agent" / "static" / "asset_allocation_figures",
    ROOT / "quant_strategy_agent_vnext" / "static" / "asset_allocation_figures",
)
CURRENT_TEMPLATE = ROOT / "quant_strategy_agent" / "templates" / "index.html"
CANONICAL_TEMPLATE = ROOT / "quant_strategy_agent" / "templates" / "index_rotation_factor_lab.html"
START = "  /* Asset allocation schema 5.0: conditional evidence contract; v4 renderers remain unchanged. */"
END = "  /* r27: common-window data-quality repair */"


class AssetAllocationV65VisualContractTests(unittest.TestCase):
    def _text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def test_formal_and_vnext_share_the_asset_allocation_contract(self):
        blocks = []
        for path in FRONTENDS:
            text = self._text(path)
            start = text.index(START)
            end = text.index(END, start)
            blocks.append(text[start:end])
        self.assertEqual(blocks[0], blocks[1])

    def test_left_nav_and_workspace_routes_match_latest_framework(self):
        expected = (
            "allocation:{title:'资产配置',views:{cycle:'周期跟踪',strategy:'资产配置'}}",
            "label:'美林时钟',kind:'allocation',page:'cycle_merrill'",
            "label:'普林格周期',kind:'allocation',page:'cycle_pring'",
            "label:'BL模型',kind:'allocation',page:'strategy_bl'",
            "label:'宏观因子模型',kind:'allocation',page:'strategy_macro'",
            "label:'风险预算模型',kind:'allocation',page:'strategy_risk'",
            "'allocation:home':['allocation:strategy','bl']",
            "'allocation:backtest':['allocation:strategy','risk']",
        )
        for path in FRONTENDS:
            text = self._text(path)
            for token in expected:
                self.assertIn(token, text)
        self.assertIn('data-target="allocation:strategy">资产配置', self._text(CURRENT_TEMPLATE))
        self.assertIn('data-target="allocation:strategy">资产配置', self._text(CANONICAL_TEMPLATE))

    def test_v65_render_dispatches_all_requested_third_level_pages(self):
        expected = (
            "allocationCycleV65(data,'merrill')",
            "allocationCycleV65(data,'pring')",
            "allocationStrategyV65(data,'bl')",
            "allocationStrategyV65(data,'macro')",
            "allocationStrategyV65(data,'risk')",
            "图表净值为日度回放，目标权重按月频生成",
        )
        for path in FRONTENDS:
            text = self._text(path)
            for token in expected:
                self.assertIn(token, text)

    def test_cycle_tracking_content_covers_merrill_and_pring_requirements(self):
        expected = (
            "PMI；CPI、PPI",
            "变化率、分位数、HP周期、FFT低频择优",
            "55%传统美林 + 45%因子引擎",
            "M1、M2、利率；社融存量/增量；PMI-CPI剪刀差",
            "历史四阶段图",
            "历史六阶段图",
            "阶段资产映射权重（月频信号）",
            "四资产回测趋势（日度回放）",
        )
        for path in FRONTENDS:
            text = self._text(path)
            for token in expected:
                self.assertIn(token, text)

    def test_asset_allocation_content_covers_three_model_requirements(self):
        expected = (
            "美林时钟与普林格周期：资产得分 + 置信转换表",
            "CVXPY + CLARABEL凸二次优化",
            "24月半衰期 + 35%对角收缩",
            "宏观因子全部罗列表格",
            "高效因子检验看板",
            "风险预算增强权重分解",
            "15%纯风险预算 + 75%宏观周期预算 + 10%相对强弱确认",
            "由最终权重反推出的动量/波动确认项",
        )
        for path in FRONTENDS:
            text = self._text(path)
            for token in expected:
                self.assertIn(token, text)

    def test_static_daily_replay_figures_are_available(self):
        for directory in STATIC_DIRS:
            self.assertTrue(directory.is_dir(), directory)
            missing = [name for name in (f"{i}.png" for i in range(1, 30)) if not (directory / name).is_file()]
            self.assertEqual([], missing)
            too_small = [name for name in (f"{i}.png" for i in range(1, 30)) if (directory / name).stat().st_size < 50_000]
            self.assertEqual([], too_small)
        for path in FRONTENDS:
            self.assertIn("/static/asset_allocation_figures/", self._text(path))


if __name__ == "__main__":
    unittest.main()
