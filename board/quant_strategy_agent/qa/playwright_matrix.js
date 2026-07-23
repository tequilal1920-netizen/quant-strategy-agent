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
      group: node.closest(".nav-group")?.querySelector(".nav-group-toggle")?.textContent.trim() || "主页",
    })),
  );
  const results = [];

  for (const item of targets) {
    const locator = page.locator(`.nav-item[data-target="${item.target}"]`);
    const started = Date.now();
    let failure = null;
    try {
      await locator.click({ timeout: 10_000 });
      await page.waitForFunction(
        (target) => {
          const active = document.querySelector(`.nav-item[data-target="${target}"]`);
          const root = document.querySelector("#view-root");
          const title = document.querySelector("#page-title");
          return Boolean(active && active.classList.contains("is-active")
            && !active.classList.contains("is-loading") && root
            && (root.innerText.trim().length > 20 || root.querySelector("iframe")) && title && title.innerText.trim());
        },
        item.target,
        { timeout: 20_000 },
      );
      const sections = await page.locator("[data-workspace-section]").evaluateAll((nodes) =>
        nodes.map((node) => node.dataset.workspaceSection),
      );
      const sectionResults = [];
      for (const section of sections) {
        const sectionStarted = Date.now();
        const sectionButton = page.locator(`[data-workspace-section="${section}"]`);
        await sectionButton.click({ timeout: 10_000 });
        await page.waitForFunction(
          (sectionId) => {
            const button = document.querySelector(`[data-workspace-section="${sectionId}"]`);
            const root = document.querySelector("#view-root");
            return Boolean(button && button.classList.contains("is-active") && root
              && (root.innerText.trim().length > 20 || root.querySelector("iframe")) && !root.innerText.includes("当前功能暂不可用"));
          },
          section,
          { timeout: 20_000 },
        );
        sectionResults.push({ section, ms: Date.now() - sectionStarted });
      }
      await page.waitForTimeout(120);
      item.sections = sectionResults;
    } catch (error) {
      failure = String(error).slice(0, 500);
    }

    const state = await page.evaluate((target) => {
      const active = document.querySelector(".nav-item.is-active");
      const root = document.querySelector("#view-root");
      const title = document.querySelector("#page-title");
      const text = root ? root.innerText.trim() : "";
      const bad = (text.match(/页面加载失败|当前功能暂不可用|请求失败|服务异常|无法加载|not found|traceback/gi) || []).slice(0, 5);
      const visible = [...document.querySelectorAll("body *")].filter((element) => {
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        const ignored = ["svg", "path", "g", "line", "rect", "circle"];
        return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden"
          && style.display !== "none" && !ignored.includes(element.tagName.toLowerCase());
      });
      const htmlFontSizes = visible.filter((element) => !element.closest("svg"))
        .map((element) => Number.parseFloat(getComputedStyle(element).fontSize)).filter(Number.isFinite);
      const chartFontSizes = [...document.querySelectorAll(".js-plotly-plot text")]
        .filter((element) => {
          const rect = element.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0;
        })
        .map((element) => Number.parseFloat(getComputedStyle(element).fontSize)).filter(Number.isFinite);
      return {
        active: active && active.dataset.target,
        title: title && title.innerText.trim(),
        textLength: text.length,
        bad,
        minHtmlFontPx: htmlFontSizes.length ? Math.min(...htmlFontSizes) : null,
        minChartFontPx: chartFontSizes.length ? Math.min(...chartFontSizes) : null,
        overflowPx: Math.max(0, document.documentElement.scrollWidth - window.innerWidth),
        rootChildren: root ? root.children.length : 0,
        hasIframe: Boolean(root && root.querySelector("iframe")),
        targetExists: Boolean(document.querySelector(`.nav-item[data-target="${target}"]`)),
      };
    }, item.target);
    results.push({ ...item, ms: Date.now() - started, failure, ...state });
  }

  const sorted = results.map((item) => item.ms).sort((left, right) => left - right);
  const dotStates = await page.locator(".nav-item i").evaluateAll((nodes) =>
    [...new Set(nodes.map((node) => getComputedStyle(node).backgroundColor))],
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
    failed: results.filter((item) => item.failure || item.active !== item.target
      || (item.textLength < 20 && !item.hasIframe) || item.bad.length || item.overflowPx > 0
      || (item.minHtmlFontPx !== null && item.minHtmlFontPx < 14)
      || (item.minChartFontPx !== null && item.minChartFontPx < 11)),
    timings: {
      min: sorted[0], median: sorted[Math.floor(sorted.length / 2)],
      p95: sorted[Math.floor(sorted.length * 0.95)], max: sorted[sorted.length - 1],
    },
    results,
    consoleErrors,
    pageErrors,
    dotStates,
    ui,
  };
}