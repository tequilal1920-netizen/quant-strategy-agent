(() => {
  "use strict";

  let domRoot = document;

  function mount(root) {
    domRoot = root;
    let attempts = 0;
    const timer = window.setInterval(() => {
      attempts += 1;
      const checkbox = domRoot.querySelector("#reliable-only");
      const chartReady = domRoot.querySelector("#all-rank-chart .barlayer");
      if (checkbox && chartReady) {
        if (!checkbox.checked) {
          checkbox.dispatchEvent(new Event("change", { bubbles: true }));
        }
        window.clearInterval(timer);
      } else if (attempts >= 100) {
        window.clearInterval(timer);
      }
    }, 100);
  }

  window.AIMonitorBoot = { mount };
})();