(function () {
  "use strict";

  const 数据缓存 = new Map();
  const 支持页面 = /^(allocation|liquidity|rotation|factorlab|technical|portfolio):/;
  const 主色 = ["#c00000", "#ffc000", "#2f75b5", "#808080", "#ed7d31", "#7030a0", "#00b050", "#5b9bd5"];
  const 板块定义 = [
    { 编号: "01", 键: "mechanism", 名称: "原理与传导" },
    { 编号: "02", 键: "descriptive", 名称: "数据与截面" },
    { 编号: "03", 键: "history", 名称: "历史与实时" },
    { 编号: "04", 键: "diagnostics", 名称: "模型与预测" },
    { 编号: "05", 键: "strategy", 名称: "策略与归因" },
  ];
  const 字段中文 = {
    id: "模型",
    name: "模型",
    candidate_id: "方案",
    candidate: "方案",
    model: "模型",
    policy: "执行",
    family: "模型族",
    covariance_method: "协方差",
    expected_return_method: "收益预测",
    lookback: "回看期",
    lookback_days: "回看日",
    risk_aversion: "风险厌恶",
    turnover_l1: "换手一阶惩罚",
    turnover_l2: "换手二阶惩罚",
    turnover_cap: "换手上限",
    position_cap: "单仓上限",
    portfolio_volatility_target: "目标波动",
    probability_power: "概率幂",
    probability_slope: "概率斜率",
    equity_guard_max: "权益保护上限",
    macro_strength: "宏观强度",
    candidate_count: "候选数",
    factor_count: "因子数",
    industry_count: "行业数",
    field_count: "字段数",
    source_count: "数据源",
    chart_count: "序列组",
    active_factor_count: "有效因子",
    live_ratio: "实时覆盖",
  };
  const 状态中文 = {
    conditional_champion: "条件冠军",
    current_champion: "当前冠军",
    current_champion_with_shadow: "冠军与影子盘",
    mixed_governance: "混合治理",
    tracking_not_return_model: "跟踪模型",
    observe_only: "仅观察",
    passed: "通过",
    pass: "通过",
    failed: "未通过",
    fail: "未通过",
    ready: "就绪",
    live: "运行",
    optimal: "最优",
    rejected: "拒绝",
    train: "训练期",
    validation: "验证期",
    valid: "验证期",
    test: "测试期",
    monthly: "月频",
    weekly: "周频",
    quarterly: "季频",
    daily: "日频",
    ewma: "指数加权",
    risk_adjusted_trend: "风险调整趋势",
    adaptive_icir_12m_neutral: "十二月自适应信息系数",
    continuous_rank_volatility_budget: "连续秩波动预算",
    equity_guarded_posterior: "权益保护后验",
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

  async function 读取数据(页面, 强制) {
    if (强制) 数据缓存.delete(页面);
    if (!数据缓存.has(页面)) {
      数据缓存.set(
        页面,
        fetch(接口地址("/api/research-evidence?route=" + encodeURIComponent(页面)), {
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        }).then(function (响应) {
          if (!响应.ok) throw new Error("研究数据读取失败：" + 响应.status);
          return 响应.json();
        })
      );
    }
    return 数据缓存.get(页面);
  }

  async function 载入绘图库() {
    if (window.Plotly) return window.Plotly;
    if (!window.__fivePanelPlotly) {
      window.__fivePanelPlotly = new Promise(function (完成, 失败) {
        const 脚本 = document.createElement("script");
        脚本.src = "https://cdn.plot.ly/plotly-2.35.2.min.js";
        脚本.onload = function () { 完成(window.Plotly); };
        脚本.onerror = 失败;
        document.head.appendChild(脚本);
      });
    }
    return window.__fivePanelPlotly;
  }

  function 有限数(值) {
    const 数字 = Number(值);
    return Number.isFinite(数字) ? 数字 : null;
  }

  function 中文值(值) {
    if (值 == null) return "—";
    if (值 === true) return "通过";
    if (值 === false) return "未通过";
    const 原文 = String(值);
    if (状态中文[原文]) return 状态中文[原文];
    return 原文
      .replace(/slope6_z36/g, "六期斜率")
      .replace(/delta3_z36/g, "三期变化")
      .replace(/level_z36/g, "水平标准化")
      .replace(/percentile60/g, "六十期分位")
      .replace(/leading/g, "先行")
      .replace(/coincident/g, "同步")
      .replace(/lagging/g, "滞后")
      .replace(/diversified posterior/gi, "分散后验")
      .replace(/train/gi, "训练")
      .replace(/validation/gi, "验证")
      .replace(/test/gi, "测试")
      .replace(/sharpe/gi, "夏普")
      .replace(/turnover/gi, "换手")
      .replace(/candidate/gi, "候选");
  }

  function 数值文本(值, 格式) {
    const 数字 = 有限数(值);
    if (数字 == null) return 中文值(值);
    if (格式 === "percent" || 格式 === "signed_percent") {
      return (格式 === "signed_percent" && 数字 > 0 ? "+" : "") + (数字 * 100).toFixed(1) + "%";
    }
    if (格式 === "percentile") return (数字 * 100).toFixed(0) + "%";
    if (格式 === "integer") return String(Math.round(数字));
    if (格式 === "signed") return (数字 > 0 ? "+" : "") + 数字.toFixed(2);
    return Math.abs(数字) >= 100 ? 数字.toFixed(0) : 数字.toFixed(2);
  }

  function 参数值(键, 值) {
    const 数字 = 有限数(值);
    if (数字 == null) return 中文值(值);
    if (键.indexOf("cap") >= 0 || 键.indexOf("target") >= 0 || 键 === "live_ratio") {
      return (数字 * 100).toFixed(1) + "%";
    }
    if (键.indexOf("lookback") >= 0 || 键.indexOf("count") >= 0 || 键 === "field_count") {
      return String(Math.round(数字));
    }
    return Math.abs(数字) < 1 ? 数字.toFixed(3) : 数字.toFixed(1);
  }

  function 参数摘要(数据) {
    const 候选 = [];
    const 冠军 = 数据.champion || {};
    const 描述 = 数据.descriptive || {};
    Object.keys(字段中文).forEach(function (键) {
      const 值 = Object.prototype.hasOwnProperty.call(冠军, 键) ? 冠军[键] : 描述[键];
      if (值 == null || Array.isArray(值) || typeof 值 === "object") return;
      候选.push({ 名称: 字段中文[键], 数值: 参数值(键, 值) });
    });
    if (数据.as_of != null) {
      const 日期 = typeof 数据.as_of === "object"
        ? Object.values(数据.as_of).filter(Boolean).slice(0, 1)[0]
        : 数据.as_of;
      候选.push({ 名称: "数据期", 数值: 中文值(日期) });
    }
    候选.push({ 名称: "状态", 数值: 中文值(数据.status) });
    const 去重 = [];
    const 已有 = new Set();
    候选.forEach(function (项) {
      if (!项.数值 || 已有.has(项.名称)) return;
      已有.add(项.名称);
      去重.push(项);
    });
    return 去重.slice(0, 10);
  }

  function 参数区(数据) {
    const 摘要 = 参数摘要(数据);
    return '<section class="统一参数区" data-five-parameters>' +
      '<div class="统一参数标题"><b>模型参数</b><span>调整后同步重绘</span></div>' +
      '<div class="统一参数控件" data-five-controls></div>' +
      '<div class="统一参数摘要">' + 摘要.map(function (项) {
        return '<div><small>' + 转义(项.名称) + '</small><strong>' + 转义(项.数值) + '</strong></div>';
      }).join("") + "</div></section>";
  }

  function 行名称(行) {
    const 候选键 = ["name", "factor", "asset", "candidate", "industry", "model", "source", "group", "split", "solver", "chart"];
    for (let 序号 = 0; 序号 < 候选键.length; 序号 += 1) {
      const 值 = 行[候选键[序号]];
      if (值 != null && 值 !== "") return 中文值(值);
    }
    return "指标";
  }

  function 趋势线(值列) {
    const 数据 = (Array.isArray(值列) ? 值列 : []).map(Number).filter(Number.isFinite);
    if (数据.length < 2) return '<span class="微趋势空">—</span>';
    const 宽 = 112;
    const 高 = 28;
    const 边 = 2;
    const 最小 = Math.min.apply(null, 数据);
    const 最大 = Math.max.apply(null, 数据);
    const 跨度 = 最大 - 最小 || 1;
    const 点列 = 数据.map(function (值, 序号) {
      const 横 = 边 + 序号 * (宽 - 边 * 2) / Math.max(数据.length - 1, 1);
      const 纵 = 高 - 边 - (值 - 最小) * (高 - 边 * 2) / 跨度;
      return 横.toFixed(1) + "," + 纵.toFixed(1);
    }).join(" ");
    const 颜色 = 数据[数据.length - 1] >= 数据[0] ? 主色[0] : 主色[2];
    return '<svg class="微趋势" viewBox="0 0 ' + 宽 + " " + 高 + '" aria-hidden="true">' +
      '<polyline points="' + 点列 + '" fill="none" stroke="' + 颜色 + '" stroke-width="2"/>' +
      "</svg>";
  }

  function 条件色(值, 格式) {
    const 数字 = 有限数(值);
    if (数字 == null) return "";
    let 强度 = 0;
    let 颜色 = 主色[2];
    if (格式 === "percentile") {
      强度 = Math.max(8, Math.min(100, Math.abs(数字) * 100));
      颜色 = 数字 >= 0.5 ? 主色[2] : 主色[1];
    } else {
      强度 = Math.max(8, Math.min(100, Math.abs(数字) * (Math.abs(数字) <= 2 ? 45 : 2)));
      颜色 = 数字 >= 0 ? 主色[0] : 主色[2];
    }
    return "background:linear-gradient(90deg," + 颜色 + "1f 0 " + 强度.toFixed(0) + "%,transparent " + 强度.toFixed(0) + "% 100%)";
  }

  function 矩阵列(表格) {
    const 全列 = (表格 && tableColumns(表格)) || [];
    const 标识 = 全列.find(function (列) {
      return ["name", "factor", "asset", "candidate", "industry", "model", "source", "group", "split", "solver", "chart"].includes(列.key);
    }) || 全列[0];
    const 趋势 = 全列.find(function (列) { return 列.format === "sparkline"; });
    const 指标 = 全列.filter(function (列) {
      return 列 !== 标识 && 列 !== 趋势 && 列.format !== "text";
    }).slice(0, 4);
    return { 标识: 标识, 指标: 指标, 趋势: 趋势 };
  }

  function tableColumns(表格) {
    return Array.isArray(表格.columns) ? 表格.columns : [];
  }

  function 条件矩阵(表格, 备用项) {
    const 行列 = 表格 && Array.isArray(表格.rows) ? 表格.rows.slice(0, 11) : [];
    if (!行列.length && 备用项 && 备用项.length) {
      return '<div class="条件矩阵 条件矩阵--参数"><div class="条件矩阵标题">关键参数</div>' +
        备用项.slice(0, 10).map(function (项, 序号) {
          return '<div class="参数矩阵行"><b>' + String(序号 + 1).padStart(2, "0") + '</b><span>' +
            转义(项.名称) + '</span><strong>' + 转义(项.数值) + "</strong></div>";
        }).join("") + "</div>";
    }
    if (!行列.length) return '<div class="条件矩阵空">暂无截面数据</div>';
    const 列组 = 矩阵列(表格);
    const 列数 = 2 + 列组.指标.length + (列组.趋势 ? 1 : 0);
    const 标题 = ['<span class="矩阵序号">序</span><span class="矩阵名称">指标</span>']
      .concat(列组.指标.map(function (列) { return "<span>" + 转义(列.label) + "</span>"; }))
      .concat(列组.趋势 ? ["<span>走势</span>"] : [])
      .join("");
    const 内容 = 行列.map(function (行, 序号) {
      const 名称 = 列组.标识 && 行[列组.标识.key] != null ? 中文值(行[列组.标识.key]) : 行名称(行);
      const 单元 = 列组.指标.map(function (列) {
        const 值 = 行[列.key];
        const 状态 = 列.format === "status";
        return '<span class="' + (状态 ? "矩阵状态" : "矩阵数值") + '" style="' + 条件色(值, 列.format) + '">' +
          转义(数值文本(值, 列.format)) + "</span>";
      }).join("");
      const 走势 = 列组.趋势 ? "<span>" + 趋势线(行[列组.趋势.key]) + "</span>" : "";
      return '<div class="条件矩阵行" style="--矩阵列:' + 列数 + ";--矩阵数:" + (列数 - 2) + '">' +
        '<span class="矩阵序号">' + String(序号 + 1).padStart(2, "0") + '</span><strong class="矩阵名称">' +
        转义(名称) + "</strong>" + 单元 + 走势 + "</div>";
    }).join("");
    return '<div class="条件矩阵" style="--矩阵列:' + 列数 + ";--矩阵数:" + (列数 - 2) + '"><div class="条件矩阵表头">' + 标题 + "</div>" + 内容 + "</div>";
  }

  function 关键数(模块) {
    const 表格 = (模块 && 模块.table) || {};
    const 行列 = Array.isArray(表格.rows) ? 表格.rows : [];
    const 列 = tableColumns(表格).filter(function (项) {
      return !["text", "sparkline", "status"].includes(项.format);
    });
    const 结果 = [];
    列.some(function (项) {
      let 最佳 = null;
      行列.forEach(function (行) {
        const 数字 = 有限数(行[项.key]);
        if (数字 == null) return;
        if (!最佳 || Math.abs(数字) > Math.abs(最佳.数值)) 最佳 = { 数值: 数字, 行: 行 };
      });
      if (!最佳) return false;
      结果.push({
        名称: 项.label,
        数值: 数值文本(最佳.数值, 项.format),
        说明: 行名称(最佳.行),
      });
      return 结果.length >= 4;
    });
    if (!结果.length) 结果.push({ 名称: "覆盖", 数值: String(行列.length), 说明: "完整截面" });
    return 结果.slice(0, 4);
  }

  function 板块HTML(定义, 数据) {
    if (定义.键 === "mechanism") {
      return '<section class="五图板块" data-five-block="mechanism">' +
        '<header class="五图标题"><b>' + 定义.编号 + '</b><h2>' + 定义.名称 + '</h2></header>' +
        '<div class="五图综合画布 五图综合画布--机理"><div class="五图主图" data-five-plot="mechanism"></div>' +
        条件矩阵(null, 参数摘要(数据)) + "</div></section>";
    }
    const 模块 = (数据.visuals || {})[定义.键] || {};
    const 数字 = 关键数(模块);
    return '<section class="五图板块" data-five-block="' + 定义.键 + '">' +
      '<header class="五图标题"><b>' + 定义.编号 + '</b><h2>' + 定义.名称 + '</h2><div class="五图数字">' +
      数字.map(function (项) {
        return '<span><small>' + 转义(项.名称) + '</small><strong>' + 转义(项.数值) +
          '</strong><em>' + 转义(中文值(项.说明)) + "</em></span>";
      }).join("") + "</div></header>" +
      '<div class="五图综合画布"><div class="五图主图" data-five-plot="' + 定义.键 + '"></div>' +
      条件矩阵(模块.table) + "</div></section>";
  }

  function 页面HTML(页面, 数据) {
    return '<section class="research-evidence research-five-panel" data-route="' + 转义(页面) + '">' +
      参数区(数据) +
      板块定义.map(function (定义) { return 板块HTML(定义, 数据); }).join("") +
      "</section>";
  }

  function 绘图序列(序列, 序号, 总数) {
    const 类型 = 序列.type || "scatter";
    const 数值列 = (序列.y || []).map(Number);
    const 颜色 = 序列.color || 主色[序号 % 主色.length];
    const 结果 = {
      type: 类型,
      mode: 序列.mode || (类型 === "bar" ? undefined : "lines"),
      name: 中文值(序列.name),
      x: (序列.x || []).map(中文值),
      y: 序列.y || [],
      yaxis: 序列.axis === "y2" ? "y2" : "y",
      text: (序列.text || []).map(中文值),
      hovertemplate: "%{x}<br>%{y:.4f}<extra>" + 转义(中文值(序列.name || "")) + "</extra>",
    };
    if (类型 === "bar") {
      结果.marker = {
        color: 总数 > 1 ? 颜色 : 数值列.map(function (值) {
          return 值 > 0 ? 主色[0] : 值 < 0 ? 主色[2] : 主色[3];
        }),
        line: { color: "#ffffff", width: 0.6 },
      };
      if (数值列.length <= 16) {
        结果.texttemplate = "%{y:.2f}";
        结果.textposition = "outside";
        结果.cliponaxis = false;
      }
    } else {
      结果.line = { color: 颜色, width: 序号 === 0 ? 2.6 : 1.8, dash: 序号 > 3 ? "dot" : "solid" };
      if (String(结果.mode).indexOf("markers") >= 0) {
        结果.marker = { size: 8, color: 颜色, line: { color: "#ffffff", width: 0.8 } };
      }
    }
    return 结果;
  }

  function 横轴标签(图表) {
    if (!图表) return [];
    if (图表.heatmap) return (图表.heatmap.x || []).map(中文值);
    const 序列 = (图表.traces || []).find(function (项) {
      return Array.isArray(项.x) && 项.x.length;
    });
    return 序列 ? 序列.x.map(中文值) : [];
  }

  function 压缩横轴(布局, 图表) {
    const 标签 = 横轴标签(图表);
    if (!标签.length) return;
    const 日期轴 = 标签.filter(function (项) {
      return /^\d{4}[-/.]\d{1,2}/.test(String(项)) || Number.isFinite(Number(项));
    }).length >= 标签.length * 0.8;
    if (日期轴) return;
    const 最长 = 标签.reduce(function (长度, 项) {
      return Math.max(长度, String(项).length);
    }, 0);
    if (标签.length <= 10 && 最长 <= 10) return;
    const 上限 = 最长 > 18 ? 12 : 9;
    const 步长 = Math.max(1, Math.ceil(标签.length / 上限));
    const 序号 = 标签.map(function (_, 索引) { return 索引; })
      .filter(function (索引) { return 索引 % 步长 === 0 || 索引 === 标签.length - 1; });
    布局.xaxis.tickmode = "array";
    布局.xaxis.tickvals = 序号.map(function (索引) { return 标签[索引]; });
    布局.xaxis.ticktext = 序号.map(function (索引) {
      const 名称 = String(标签[索引]);
      if (最长 > 18) return String(索引 + 1).padStart(2, "0");
      return 名称.length > 7 ? 名称.slice(0, 7) : 名称;
    });
    布局.xaxis.tickangle = 最长 > 18 ? 0 : -18;
  }

  function 基础布局(图表, 高度) {
    const 布局 = {
      title: {
        text: 中文值((图表 && 图表.title) || ""),
        x: 0.01,
        xanchor: "left",
        font: { size: 15, color: "#18212b" },
      },
      height: 高度,
      margin: { l: 62, r: 图表 && 图表.y2_title ? 62 : 24, t: 52, b: 68 },
      paper_bgcolor: "#ffffff",
      plot_bgcolor: "#ffffff",
      hovermode: "closest",
      dragmode: false,
      barmode: "group",
      bargap: 0.25,
      xaxis: {
        title: 中文值((图表 && 图表.x_title) || ""),
        autorange: true,
        automargin: true,
        rangeslider: { visible: false },
        tickangle: -12,
        showgrid: false,
        zeroline: false,
        linecolor: "#cfd8e3",
        tickfont: { size: 11 },
      },
      yaxis: {
        autorange: true,
        automargin: true,
        title: 中文值((图表 && 图表.y_title) || ""),
        gridcolor: "#e7ebf0",
        gridwidth: 0.7,
        zerolinecolor: "#a5a5a5",
      },
      legend: { orientation: "h", y: -0.19, x: 0, font: { size: 11 } },
      font: { family: "Arial, KaiTi, STKaiti, sans-serif", size: 12, color: "#18212b" },
    };
    if (图表 && 图表.y2_title) {
      布局.yaxis2 = {
        title: 中文值(图表.y2_title),
        overlaying: "y",
        side: "right",
        showgrid: false,
        zeroline: false,
      };
    }
    if (图表 && Number.isFinite(Number(图表.vline))) {
      布局.shapes = [{
        type: "line",
        x0: Number(图表.vline),
        x1: Number(图表.vline),
        y0: 0,
        y1: 1,
        yref: "paper",
        line: { color: 主色[0], dash: "dash", width: 1.4 },
      }];
    }
    压缩横轴(布局, 图表);
    return 布局;
  }

  function 绘制机理(容器, 数据) {
    const 节点全文 = ((数据.mechanism || {}).nodes || []).map(中文值);
    const 节点 = 节点全文.map(function (名称, 序号) {
      const 简称 = 名称.length > 3 ? 名称.slice(0, 3) : 名称;
      return String(序号 + 1).padStart(2, "0") + "<br>" + 简称;
    });
    if (!节点全文.length) return Promise.resolve();
    const 序列 = [{
      type: "sankey",
      orientation: "h",
      arrangement: "snap",
      node: {
        pad: 24,
        thickness: 22,
        line: { color: "#ffffff", width: 1 },
        label: 节点,
        customdata: 节点全文,
        color: 节点.map(function (_, 序号) { return 主色[序号 % 主色.length]; }),
        hovertemplate: "%{customdata}<extra></extra>",
      },
      link: {
        source: 节点.slice(0, -1).map(function (_, 序号) { return 序号; }),
        target: 节点.slice(1).map(function (_, 序号) { return 序号 + 1; }),
        value: 节点.slice(1).map(function () { return 1; }),
        color: 节点.slice(1).map(function (_, 序号) { return 主色[序号 % 主色.length] + "33"; }),
        hovertemplate: "%{source.label} → %{target.label}<extra></extra>",
      },
    }];
    const 公式 = String((数据.mechanism || {}).formula || "").split("；")[0].slice(0, 72);
    const 布局 = {
      height: 420,
      margin: { l: 20, r: 20, t: 36, b: 54 },
      paper_bgcolor: "#ffffff",
      plot_bgcolor: "#ffffff",
      font: { family: "Arial, KaiTi, STKaiti, sans-serif", size: 12, color: "#18212b" },
      annotations: 公式 ? [{
        x: 0.5,
        y: -0.08,
        xref: "paper",
        yref: "paper",
        text: 转义(公式),
        showarrow: false,
        font: { size: 12, color: "#4b5563" },
      }] : [],
    };
    return window.Plotly.newPlot(容器, 序列, 布局, {
      responsive: true,
      scrollZoom: false,
      doubleClick: false,
      displaylogo: false,
      displayModeBar: false,
    });
  }

  function 绘制模块(容器, 模块) {
    const 图表 = (模块 && 模块.chart) || {};
    let 序列 = [];
    let 高度 = 420;
    if (图表.heatmap) {
      const 行数 = (图表.heatmap.y || []).length;
      高度 = Math.max(420, Math.min(560, 行数 * 24 + 120));
      序列 = [{
        type: "heatmap",
        x: (图表.heatmap.x || []).map(中文值),
        y: (图表.heatmap.y || []).map(中文值),
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
      序列 = 原序列.map(function (项, 序号) { return 绘图序列(项, 序号, 原序列.length); });
    }
    if (!序列.length) {
      容器.innerHTML = '<div class="五图空白">暂无可绘制数据</div>';
      return Promise.resolve();
    }
    return window.Plotly.newPlot(容器, 序列, 基础布局(图表, 高度), {
      responsive: true,
      scrollZoom: false,
      doubleClick: false,
      displaylogo: false,
      displayModeBar: false,
    });
  }

  function 恢复旧控件(宿主, 旧区块) {
    if (!旧区块) return;
    Array.from(旧区块.querySelectorAll(".研究参数原件")).forEach(function (节点) {
      const 槽位 = 节点.dataset.fivePanelSlot;
      const 占位 = 槽位 ? 宿主.querySelector('[data-five-placeholder="' + CSS.escape(槽位) + '"]') : null;
      节点.classList.remove("研究参数原件");
      节点.classList.remove("研究参数动作");
      delete 节点.dataset.fivePanelSlot;
      if (占位 && 占位.parentNode) 占位.parentNode.replaceChild(节点, 占位);
      else 宿主.insertBefore(节点, 旧区块);
    });
    旧区块.remove();
  }

  function 参数字段(控件, 边界) {
    const 查询 = "select,input:not([type='hidden']),textarea";
    const 禁止壳层 = ".fl2-shell,.fl2-section,.fl2-toolbar,.fl2-param-group,section,article,main";
    function 可迁移(节点) {
      if (!节点 || 节点 === 边界 || !边界.contains(节点)) return false;
      if (!["LABEL", "DIV"].includes(节点.tagName)) return false;
      if (节点.matches(禁止壳层)) return false;
      if (节点.querySelectorAll(查询).length !== 1) return false;
      if (节点.querySelector("table,.js-plotly-plot,.chart-panel,.table-panel,details,[data-five-placeholder]")) return false;
      return 节点.querySelectorAll("*").length <= 48;
    }
    const 首选 = 控件.closest("label,.fl2-field,.control-field,.field,.form-field,.filter-field");
    if (可迁移(首选)) return 首选;
    let 上级 = 控件.parentElement;
    while (上级 && 上级 !== 边界) {
      if (可迁移(上级)) return 上级;
      if (上级.matches && 上级.matches(禁止壳层)) break;
      上级 = 上级.parentElement;
    }
    return 控件;
  }

  function 收拢原页(宿主, 新区块, 页面, 工作区) {
    const 控件宿主 = 新区块.querySelector("[data-five-controls]");
    const 原子项 = Array.from(宿主.children).filter(function (节点) { return 节点 !== 新区块; });
    const 已移动 = new Set();
    let 槽位序号 = 0;

    function 迁移(元素, 动作) {
      if (!元素 || 已移动.has(元素)) return;
      if (!动作) {
        const 查询 = "select,input:not([type='hidden']),textarea";
        const 控件数 = 元素.matches && 元素.matches(查询) ? 1 : 元素.querySelectorAll(查询).length;
        const 非字段 = 元素.matches && 元素.matches(".fl2-shell,.fl2-section,.fl2-toolbar,.fl2-param-group,section,article,main");
        const 含业务 = 元素.querySelector && 元素.querySelector("table,.js-plotly-plot,.chart-panel,.table-panel,details");
        if (控件数 !== 1 || 非字段 || 含业务) return;
      }
      const 槽位 = "five-" + 页面.replace(/[^a-z0-9]+/gi, "-") + "-" + (++槽位序号);
      const 占位 = document.createElement("span");
      占位.hidden = true;
      占位.dataset.fivePlaceholder = 槽位;
      元素.parentNode.insertBefore(占位, 元素);
      元素.dataset.fivePanelSlot = 槽位;
      元素.classList.add("研究参数原件");
      if (动作) 元素.classList.add("研究参数动作");
      控件宿主.appendChild(元素);
      已移动.add(元素);
    }

    原子项.forEach(function (节点) {
      const 查询 = "select,input:not([type='hidden']),textarea";
      const 控件列 = 节点.querySelectorAll ? Array.from(节点.querySelectorAll(查询)) : [];
      const 字段列 = [];
      控件列.map(function (控件) {
        return 参数字段(控件, 节点);
      }).forEach(function (字段) {
        if (!字段列.includes(字段)) 字段列.push(字段);
      });
      字段列.forEach(function (字段) {
        迁移(字段, false);
      });
      if (控件列.length && 节点.querySelectorAll) {
        Array.from(节点.querySelectorAll("button")).forEach(function (按钮) {
          const 文字 = String(按钮.textContent || "").trim();
          if (!文字 || 文字.length > 12 || 按钮.closest("table,.js-plotly-plot")) return;
          迁移(按钮, true);
        });
      }
      节点.classList.add("研究原页隐藏");
    });

    控件宿主.querySelectorAll("select,input,textarea").forEach(function (控件) {
      if (控件.dataset.fivePanelSync === "1") return;
      控件.dataset.fivePanelSync = "1";
      控件.addEventListener("change", function () {
        window.setTimeout(function () {
          挂载(页面, 工作区, true).catch(function (错误) {
            console.error("五类综合图重绘失败", 错误);
          });
        }, 420);
      });
    });
    if (!控件宿主.querySelector("select,input,textarea,button")) 控件宿主.remove();
    宿主.classList.add("研究五图模式");
  }

  function 中文化可见文本(宿主) {
    const 遍历 = document.createTreeWalker(宿主, NodeFilter.SHOW_TEXT);
    const 待改 = [];
    let 节点 = 遍历.nextNode();
    while (节点) {
      const 原文 = 节点.nodeValue || "";
      if (/slope6_z36|delta3_z36|level_z36|percentile60|diversified posterior/i.test(原文)) 待改.push(节点);
      节点 = 遍历.nextNode();
    }
    待改.forEach(function (文字节点) { 文字节点.nodeValue = 中文值(文字节点.nodeValue); });
  }

  async function 挂载(页面, 工作区, 强制) {
    if (!支持页面.test(页面)) {
      document.documentElement.classList.remove("五图页面");
      return;
    }
    document.documentElement.classList.add("五图页面");
    const 数据页面 = 页面 === "factorlab:strategy" && (!工作区 || 工作区.kind !== "index")
      ? "factorlab:dashboard"
      : 页面;
    const 数据 = await 读取数据(数据页面, 强制);
    if (!数据 || !数据.visuals) return;
    const 根节点 = document.getElementById("view-root");
    if (!根节点) return;
    const 缓存页 = 根节点.querySelector('.view-cache-pane[data-view="' + CSS.escape(页面) + '"]');
    const 宿主 = 缓存页 || 根节点;
    const 旧区块 = 宿主.querySelector('.research-five-panel[data-route="' + CSS.escape(页面) + '"]');
    恢复旧控件(宿主, 旧区块);
    Array.from(宿主.children).forEach(function (节点) { 节点.classList.remove("研究原页隐藏"); });
    宿主.insertAdjacentHTML("afterbegin", 页面HTML(页面, 数据));
    const 新区块 = 宿主.querySelector('.research-five-panel[data-route="' + CSS.escape(页面) + '"]');
    if (!新区块) return;
    收拢原页(宿主, 新区块, 页面, 工作区);
    await 载入绘图库();
    await Promise.all(板块定义.map(function (定义) {
      const 容器 = 新区块.querySelector('[data-five-plot="' + 定义.键 + '"]');
      if (定义.键 === "mechanism") return 绘制机理(容器, 数据);
      return 绘制模块(容器, (数据.visuals || {})[定义.键]);
    }));
    中文化可见文本(新区块);
  }

  window.ResearchEvidence = {
    mount: 挂载,
    clearCache: function () { 数据缓存.clear(); },
  };
})();
