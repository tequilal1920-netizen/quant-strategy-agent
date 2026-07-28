async page => {
  const results = [];

  const visit = async (group, item) => {
    const sidebar = page.locator(".sidebar");
    const groupButton = sidebar.getByRole("button", { name: group, exact: true }).first();
    if (await groupButton.getAttribute("aria-expanded") !== "true") {
      await groupButton.click();
    }
    await sidebar.getByRole("button", { name: item, exact: true }).last().click();
    await page.waitForTimeout(3500);
    const section = page.locator(".research-analysis").last();
    results.push(await section.evaluate(el => ({
      route: el.dataset.route,
      blocks: el.querySelectorAll(".研究区块").length,
      plots: el.querySelectorAll(".研究主图.js-plotly-plot").length,
      tables: el.querySelectorAll("table").length,
      cards: el.querySelectorAll(".研究图谱项").length,
      scrollX: el.scrollWidth - el.clientWidth,
      oldIntro: el.textContent.includes("模型与数据证据层"),
      oldStatus: el.textContent.includes("模型状态"),
      englishCodes: /slope\d+_z\d+|delta\d+_z\d+|level_z\d+|\bleading\b|\bcoincident\b|\blagging\b/.test(el.textContent),
    })));
  };

  await visit("资产配置", "周期跟踪");
  await visit("资金面跟踪", "散户");
  await visit("行业景气度", "行业景气度");
  await visit("因子实验室", "因子看板");
  await visit("技术分析", "K线学习");
  await visit("组合优化", "优化求解");
  return results;
}
