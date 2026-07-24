async (page) => {
  const consoleErrors = [];
  const pageErrors = [];
  const onConsole = (message) => { if (message.type() === "error") consoleErrors.push(message.text()); };
  const onPageError = (error) => pageErrors.push(String(error));
  page.on("console", onConsole);
  page.on("pageerror", onPageError);

  const groups = page.locator(".nav-group-toggle");
  for (let index = 0; index < await groups.count(); index += 1) {
    const toggle = groups.nth(index);
    if ((await toggle.getAttribute("aria-expanded")) !== "true") await toggle.click();
  }

  const targets = await page.locator(".nav-item[data-target]").evaluateAll((nodes) =>
    nodes.map((node) => ({
      target: node.dataset.target,
      label: node.textContent.trim(),
      group: node.closest(".nav-group")?.querySelector(".nav-group-toggle")?.textContent.trim() || "主页",
    })),
  );
  const results = [];

  for (const item of targets) {
    const started = Date.now();
    let failure = null;
    try {
      await page.locator(`.nav-item[data-target="${item.target}"]`).click({ timeout: 10_000 });
      await page.waitForFunction((target) => {
        const active = document.querySelector(`.nav-item[data-target="${target}"]`);
        const root = document.querySelector("#view-root");
        const title = document.querySelector("#page-title");
        const shadow = root?.querySelector("#ai-monitor-host")?.shadowRoot;
        const text = `${root?.innerText || ""} ${shadow?.textContent || ""}`.trim();
        const aiReady = target !== "data:ai_monitor" || Boolean(
          shadow?.querySelectorAll("[data-ai-section]").length === 4
          && shadow?.querySelectorAll(".js-plotly-plot").length >= 8
          && shadow?.querySelector("#weight-control-panel")?.dataset.ready === "true"
        );
        return Boolean(active?.classList.contains("is-active") && text.length > 20 && title?.innerText.trim() && aiReady);
      }, item.target, { timeout: item.target === "data:ai_monitor" ? 120_000 : 30_000 });
      await page.waitForTimeout(180);
    } catch (error) {
      failure = String(error).slice(0, 500);
    }

    const state = await page.evaluate((target) => {
      const active = document.querySelector(".nav-item.is-active");
      const root = document.querySelector("#view-root");
      const shadow = root?.querySelector("#ai-monitor-host")?.shadowRoot;
      const text = `${root?.innerText || ""} ${shadow?.textContent || ""}`.trim();
      const bad = (text.match(/页面加载失败|当前功能暂不可用|请求失败|服务异常|无法加载|not found|traceback/gi) || []).slice(0, 5);
      const ignored = new Set(["svg", "path", "g", "line", "rect", "circle"]);
      const candidates = [...document.querySelectorAll("body *"), ...(shadow ? shadow.querySelectorAll("*") : [])];
      const visible = candidates.filter((element) => {
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden"
          && style.display !== "none" && !ignored.has(element.tagName.toLowerCase());
      });
      const htmlSizes = visible.filter((element) => !element.closest("svg"))
        .map((element) => Number.parseFloat(getComputedStyle(element).fontSize)).filter(Number.isFinite);
      const charts = [...document.querySelectorAll(".js-plotly-plot text"), ...(shadow ? shadow.querySelectorAll(".js-plotly-plot text") : [])];
      const chartSizes = charts.filter((element) => {
        const rect = element.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
      }).map((element) => Number.parseFloat(getComputedStyle(element).fontSize)).filter(Number.isFinite);
      const aiNative = target === "data:ai_monitor" ? {
        sectionCount: shadow?.querySelectorAll("[data-ai-section]").length || 0,
        chartCount: shadow?.querySelectorAll(".js-plotly-plot").length || 0,
        weightReady: shadow?.querySelector("#weight-control-panel")?.dataset.ready || null,
      } : null;
      return {
        active: active?.dataset.target || null,
        textLength: text.length,
        bad,
        minHtmlFontPx: htmlSizes.length ? Math.min(...htmlSizes) : null,
        minChartFontPx: chartSizes.length ? Math.min(...chartSizes) : null,
        overflowPx: Math.max(0, document.documentElement.scrollWidth - window.innerWidth),
        targetExists: Boolean(document.querySelector(`.nav-item[data-target="${target}"]`)),
        hasIframe: Boolean(document.querySelector("iframe")),
        aiNative,
      };
    }, item.target);
    results.push({ ...item, ms: Date.now() - started, failure, ...state });
  }

  const failed = results.filter((item) => item.failure || item.active !== item.target || item.textLength < 20
    || item.bad.length || item.overflowPx > 0 || item.hasIframe
    || (item.minHtmlFontPx !== null && item.minHtmlFontPx < 14)
    || (item.minChartFontPx !== null && item.minChartFontPx < 11)
    || (item.target === "data:ai_monitor" && (item.aiNative?.sectionCount !== 4
      || item.aiNative?.chartCount < 8 || item.aiNative?.weightReady !== "true")));
  const sorted = results.map((item) => item.ms).sort((left, right) => left - right);
  const dotStates = await page.locator(".nav-item").evaluateAll((nodes) =>
    [...new Set(nodes.map((node) => node.dataset.status || null))],
  );
  const ui = await page.evaluate(() => ({
    bodyFont: getComputedStyle(document.body).fontFamily,
    h1Px: Number.parseFloat(getComputedStyle(document.querySelector("#page-title")).fontSize),
    viewport: { width: innerWidth, height: innerHeight },
  }));
  page.off("console", onConsole);
  page.off("pageerror", onPageError);
  return {
    count: results.length,
    failedCount: failed.length,
    failed,
    timings: {
      min: sorted[0], median: sorted[Math.floor(sorted.length / 2)],
      p95: sorted[Math.floor(sorted.length * 0.95)], max: sorted[sorted.length - 1],
      slowest: [...results].sort((a, b) => b.ms - a.ms).slice(0, 5)
        .map(({ target, label, group, ms }) => ({ target, label, group, ms })),
    },
    consoleErrors,
    pageErrors,
    dotStates,
    ui,
  };
}