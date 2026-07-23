(function () {
  "use strict";

  var LOCAL_SOURCE = "/static/vendor/plotly.min.js";
  var CDN_SOURCE = "https://cdn.plot.ly/plotly-2.35.2.min.js";

  function inject(source) {
    return new Promise(function (resolve, reject) {
      var script = document.createElement("script");
      var completed = false;
      var timeout = window.setTimeout(function () {
        if (completed) return;
        completed = true;
        script.remove();
        reject(new Error("plotly_load_timeout"));
      }, 8000);

      script.src = source;
      script.async = true;
      script.crossOrigin = source.indexOf("https://") === 0 ? "anonymous" : "";
      script.onload = function () {
        if (completed) return;
        completed = true;
        window.clearTimeout(timeout);
        if (window.Plotly && typeof window.Plotly.newPlot === "function") {
          resolve(window.Plotly);
        } else {
          reject(new Error("plotly_missing_after_load"));
        }
      };
      script.onerror = function () {
        if (completed) return;
        completed = true;
        window.clearTimeout(timeout);
        reject(new Error("plotly_load_failed"));
      };
      document.head.appendChild(script);
    });
  }

  window.dashboardPlotlyReady = window.Plotly
    ? Promise.resolve(window.Plotly)
    : inject(LOCAL_SOURCE).catch(function () {
        return inject(CDN_SOURCE).catch(function () {
          return null;
        });
      });
})();
