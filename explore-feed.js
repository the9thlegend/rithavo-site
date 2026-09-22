/* Home/Explore/Admin Integration Phase, corrected by the Product
   Correction Phase — the Explore feed, rendered into a container
   element. No customer-facing filters: this phase scrapped the
   story-type/industry filter UI entirely. The feed is a single,
   continuous, profile-based-relevance ordering fetched from this
   service's own /api/explore/stories proxy (never app.rithavo.com
   directly from the browser) — "Relevant to You" first, then
   "Explore" once relevant stories are exhausted, exactly as the
   backend already ordered them (is_relevant on each story). No
   ranking happens in this file; it only decides where to insert the
   two section headers as stories arrive in the order the server sent.

   Usage: RithavoExplore.mount(document.getElementById("explore-root"))
*/
window.RithavoExplore = (function () {
  const STORY_TYPE_LABELS_FALLBACK = {
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
      <span class="explore-chip">${s.story_type_label || STORY_TYPE_LABELS_FALLBACK[s.story_type] || s.story_type}</span>
      <div class="explore-card-headline"></div>
      <p class="explore-card-summary"></p>
      <div class="explore-card-meta">${meta}</div>
    </div>`;
    wrapper.querySelector(".explore-card-headline").textContent = s.headline;
    wrapper.querySelector(".explore-card-summary").textContent = s.summary;
    return wrapper;
  }

  function sectionHeader(text) {
    const el = document.createElement("div");
    el.className = "explore-section-header";
    el.textContent = text;
    return el;
  }

  function ensureModal() {
    let modal = document.getElementById("explore-story-modal");
    if (modal) return modal;
    modal = document.createElement("div");
    modal.id = "explore-story-modal";
    modal.className = "explore-modal-overlay";
    modal.innerHTML = `
      <div class="explore-modal" role="dialog" aria-modal="true">
        <button type="button" class="explore-modal-close" aria-label="Close">&times;</button>
        <div class="explore-modal-body"></div>
      </div>
    `;
    modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });
    modal.querySelector(".explore-modal-close").addEventListener("click", closeModal);
    document.body.appendChild(modal);
    return modal;
  }

  function closeModal() {
    const modal = document.getElementById("explore-story-modal");
    if (modal) modal.classList.remove("open");
  }

  function openStoryModal(id) {
    const modal = ensureModal();
    const body = modal.querySelector(".explore-modal-body");
    body.innerHTML = `<div class="app-skeleton">Loading…</div>`;
    modal.classList.add("open");
    fetch(`/api/explore/${id}`)
      .then((r) => r.json())
      .then((s) => {
        const image = s.image_ref || s.has_image
          ? `<img class="explore-story-image" src="https://app.rithavo.com/explore/image/${s.id}" alt="">`
          : "";
        const industries = (s.industries || []).map((i) => `<span class="explore-chip">${i.name}</span>`).join("");
        const wrapper = document.createElement("div");
        wrapper.innerHTML = `
          ${image}
          <span class="explore-chip">${s.story_type_label || s.story_type}</span>${industries}
          <h2 style="margin:12px 0 6px;"></h2>
          <div class="explore-card-meta" style="margin-bottom:16px;"></div>
          <div class="explore-story-body"></div>
          ${s.why_it_matters ? `<div class="explore-why-it-matters"><strong>Why it matters</strong><div class="explore-story-body"></div></div>` : ""}
          ${s.source_url ? `<p style="margin-top:20px;"><a href="${s.source_url}" target="_blank" rel="noopener noreferrer">Read the original source →</a></p>` : ""}
        `;
        wrapper.querySelector("h2").textContent = s.headline;
        wrapper.querySelector(".explore-card-meta").textContent =
          (s.source_name || "Rithavo") + (s.published_at ? " · " + s.published_at.slice(0, 10) : "");
        wrapper.querySelector(".explore-story-body").textContent = s.body || s.summary || "";
        if (s.why_it_matters) wrapper.querySelectorAll(".explore-story-body")[1].textContent = s.why_it_matters;
        body.innerHTML = "";
        body.appendChild(wrapper);
      })
      .catch(() => { body.innerHTML = `<div class="app-banner app-banner-error">Couldn't load this story right now.</div>`; });
  }

  function mount(root, opts) {
    opts = opts || {};
    // Product Correction Phase: opening a story must never navigate the
    // visitor away from Home to app.rithavo.com -- an in-page modal
    // keeps the whole Explore experience inside Home, per that phase's
    // explicit "no separate customer-facing Explore journey" rule. Only
    // the story's OWN cited source link (an expected, separate external
    // citation, not a second Rithavo surface) opens in a new tab.
    const onOpenStory = opts.onOpenStory || openStoryModal;

    let page = 1;
    let hasMore = false;
    let loading = false;
    let shownRelevantHeader = false;
    let shownBroaderHeader = false;
    let renderedAnyCard = false;

    root.innerHTML = `
      <div class="explore-grid" id="explore-grid"></div>
      <div class="explore-end" id="explore-sentinel">Loading…</div>
    `;
    const grid = root.querySelector("#explore-grid");
    const sentinel = root.querySelector("#explore-sentinel");

    function appendStory(s) {
      if (s.is_relevant && !shownRelevantHeader) {
        grid.appendChild(sectionHeader("Relevant to you"));
        shownRelevantHeader = true;
      }
      if (!s.is_relevant && !shownBroaderHeader) {
        grid.appendChild(sectionHeader("Explore"));
        shownBroaderHeader = true;
      }
      const card = cardHtml(s);
      card.addEventListener("click", (e) => { e.preventDefault(); onOpenStory(s.id); });
      grid.appendChild(card);
      renderedAnyCard = true;
    }

    function loadMore() {
      if (loading || (page > 1 && !hasMore)) return;
      loading = true;
      fetch("/api/explore/stories?" + new URLSearchParams({ page: page }).toString())
        .then((r) => r.json())
        .then((data) => {
          (data.stories || []).forEach(appendStory);
          hasMore = !!data.has_more;
          page = data.next_page || page + 1;
          loading = false;
          if (!renderedAnyCard) {
            grid.innerHTML = `<div class="explore-empty">No stories yet — check back soon.</div>`;
          }
          if (!hasMore) {
            sentinel.textContent = renderedAnyCard ? "You're all caught up." : "";
            sentinel.style.display = renderedAnyCard ? "" : "none";
          } else {
            sentinel.style.display = "none"; // hidden until intersecting again
          }
        })
        .catch(() => { loading = false; sentinel.textContent = "Couldn't load more stories right now."; });
    }

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
