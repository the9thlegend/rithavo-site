// Rithavo — shared interaction layer. Lightweight, no dependencies.

(function () {
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Scroll-reveal
  var revealEls = document.querySelectorAll(".reveal");
  if (reduceMotion || !("IntersectionObserver" in window)) {
    revealEls.forEach(function (el) { el.classList.add("in"); });
  } else {
    var io = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add("in");
            io.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.15, rootMargin: "0px 0px -8% 0px" }
    );
    revealEls.forEach(function (el) { io.observe(el); });
  }

  // Animated scorecard bars/rings — trigger fill on reveal
  var scoreBars = document.querySelectorAll("[data-score]");
  if (scoreBars.length) {
    var scoreIO = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            var el = entry.target;
            var score = parseFloat(el.getAttribute("data-score"));
            var max = parseFloat(el.getAttribute("data-max") || "100");
            var pct = Math.max(0, Math.min(100, (score / max) * 100));
            var fill = el.querySelector(".sc-fill");
            if (fill) {
              requestAnimationFrame(function () { fill.style.width = pct + "%"; });
            }
            scoreIO.unobserve(el);
          }
        });
      },
      { threshold: 0.3 }
    );
    scoreBars.forEach(function (el) { scoreIO.observe(el); });
  }

  // Subtle cursor-responsive ambient blobs (desktop only, cheap transform, throttled via rAF)
  var ambient = document.querySelector(".ambient");
  if (ambient && !reduceMotion && window.matchMedia("(pointer: fine)").matches) {
    var ticking = false;
    var mx = 0, my = 0;
    window.addEventListener("mousemove", function (e) {
      mx = (e.clientX / window.innerWidth - 0.5) * 2;
      my = (e.clientY / window.innerHeight - 0.5) * 2;
      if (!ticking) {
        requestAnimationFrame(function () {
          ambient.style.setProperty("--mx", mx.toFixed(3));
          ambient.style.setProperty("--my", my.toFixed(3));
          ticking = false;
        });
        ticking = true;
      }
    });
  }

  // Mark current nav link
  var path = window.location.pathname.split("/").pop() || "index.html";
  document.querySelectorAll(".navlinks a").forEach(function (a) {
    var href = a.getAttribute("href");
    if (href === path || (path === "" && href === "index.html")) {
      a.setAttribute("aria-current", "page");
    }
  });
})();
