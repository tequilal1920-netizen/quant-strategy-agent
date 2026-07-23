async (page) => {
  const consoleErrors = [];
  const pageErrors = [];
  const onConsole = (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  };
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
      group: node.closest(".nav-group").querySelector(".nav-group-toggle").textContent.trim(),
    })),
  );
  const results = [];

  for (const item of targets) {
    const started = Date.now();
    let failure = null;
    try {
      await page.locator(`.nav-item[data-target="${item.target}"]`).click({ timeout: 10_000 });
      await page.waitForFunction(
        (target) => {
          const active = document.querySelector(`.nav-item[data-target="${target}"]`);
          const root = document.querySelector("#view-root");
          const title = document.querySelector("#page-title");
          return Boolean(active?.classList.contains("is-active") && (root?.innerText.trim().length > 20 || root?.querySelector("iframe")) && title?.innerText.trim());
        },
        item.target,
        { timeout: 20_000 },
      );
      await page.waitForTimeout(180);
    } catch (error) {
      failure = String(error).slice(0, 400);
    }

    const state = await page.evaluate((target) => {
      const active = document.querySelector(".nav-item.is-active");
      const root = document.querySelector("#view-root");
      const text = root?.innerText.trim() || "";
      const bad = (text.match(/加载失败|请求失败|服务异常|无法加载|not found|traceback/gi) || []).slice(0, 5);
      const visible = [...document.querySelectorAll("body *")].filter((element) => {
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none"
          && !["svg", "path", "g", "line", "rect", "circle"].includes(element.tagName.toLowerCase());
      });
      const htmlSizes = visible.filter((element) => !element.closest("svg"))
        .map((element) => Number.parseFloat(getComputedStyle(element).fontSize)).filter(Number.isFinite);
      const chartSizes = [...document.querySelectorAll(".js-plotly-plot text")]
        .filter((element) => element.getBoundingClientRect().width > 0 && element.getBoundingClientRect().height > 0)
        .map((element) => Number.parseFloat(getComputedStyle(element).fontSize)).filter(Number.isFinite);
      return {
        active: active?.dataset.target || null,
        textLength: text.length,
        bad,
        minHtmlFontPx: htmlSizes.length ? Math.min(...htmlSizes) : null,
        minChartFontPx: chartSizes.length ? Math.min(...chartSizes) : null,
        overflowPx: Math.max(0, document.documentElement.scrollWidth - window.innerWidth),
        targetExists: Boolean(document.querySelector(`.nav-item[data-target="${target}"]`)),
        hasIframe: Boolean(root?.querySelector("iframe")),
      };
    }, item.target);
    results.push({ ...item, ms: Date.now() - started, failure, ...state });
  }

  const failed = results.filter((item) => item.failure || item.active !== item.target || (item.textLength < 20 && !item.hasIframe)
    || item.bad.length || item.overflowPx > 0 || (item.minHtmlFontPx !== null && item.minHtmlFontPx < 14)
    || (item.minChartFontPx !== null && item.minChartFontPx < 11));
  const sorted = results.map((item) => item.ms).sort((left, right) => left - right);
  const dotStates = await page.locator(".nav-item i").evaluateAll((nodes) =>
    [...new Set(nodes.map((node) => ({ status: node.dataset.status || null, color: getComputedStyle(node).backgroundColor })))],
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
      min: sorted[0],
      median: sorted[Math.floor(sorted.length / 2)],
      p95: sorted[Math.floor(sorted.length * 0.95)],
      max: sorted[sorted.length - 1],
      slowest: [...results].sort((a, b) => b.ms - a.ms).slice(0, 5).map(({ target, label, group, ms }) => ({ target, label, group, ms })),
    },
    consoleErrors,
    pageErrors,
    dotStates,
    ui,
  };
}
