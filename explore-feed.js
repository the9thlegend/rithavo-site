/* Home/Explore/Admin Integration Phase — the Explore feed, rendered
   into a container element. Fetches this service's own /api/explore/*
   proxy (never app.rithavo.com directly from the browser) so the exact
   same feed/pagination/filtering the sibling already implements powers
   Home here without a second implementation.

   Usage: RithavoExplore.mount(document.getElementById("explore-root"))
*/
window.RithavoExplore = (function () {
  const STORY_TYPES = {
    LAYOFFS_HIRING: "Layoffs & Hiring", LEADERSHIP: "Leadership & Appointments", MA: "Mergers & Acquisitions",
    COMPANY_DEVELOPMENT: "Company Developments", PRODUCT_TECHNOLOGY: "Product & Technology",
    PARTNERSHIP: "Partnerships", WORKPLACE: "Workplace", CAREER: "Career", BUSINESS: "Business",
    REGULATORY: "Regulatory", GLOBAL: "Global",
  };

  function cardHtml(s) {
    const image = s.has_image
      ? `<img class="explore-card-image" src="https://app.rithavo.com/explore/image/${s.id}" alt="" loading="lazy">`
      : `<div class="explore-card-image-fallback"></div>`;
    const meta = (s.source_name || "Rithavo") + (s.published_at ? " · " + s.published_at.slice(0, 10) : "");
    const wrapper = document.createElement("a");
    wrapper.className = "explore-card";
    wrapper.href = "#";
    wrapper.dataset.storyId = s.id;
    wrapper.innerHTML = `${image}<div class="explore-card-body">
      <span class="explore-chip">${s.story_type_label || STORY_TYPES[s.story_type] || s.story_type}</span>
      <div class="explore-card-headline"></div>
      <p class="explore-card-summary"></p>
      <div class="explore-card-meta">${meta}</div>
    </div>`;
    wrapper.querySelector(".explore-card-headline").textContent = s.headline;
    wrapper.querySelector(".explore-card-summary").textContent = s.summary;
    return wrapper;
  }

  function mount(root, opts) {
    opts = opts || {};
    const onOpenStory = opts.onOpenStory || function (id) {
      window.open(`https://app.rithavo.com/explore/${id}`, "_blank", "noopener");
    };

    let page = 1;
    let hasMore = false;
    let loading = false;
    let storyType = "";
    let industryId = "";

    root.innerHTML = `
      <div class="explore-filterbar" id="explore-filterbar"></div>
      <div class="explore-grid" id="explore-grid"></div>
      <div class="explore-end" id="explore-sentinel">Loading…</div>
    `;
    const filterbar = root.querySelector("#explore-filterbar");
    const grid = root.querySelector("#explore-grid");
    const sentinel = root.querySelector("#explore-sentinel");

    function renderFilterbar(industryTree) {
      const chips = [`<button type="button" class="explore-filter-chip ${!storyType ? "active" : ""}" data-story-type="">All</button>`];
      Object.keys(STORY_TYPES).forEach((slug) => {
        chips.push(
          `<button type="button" class="explore-filter-chip ${storyType === slug ? "active" : ""}" data-story-type="${slug}">${STORY_TYPES[slug]}</button>`
        );
      });
      let options = `<option value="">All industries</option>`;
      industryTree.forEach((group) => {
        options += `<option value="${group.parent.id}">${group.parent.name}</option>`;
        group.children.forEach((c) => {
          options += `<option value="${c.id}">&nbsp;&nbsp;— ${c.name}</option>`;
        });
      });
      filterbar.innerHTML = chips.join("") + `<select class="explore-industry-select" id="explore-industry-select">${options}</select>`;
      filterbar.querySelectorAll(".explore-filter-chip").forEach((chip) => {
        chip.addEventListener("click", () => {
          storyType = chip.dataset.storyType;
          resetAndLoad();
        });
      });
      filterbar.querySelector("#explore-industry-select").addEventListener("change", (e) => {
        industryId = e.target.value;
        resetAndLoad();
      });
    }

    function resetAndLoad() {
      page = 1;
      hasMore = false;
      grid.innerHTML = "";
      sentinel.style.display = "";
      sentinel.textContent = "Loading…";
      renderFilterbar(window.__rithavoExploreIndustryTree || []);
      loadMore();
    }

    function loadMore() {
      if (loading || (page > 1 && !hasMore)) return;
      loading = true;
      const params = new URLSearchParams({ page: page });
      if (storyType) params.set("story_type", storyType);
      if (industryId) params.set("industry", industryId);
      fetch("/api/explore/stories?" + params.toString())
        .then((r) => r.json())
        .then((data) => {
          if (page === 1 && (!data.stories || data.stories.length === 0)) {
            grid.innerHTML = `<div class="explore-empty">No stories match these filters yet.</div>`;
          }
          (data.stories || []).forEach((s) => {
            const card = cardHtml(s);
            card.addEventListener("click", (e) => { e.preventDefault(); onOpenStory(s.id); });
            grid.appendChild(card);
          });
          hasMore = !!data.has_more;
          page = data.next_page || page + 1;
          loading = false;
          if (!hasMore) {
            sentinel.textContent = grid.children.length ? "You're all caught up." : "";
            sentinel.style.display = grid.children.length ? "" : "none";
          } else {
            sentinel.style.display = "none"; // hidden until intersecting again
          }
        })
        .catch(() => { loading = false; sentinel.textContent = "Couldn't load more stories right now."; });
    }

    fetch("/api/explore/industries")
      .then((r) => r.json())
      .then((data) => {
        window.__rithavoExploreIndustryTree = data.tree || [];
        renderFilterbar(window.__rithavoExploreIndustryTree);
      })
      .catch(() => renderFilterbar([]));

    loadMore();

    const observer = new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting && hasMore) {
        sentinel.style.display = "";
        sentinel.textContent = "Loading more…";
        loadMore();
      }
    });
    observer.observe(sentinel);
  }

  return { mount };
})();
