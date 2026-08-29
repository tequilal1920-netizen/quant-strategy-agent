(function () {
  "use strict";

  const 数据缓存 = new Map();
  const 支持页面 = /^(allocation|liquidity|rotation|factorlab|technical|portfolio):/;
  const 固定色 = ["#c00000", "#ffc000", "#2f75b5", "#808080", "#ed7d31", "#7030a0", "#00b050", "#5b9bd5", "#a5a5a5", "#ff0000"];
  const 状态中文 = {
    pass: "通过",
    passed: "通过",
    live: "正常",
    ready: "就绪",
    optimal: "最优",
    optimal_inaccurate: "近似最优",
    true: "通过",
    false: "未通过",
    fail: "未通过",
    failed: "未通过",
    rejected: "未通过",
    review: "复核",
    conditional: "有条件通过",
    not_installed: "未安装",
    train: "训练期",
    training: "训练期",
    valid: "验证期",
    validation: "验证期",
    test: "测试期",
    daily: "日频",
    weekly: "周频",
    monthly: "月频",
    quarterly_style: "季度风格",
    bond_cash: "债券现金",
    broad_equity: "宽基权益",
    commodity: "商品",
    thematic_equity: "主题权益",
    other: "其他",
    moneyflow: "资金流",
    quality_roa: "盈利质量",
    growth_revenue: "营收增长",
    growth_profit: "利润增长",
    value_ep: "盈利估值",
    value_bp: "账面估值",
    momentum_12m: "十二月动量",
    momentum_6m: "六月动量",
    reversal_1m: "一月反转",
    residual_volatility: "残差波动",
    liquidity: "流动性",
    size: "规模",
    beta: "市场敏感度",
    leverage: "杠杆",
    dividend_yield: "股息率",
    earnings_yield: "盈利收益率",
    adaptive_icir_12m_neutral: "十二月滚动ICIR中性",
    continuous_rank_volatility_budget: "连续排序波动预算",
    continuous_rank_inverse_volatility_budget: "连续排序逆波动预算",
    cs_elastic_neutral: "横截面弹性中性",
    full_exposure: "全额暴露",
    equity_guarded_posterior: "权益保护后验",
    CLARABEL: "澄清锥",
    OSQP: "二次规划",
    SCS: "分裂锥",
    SCIPY_SLSQP: "序列二次规划",
  };

  function 转义(值) {
    return String(值 == null ? "" : 值)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function 接口地址(路径) {
    const 前缀 = String((window.APP_BOOT && window.APP_BOOT.basePath) || "").replace(/\/$/, "");
    return 前缀 + 路径;
  }

  async function 读取数据(页面) {
    if (!数据缓存.has(页面)) {
      数据缓存.set(
        页面,
        fetch(接口地址("/api/research-evidence?route=" + encodeURIComponent(页面)), {
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        }).then(function (响应) {
          if (!响应.ok) throw new Error("研究数据读取失败_" + 响应.status);
          return 响应.json();
        })
      );
    }
    return 数据缓存.get(页面);
  }

  async function 载入绘图库() {
    if (window.Plotly) return window.Plotly;
    if (!window.__researchEvidencePlotly) {
      window.__researchEvidencePlotly = new Promise(function (完成, 失败) {
        const 脚本 = document.createElement("script");
        脚本.src = window.APP_BOOT.plotlyUrl;
        脚本.onload = function () { 完成(window.Plotly); };
        脚本.onerror = 失败;
        document.head.appendChild(脚本);
      });
    }
    return window.__researchEvidencePlotly;
  }

  function 有限数(值) {
    const 数字 = Number(值);
    return Number.isFinite(数字) ? 数字 : null;
  }

  function 小数(值, 位数) {
    const 数字 = 有限数(值);
    return 数字 == null ? "—" : 数字.toFixed(位数 == null ? 2 : 位数);
  }

  function 百分比(值, 位数) {
    const 数字 = 有限数(值);
    return 数字 == null ? "—" : (数字 * 100).toFixed(位数 == null ? 1 : 位数) + "%";
  }

  function 中文值(值) {
    if (值 == null || 值 === "") return "—";
    if (typeof 值 === "boolean") return 值 ? "通过" : "未通过";
    const 原文 = String(值);
    if (状态中文[原文]) return 状态中文[原文];
    if (/^CSI2000_ENH\s*\/\s*M$/i.test(原文)) return "国证2000增强 / 月频";
    if (/^CSI2000_ENH\s*\/\s*W$/i.test(原文)) return "国证2000增强 / 周频";
    if (/^CSI2000_ENH\s*\/\s*Q$/i.test(原文)) return "国证2000增强 / 季频";
    if (/^CSI800_ENH\s*\/\s*M$/i.test(原文)) return "中证800增强 / 月频";
    if (/^CSI800_ENH\s*\/\s*W$/i.test(原文)) return "中证800增强 / 周频";
    if (/^CSI800_ENH\s*\/\s*Q$/i.test(原文)) return "中证800增强 / 季频";
    return 原文
      .replace(/C6_direct_month_smooth/g, "C6 月度直连平滑")
      .replace(/C18_monthly_residual_path_top5/g, "C18 月度残差路径前五")
      .replace(/C19_monthly_business_price_crowding_top5/g, "C19 月度经营价格拥挤前五")
      .replace(/diversified posterior/gi, "分散后验")
      .replace(/adaptive_icir_12m_neutral/g, "十二月滚动ICIR中性")
      .replace(/continuous_rank_inverse_volatility_budget/g, "连续排序逆波动预算")
      .replace(/continuous_rank_volatility_budget/g, "连续排序波动预算")
      .replace(/cs_elastic_neutral/g, "横截面弹性中性")
      .replace(/full_exposure/g, "全额暴露")
      .replace(/equity_guarded_posterior/g, "权益保护后验")
      .replace(/slope(\d+)_z(\d+)/g, "$1期斜率（$2期标准化）")
      .replace(/delta(\d+)_z(\d+)/g, "$1期变化（$2期标准化）")
      .replace(/level_z(\d+)/g, "水平值（$1期标准化）")
      .replace(/percentile(\d+)/g, "$1期分位")
      .replace(/\bleading\b/gi, "先行")
      .replace(/\bcoincident\b/gi, "同步")
      .replace(/\blagging\b/gi, "滞后")
      .replace(/PIT/g, "点时")
      .replace(/daily/g, "日频")
      .replace(/weekly/g, "周频")
      .replace(/monthly/g, "月频")
      .replace(/quarterly_style/g, "季度风格")
      .replace(/bond_cash/g, "债券现金")
      .replace(/broad_equity/g, "宽基权益")
      .replace(/thematic_equity/g, "主题权益")
      .replace(/commodity/g, "商品")
      .replace(/moneyflow/g, "资金流")
      .replace(/quality_roa/g, "盈利质量")
      .replace(/growth_revenue/g, "营收增长")
      .replace(/growth_profit/g, "利润增长")
      .replace(/value_ep/g, "盈利估值")
      .replace(/value_bp/g, "账面估值")
      .replace(/momentum_12m/g, "十二月动量")
      .replace(/momentum_6m/g, "六月动量")
      .replace(/reversal_1m/g, "一月反转")
      .replace(/residual_volatility/g, "残差波动")
      .replace(/_/g, " ");
  }

  function 中文数组(值列) {
    return (Array.isArray(值列) ? 值列 : []).map(function (值) {
      return typeof 值 === "string" ? 中文值(值) : 值;
    });
  }

  function 趋势线(值列) {
    const 数据 = (Array.isArray(值列) ? 值列 : []).map(Number).filter(Number.isFinite);
    if (!数据.length) return "";
    const 宽 = 150;
    const 高 = 34;
    const 边 = 2;
    const 最小 = Math.min.apply(null, 数据);
    const 最大 = Math.max.apply(null, 数据);
    const 跨度 = 最大 - 最小 || 1;
    const 点列 = 数据.map(function (值, 序号) {
      const 横 = 边 + 序号 * (宽 - 边 * 2) / Math.max(数据.length - 1, 1);
      const 纵 = 高 - 边 - (值 - 最小) * (高 - 边 * 2) / 跨度;
      return 横.toFixed(1) + "," + 纵.toFixed(1);
    });
    const 上行 = 数据[数据.length - 1] >= 数据[0];
    const 颜色 = 上行 ? 固定色[0] : 固定色[2];
    const 终点 = 点列[点列.length - 1].split(",");
    return '<svg class="研究趋势线" viewBox="0 0 ' + 宽 + " " + 高 +
      '" preserveAspectRatio="none" role="img" aria-label="历史趋势">' +
      '<polyline points="' + 点列.join(" ") + '" fill="none" stroke="' + 颜色 +
      '" stroke-width="2"/><circle cx="' + 终点[0] + '" cy="' + 终点[1] +
      '" r="2.5" fill="' + 颜色 + '"/></svg>';
  }

  function 中文化页面文本(宿主) {
    if (!宿主 || !document.createTreeWalker) return;
    const 遍历 = document.createTreeWalker(宿主, NodeFilter.SHOW_TEXT);
    const 待改 = [];
    let 节点 = 遍历.nextNode();
    while (节点) {
      const 父级 = 节点.parentElement;
      const 原文 = 节点.nodeValue || "";
      if (
        父级 &&
        !父级.closest("script, style, .js-plotly-plot") &&
        /(slope\d+_z\d+|delta\d+_z\d+|level_z\d+|percentile\d+|\bleading\b|\bcoincident\b|\blagging\b|diversified posterior)/i.test(原文)
      ) {
        待改.push(节点);
      }
      节点 = 遍历.nextNode();
    }
    待改.forEach(function (文字节点) {
      文字节点.nodeValue = 中文值(文字节点.nodeValue);
    });
  }

  function 单元值(行, 列) {
    const 值 = 行[列.key];
    const 格式 = 列.format || "text";
    if (格式 === "sparkline") return 趋势线(值);
    if (格式 === "status") {
      const 文字 = 中文值(值);
      const 通过 = 值 === true || ["pass", "passed", "live", "ready", "optimal", "true", "a"].includes(String(值).toLowerCase());
      const 未过 = 值 === false || ["fail", "failed", "false", "rejected", "not_installed"].includes(String(值).toLowerCase());
      return '<span class="研究状态" data-tone="' + (通过 ? "通过" : 未过 ? "未过" : "中性") + '">' + 转义(文字) + "</span>";
    }
    if (格式 === "arrow") {
      const 数字 = 有限数(值) || 0;
      return '<span class="研究方向" data-sign="' + (数字 > 0 ? "正" : 数字 < 0 ? "负" : "平") + '">' +
        (数字 > 0 ? "↑" : 数字 < 0 ? "↓" : "→") + "</span>";
    }
    if (格式 === "percent" || 格式 === "signed_percent") {
      const 数字 = 有限数(值);
      return '<span class="研究数字" data-sign="' + (格式 === "signed_percent" && 数字 != null ? (数字 > 0 ? "正" : 数字 < 0 ? "负" : "平") : "平") +
        '">' + 百分比(数字, 1) + "</span>";
    }
    if (格式 === "percentile") {
      const 数字 = 有限数(值);
      const 比例 = 数字 == null ? 0 : Math.max(0, Math.min(1, 数字));
      return '<span class="研究分位" style="--分位:' + (比例 * 100).toFixed(1) + '%">' + 百分比(数字, 0) + "</span>";
    }
    if (格式 === "integer") return 转义(Math.round(Number(值) || 0));
    if (格式 === "scientific") {
      const 数字 = 有限数(值);
      return 数字 == null ? "—" : 转义(数字.toExponential(2));
    }
    if (格式 === "number" || 格式 === "signed") {
      const 数字 = 有限数(值);
      return '<span class="研究数字" data-sign="' + (格式 === "signed" && 数字 != null ? (数字 > 0 ? "正" : 数字 < 0 ? "负" : "平") : "平") +
        '">' + 小数(数字, 2) + "</span>";
    }
    return 转义(中文值(值));
  }

  function 行名称(行) {
    const 候选键 = ["name", "factor", "asset", "candidate", "industry", "chart", "model", "source", "group", "split", "solver"];
    for (let 序号 = 0; 序号 < 候选键.length; 序号 += 1) {
      const 值 = 行[候选键[序号]];
      if (值 != null && 值 !== "") return 中文值(值);
    }
    return "观测项";
  }

  function 图谱(表格) {
    const 列 = (表格 && 表格.columns) || [];
    const 行列 = (表格 && 表格.rows) || [];
    if (!行列.length || !列.length) return "";
    const 标识列 = 列.find(function (项目) {
      return ["name", "factor", "asset", "candidate", "industry", "chart", "model", "source", "group", "split", "solver"].includes(项目.key);
    }) || 列[0];
    return '<div class="研究图谱" style="--图谱列数:' + Math.min(4, Math.max(2, Math.ceil(Math.sqrt(行列.length)))) + '">' +
      行列.map(function (行) {
        const 趋势列 = 列.find(function (项目) { return 项目.format === "sparkline"; });
        const 指标列 = 列.filter(function (项目) { return 项目 !== 标识列 && 项目.format !== "sparkline"; });
        const 辅助 = 指标列.map(function (项目) {
          return '<span class="研究图谱指标"><small>' + 转义(项目.label) + '</small><b>' + 单元值(行, 项目) + "</b></span>";
        }).join("");
        return '<article class="研究图谱项" aria-label="' + 转义(行名称(行)) + '">' +
          '<header><strong>' + 转义(中文值(行[标识列.key])) + '</strong></header>' +
          '<div class="研究图谱指标列">' + 辅助 + "</div>" +
          (趋势列 ? '<div class="研究图谱趋势">' + 趋势线(行[趋势列.key]) + "</div>" : "") +
          "</article>";
      }).join("") + "</div>";
  }

  function 最大行(行列, 键, 取绝对值) {
    return 行列.reduce(function (当前, 行) {
      const 数字 = 有限数(行[键]);
      if (数字 == null) return 当前;
      const 比较值 = 取绝对值 ? Math.abs(数字) : 数字;
      if (!当前 || 比较值 > 当前.比较值) return { 行: 行, 数字: 数字, 比较值: 比较值 };
      return 当前;
    }, null);
  }

  function 查找样本(行列, 名称) {
    return 行列.find(function (行) {
      return String(行.split || "").toLowerCase() === 名称;
    }) || null;
  }

  function 直接结论(层, 模块) {
    const 表格 = 模块.table || {};
    const 行列 = 表格.rows || [];
    if (!行列.length) return "当前没有可核对的数据。";

    if (层 === "descriptive") {
      if ("percentile" in 行列[0]) {
        const 高位 = 最大行(行列, "percentile", false);
        const 变动 = 最大行(行列, "change", true);
        return "共跟踪" + 行列.length + "项；" + 行名称(高位.行) + "处于" + 百分比(高位.数字, 0) +
          "历史分位，" + 行名称(变动.行) + "较前值变动最大，为" + 小数(变动.数字, 2) + "。";
      }
      if ("weight" in 行列[0] && "factor" in 行列[0]) {
        const 权重 = 最大行(行列, "absolute_weight", false) || 最大行(行列, "weight", true);
        const 前三 = 行列.slice().sort(function (甲, 乙) { return Math.abs(Number(乙.weight) || 0) - Math.abs(Number(甲.weight) || 0); }).slice(0, 3);
        const 合计 = 前三.reduce(function (和, 行) { return 和 + Math.abs(Number(行.weight) || 0); }, 0);
        return "当前权重最高为" + 行名称(权重.行) + "，占" + 百分比(Math.abs(权重.数字), 1) +
          "；前三项绝对权重合计" + 百分比(合计, 1) + "。";
      }
      if ("weight" in 行列[0] && "asset" in 行列[0]) {
        const 权重 = 最大行(行列, "weight", false);
        const 风险 = 最大行(行列, "risk_contribution", true);
        return "当前权重最高为" + 行名称(权重.行) + "，占" + 百分比(权重.数字, 1) +
          "；风险贡献绝对值最高为" + 行名称(风险.行) + "，占" + 百分比(风险.数字, 1) + "。";
      }
      if ("score" in 行列[0]) {
        const 得分 = 最大行(行列, "score", false);
        return "共比较" + 行列.length + "组候选；完整性得分最高为" + 行名称(得分.行) + "，得分" + 小数(得分.数字, 1) + "。";
      }
    }

    if (层 === "history") {
      const 验证 = 查找样本(行列, "validation") || 查找样本(行列, "valid");
      const 测试 = 查找样本(行列, "test");
      if (验证 && 测试 && "sharpe" in 验证) {
        return "验证期夏普" + 小数(验证.sharpe, 2) + "，测试期夏普" + 小数(测试.sharpe, 2) +
          "；测试期年化收益" + 百分比(测试.annual_return, 1) + "，最大回撤" + 百分比(测试.max_drawdown, 1) + "。";
      }
      if ("observations" in 行列[0]) {
        const 样本 = 最大行(行列, "observations", false);
        const 通过数 = 行列.filter(function (行) { return ["passed", "pass", "live", true].includes(行.status); }).length;
        return "覆盖" + 行列.length + "组序列，最大共同样本为" + Math.round(样本.数字) +
          "期；质量检查通过" + 通过数 + "组。";
      }
      if ("metrics" in 行列[0]) {
        let 最佳 = null;
        行列.forEach(function (行) {
          const 测试期 = (行.metrics || []).find(function (指标) { return 指标.split === "test"; });
          if (测试期 && (!最佳 || Number(测试期.sharpe) > 最佳.夏普)) 最佳 = { 行: 行, 夏普: Number(测试期.sharpe), 指标: 测试期 };
        });
        if (最佳) {
          return 中文值(最佳.行.model) + "测试期夏普最高，为" + 小数(最佳.夏普, 2) +
            "；同期超额年化" + 百分比(最佳.指标.annual_excess_return, 1) + "。";
        }
      }
    }

    if (层 === "diagnostics") {
      if ("actual_solver" in 行列[0]) {
        const 最快 = 行列.reduce(function (当前, 行) {
          return !当前 || Number(行.median_ms) < Number(当前.median_ms) ? 行 : 当前;
        }, null);
        const 残差 = 最大行(行列, "max_constraint_violation", false);
        return "中位求解最快为" + 中文值(最快.actual_solver) + "，耗时" + 小数(最快.median_ms, 2) +
          "毫秒；全部路径最大约束残差为" + (残差 ? 残差.数字.toExponential(2) : "—") + "。";
      }
      if ("validation_sharpe" in 行列[0]) {
        const 最佳 = 最大行(行列, "validation_sharpe", false);
        const 换手 = 有限数(最佳.行.validation_turnover != null ? 最佳.行.validation_turnover : 最佳.行.turnover);
        return "验证期夏普最高为" + 行名称(最佳.行) + "的" + 小数(最佳.数字, 2) +
          (换手 == null ? "。" : "，同期换手" + 小数(换手, 2) + "。");
      }
      if ("observations" in 行列[0]) {
        const 间隔 = 最大行(行列, "largest_gap", false);
        const 缺失 = 行列.reduce(function (和, 行) { return 和 + (Number(行.missing) || 0); }, 0);
        return "交集后缺失合计" + 缺失 + "项；最大数据间隔为" + 小数(间隔.数字, 0) +
          "日，来自" + 行名称(间隔.行) + "。";
      }
      if ("passed" in 行列[0]) {
        const 晋级 = 行列.filter(function (行) { return 行.passed === true; }).length;
        const 得分 = 最大行(行列, "score", false);
        return "共检查" + 行列.length + "组候选，最终晋级" + 晋级 + "组；完整性得分最高为" +
          行名称(得分.行) + "的" + 小数(得分.数字, 1) + "。";
      }
    }

    if (层 === "strategy") {
      if ("cost_bps" in 行列[0]) {
        const 起点 = 行列[0];
        const 终点 = 行列[行列.length - 1];
        return "单边成本由" + 小数(起点.cost_bps, 0) + "升至" + 小数(终点.cost_bps, 0) +
          "基点时，年化收益由" + 百分比(起点.return, 1) + "降至" + 百分比(终点.return, 1) +
          "，夏普由" + 小数(起点.sharpe, 2) + "降至" + 小数(终点.sharpe, 2) + "。";
      }
      if ("active_contribution" in 行列[0]) {
        const 正向 = 最大行(行列, "active_contribution", false);
        const 负向 = 行列.reduce(function (当前, 行) {
          return !当前 || Number(行.active_contribution) < Number(当前.active_contribution) ? 行 : 当前;
        }, null);
        return "主动贡献最高为" + 行名称(正向.行) + "的" + 百分比(正向.数字, 1) +
          "；拖累最大为" + 行名称(负向) + "的" + 百分比(负向.active_contribution, 1) + "。";
      }
      if ("selected" in 行列[0]) {
        const 入选 = 行列.filter(function (行) { return 行.selected === true; });
        const 最高 = 最大行(行列, "score", false);
        return "当前入选" + 入选.length + "个行业；综合得分最高为" + 行名称(最高.行) +
          "，得分" + 小数(最高.数字, 2) + "。";
      }
      if ("source" in 行列[0]) {
        return "当前覆盖" + 行列.length + "类资金数据源，发布频率和复盘边界均按原始口径分别处理。";
      }
      if ("weight" in 行列[0] && "asset" in 行列[0]) {
        const 权重 = 最大行(行列, "weight", false);
        const 风险 = 最大行(行列, "risk_contribution", true);
        const 收益线 = ((模块.chart || {}).traces || []).find(function (序列) { return 序列.name === "年化收益"; });
        const 夏普线 = ((模块.chart || {}).traces || []).find(function (序列) { return 序列.name === "夏普"; });
        let 成本结论 = "";
        if (收益线 && 夏普线 && 收益线.y && 收益线.y.length && 夏普线.y && 夏普线.y.length) {
          const 末序号 = Math.min(收益线.y.length, 夏普线.y.length) - 1;
          成本结论 = "；最高成本情景年化收益" + 百分比(收益线.y[末序号], 1) + "，夏普" + 小数(夏普线.y[末序号], 2);
        }
        return "配置权重最高为" + 行名称(权重.行) + "的" + 百分比(权重.数字, 1) +
          "，风险贡献最高为" + 行名称(风险.行) + "的" + 百分比(风险.数字, 1) + 成本结论 + "。";
      }
      if ("passed" in 行列[0]) {
        const 晋级 = 行列.filter(function (行) { return 行.passed === true; }).length;
        const 最佳 = 最大行(行列, "test_sharpe", false);
        return "最终晋级" + 晋级 + "组；测试期夏普最高为" + 行名称(最佳.行) + "的" + 小数(最佳.数字, 2) + "。";
      }
    }
    return "本层覆盖" + 行列.length + "组可核对结果，图中按统一口径展示全部观测。";
  }

  function 核心数字(模块) {
    const 表格 = 模块.table || {};
    const 行列 = 表格.rows || [];
    const 列 = 表格.columns || [];
    if (!行列.length) return "";
    const 结果 = [{ 标签: "覆盖数量", 数值: String(行列.length), 说明: "全部显示" }];
    const 优先键 = [
      "percentile", "validation_sharpe", "test_sharpe", "sharpe", "rank_ic",
      "weight", "risk_contribution", "score", "active_contribution",
      "annual_return", "turnover", "observations", "largest_gap", "max_constraint_violation",
    ];
    优先键.some(function (键) {
      const 列项 = 列.find(function (项目) { return 项目.key === 键; });
      if (!列项) return false;
      const 最大 = 最大行(行列, 键, 键 === "risk_contribution" || 键 === "active_contribution");
      if (!最大) return false;
      结果.push({
        标签: 列项.label,
        数值: ["percentile", "weight", "risk_contribution", "active_contribution", "annual_return"].includes(键) ? 百分比(最大.数字, 1) : 小数(最大.数字, 键 === "observations" || 键 === "largest_gap" ? 0 : 2),
        说明: 行名称(最大.行),
      });
      return 结果.length >= 4;
    });
    for (let 序号 = 0; 序号 < 优先键.length && 结果.length < 4; 序号 += 1) {
      const 键 = 优先键[序号];
      if (结果.some(function (项目) { return 项目.键 === 键; })) continue;
      const 列项 = 列.find(function (项目) { return 项目.key === 键; });
      const 最大 = 列项 ? 最大行(行列, 键, 键 === "risk_contribution" || 键 === "active_contribution") : null;
      if (!最大 || 结果.some(function (项目) { return 项目.标签 === 列项.label; })) continue;
      结果.push({
        标签: 列项.label,
        数值: ["percentile", "weight", "risk_contribution", "active_contribution", "annual_return"].includes(键) ? 百分比(最大.数字, 1) : 小数(最大.数字, 键 === "observations" || 键 === "largest_gap" ? 0 : 2),
        说明: 行名称(最大.行),
      });
    }
    const 状态列 = 列.find(function (项目) { return 项目.format === "status"; });
    if (状态列 && 结果.length < 4) {
      const 通过 = 行列.filter(function (行) {
        return 行[状态列.key] === true || ["pass", "passed", "live", "ready", "optimal"].includes(String(行[状态列.key]).toLowerCase());
      }).length;
      结果.push({ 标签: "通过数量", 数值: String(通过), 说明: "共" + 行列.length + "项" });
    }
    return '<div class="研究数字带">' + 结果.slice(0, 4).map(function (项目) {
      return '<div><small>' + 转义(项目.标签) + '</small><strong>' + 转义(项目.数值) +
        '</strong><span>' + 转义(中文值(项目.说明)) + "</span></div>";
    }).join("") + "</div>";
  }

  function 链路图(数据) {
    const 节点 = (数据.mechanism && 数据.mechanism.nodes) || [];
    const 公式 = (数据.mechanism && 数据.mechanism.formula) || "";
    return '<section class="研究区块 研究区块--链路" data-block="mechanism">' +
      '<header class="研究标题"><h3>分析链路</h3></header>' +
      '<div class="研究链路">' + 节点.map(function (节点, 序号) {
        return '<div><b>' + String(序号 + 1).padStart(2, "0") + '</b><span>' + 转义(中文值(节点)) + "</span></div>";
      }).join("") + "</div>" +
      (公式 ? '<div class="研究关系"><span>计算关系</span><b>' + 转义(公式) + "</b></div>" : "") +
      "</section>";
  }

  function 分析区块(层, 模块) {
    return '<section class="研究区块" data-block="' + 层 + '">' +
      '<header class="研究标题"><h3>' + 转义(模块.title || "重点分析") + "</h3></header>" +
      '<div class="研究结论"><span>结论</span><p>' + 转义(直接结论(层, 模块)) + "</p></div>" +
      核心数字(模块) +
      '<div class="研究主图" data-analysis-plot="' + 层 + '"></div>' +
      图谱(模块.table) +
      "</section>";
  }

  function 页面内容(页面, 数据) {
    const 图层 = 数据.visuals || {};
    return '<section class="research-evidence research-analysis" data-route="' + 转义(页面) + '">' +
      链路图(数据) +
      ["descriptive", "history", "diagnostics", "strategy"].map(function (层) {
        return 图层[层] ? 分析区块(层, 图层[层]) : "";
      }).join("") +
      "</section>";
  }

  function 绘图序列(序列, 序号, 总数) {
    const 类型 = 序列.type || "scatter";
    const 数值列 = (序列.y || []).map(Number);
    const 颜色 = 固定色[序号 % 固定色.length];
    const 结果 = {
      type: 类型,
      mode: 序列.mode || (类型 === "bar" ? undefined : "lines"),
      name: 中文值(序列.name),
      x: 中文数组(序列.x || []),
      y: 序列.y || [],
      yaxis: 序列.axis === "y2" ? "y2" : "y",
      text: 中文数组(序列.text || []),
      hovertemplate: "%{x}<br>%{y:.4f}<extra>" + 转义(中文值(序列.name || "")) + "</extra>",
    };
    if (类型 === "bar") {
      结果.marker = {
        color: 总数 > 1 ? 颜色 : 数值列.map(function (值) { return 值 > 0 ? 固定色[0] : 值 < 0 ? 固定色[2] : 固定色[3]; }),
        line: { color: "#ffffff", width: 0.5 },
      };
      if (数值列.length <= 18) {
        结果.texttemplate = "%{y:.2f}";
        结果.textposition = "outside";
        结果.cliponaxis = false;
      }
    } else if (String(结果.mode || "").indexOf("markers") >= 0) {
      结果.marker = { size: 9, color: 颜色, line: { color: "#ffffff", width: 0.8 }, symbol: 序号 % 2 ? "diamond" : "circle" };
      结果.line = { color: 颜色, width: 1.8 };
    } else {
      结果.line = { color: 颜色, width: 序号 === 0 ? 2.6 : 2.0, dash: 序号 > 2 ? "dot" : "solid" };
    }
    return 结果;
  }

  function 绘制主图(容器, 图表) {
    if (!容器 || !图表) return Promise.resolve();
    let 序列;
    if (图表.heatmap) {
      序列 = [{
        type: "heatmap",
        x: 中文数组(图表.heatmap.x || []),
        y: 中文数组(图表.heatmap.y || []),
        z: 图表.heatmap.z || [],
        zmin: 0,
        zmax: 1,
        colorscale: [[0, "#f4cccc"], [0.48, "#fff2cc"], [0.52, "#d9eaf7"], [1, "#2f75b5"]],
        showscale: false,
        xgap: 2,
        ygap: 2,
        hovertemplate: "%{y}<br>%{x}：%{z}<extra></extra>",
      }];
    } else {
      const 原序列 = 图表.traces || [];
      序列 = 原序列.map(function (项目, 序号) { return 绘图序列(项目, 序号, 原序列.length); });
    }
    if (!序列.length) {
      容器.innerHTML = '<div class="研究空白">当前没有可绘制序列。</div>';
      return Promise.resolve();
    }
    const 标线 = [];
    if (Number.isFinite(Number(图表.vline))) {
      标线.push({
        type: "line",
        x0: Number(图表.vline),
        x1: Number(图表.vline),
        y0: 0,
        y1: 1,
        yref: "paper",
        line: { color: 固定色[0], dash: "dash", width: 1.5 },
      });
    }
    const 热力行数 = 图表.heatmap ? (图表.heatmap.y || []).length : 0;
    const 图高 = 图表.heatmap ? Math.max(390, Math.min(720, 热力行数 * 30 + 140)) : 390;
    const 布局 = {
      title: { text: 中文值(图表.title || ""), x: 0.01, xanchor: "left", font: { size: 15, color: "#18212b" } },
      height: 图高,
      margin: { l: 66, r: 图表.y2_title ? 66 : 28, t: 52, b: 86 },
      paper_bgcolor: "#ffffff",
      plot_bgcolor: "#ffffff",
      hovermode: "closest",
      dragmode: false,
      barmode: "group",
      bargap: 0.28,
      xaxis: {
        title: 中文值(图表.x_title || ""),
        autorange: true,
        automargin: true,
        rangeslider: { visible: false },
        tickangle: 图表.heatmap ? -25 : -15,
        showgrid: false,
        zeroline: false,
        linecolor: "#cfd8e3",
        tickfont: { size: 11 },
      },
      yaxis: {
        autorange: true,
        automargin: true,
        title: 中文值(图表.y_title || ""),
        gridcolor: "#e7ebf0",
        gridwidth: 0.7,
        zerolinecolor: "#a5a5a5",
      },
      legend: { orientation: "h", y: -0.24, x: 0, font: { size: 11 } },
      shapes: 标线,
      font: { family: "Arial, KaiTi, STKaiti, sans-serif", size: 12, color: "#18212b" },
    };
    if (图表.y2_title) {
      布局.yaxis2 = {
        title: 中文值(图表.y2_title),
        overlaying: "y",
        side: "right",
        showgrid: false,
        zeroline: false,
      };
    }
    return window.Plotly.newPlot(容器, 序列, 布局, {
      responsive: true,
      scrollZoom: false,
      doubleClick: false,
      displaylogo: false,
      displayModeBar: false,
    });
  }

  async function 挂载(页面, 工作区) {
    if (!支持页面.test(页面)) return;
    const 数据页面 = 页面 === "factorlab:strategy" && (!工作区 || 工作区.kind !== "index")
      ? "factorlab:dashboard"
      : 页面;
    const 数据 = await 读取数据(数据页面);
    if (!数据 || !数据.visuals) return;
    const 根节点 = document.getElementById("view-root");
    if (!根节点) return;
    const 缓存页 = 根节点.querySelector('.view-cache-pane[data-view="' + CSS.escape(页面) + '"]');
    const 宿主 = 缓存页 || 根节点;
    const 旧区块 = 宿主.querySelector('.research-analysis[data-route="' + CSS.escape(页面) + '"]');
    if (旧区块) 旧区块.remove();
    宿主.insertAdjacentHTML("beforeend", 页面内容(页面, 数据));
    const 新区块 = 宿主.querySelector('.research-analysis[data-route="' + CSS.escape(页面) + '"]');
    if (!新区块) return;
    await 载入绘图库();
    await Promise.all(
      ["descriptive", "history", "diagnostics", "strategy"].map(function (层) {
        const 图表 = 数据.visuals[层] && 数据.visuals[层].chart;
        return 绘制主图(新区块.querySelector('[data-analysis-plot="' + 层 + '"]'), 图表);
      })
    );
    中文化页面文本(宿主);
  }

  window.ResearchEvidence = {
    mount: 挂载,
    clearCache: function () { 数据缓存.clear(); },
  };
})();
