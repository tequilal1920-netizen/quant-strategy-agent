from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FRONTENDS = (
    ROOT / "quant_strategy_agent" / "static" / "js" / "app.js",
    ROOT / "quant_strategy_agent_vnext" / "static" / "js" / "app.js",
)
START = "  /* Asset allocation schema 5.0: conditional evidence contract; v4 renderers remain unchanged. */"
END = "  /* r27: common-window data-quality repair */"


class AssetAllocationV5VisualContractTests(unittest.TestCase):
    def _text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def test_both_frontends_share_the_same_schema_five_contract(self):
        blocks = []
        for path in FRONTENDS:
            text = self._text(path)
            start = text.index(START)
            end = text.index(END, start)
            blocks.append(text[start:end])
        self.assertEqual(blocks[0], blocks[1])

    def test_schema_five_is_conditional_and_v4_fallback_remains(self):
        for path in FRONTENDS:
            text = self._text(path)
            self.assertIn("if(allocIsV5(data))", text)
            self.assertIn("if(view==='cycle')return await allocationCycle();", text)
            self.assertIn("if(view==='strategy')return await allocationStrategy();", text)
            self.assertIn("if(view==='backtest')return await allocationBacktest();", text)
            self.assertIn("return await allocationHome();", text)

    def test_cycle_evidence_contract_is_complete(self):
        required = (
            "周期因子可用性与PIT证据",
            "因子可用性、来源与PIT时点",
            "observation_period",
            "release_time",
            "available_time",
            "vintage",
            "持续期与状态转移模型",
            "周期贡献与观点方向冲突",
            "跨周期冲突诊断",
            "display",
        )
        for path in FRONTENDS:
            block = self._text(path)
            for token in required:
                self.assertIn(token, block)

    def test_strategy_and_oos_contract_is_complete(self):
        required = (
            "四资产权重、风险预算锚与资产风险贡献",
            "Black–Litterman观点与后验",
            "完整观点误差协方差Ω",
            "cycle_contributions",
            "宏观因子风险贡献",
            "硬约束与可行域残差",
            "交易成本与目标函数惩罚",
            "OOS、概率夏普与生产晋级门禁",
            "selection_uses_test",
            "probabilistic_sharpe_ratio",
        )
        for path in FRONTENDS:
            block = self._text(path)
            for token in required:
                self.assertIn(token, block)

    def test_existing_allocation_headings_are_unchanged(self):
        required = (
            "header('资产配置主页','多周期研判、风险预算与当前权重','资产配置')",
            "header('周期跟踪','逐因子信号、完整阶段图谱与历史复盘','资产配置')",
            "header('配置策略','全天候、风险平价与多模型求解','资产配置')",
            "header('回测检验','等权基准、训练验证测试与主动收益审计','资产配置')",
        )
        for path in FRONTENDS:
            text = self._text(path)
            for token in required:
                self.assertIn(token, text)


if __name__ == "__main__":
    unittest.main()
