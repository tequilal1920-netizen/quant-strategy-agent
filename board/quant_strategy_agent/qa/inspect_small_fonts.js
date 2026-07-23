async (page) => page.evaluate(() =>
  [...document.querySelectorAll("body *")]
    .filter((element) => {
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return rect.width > 0
        && rect.height > 0
        && !element.closest("svg")
        && Number.parseFloat(style.fontSize) < 14;
    })
    .slice(0, 40)
    .map((element) => ({
      tag: element.tagName,
      className: String(element.className || ""),
      size: getComputedStyle(element).fontSize,
      text: (element.innerText || element.textContent || "").trim().slice(0, 60),
    })),
)
