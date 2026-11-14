/* Entry points from the private /profile/ page to the public-profile and business owner panel (/c/manage).
   The links are created only when the management API answers 200, i.e. only while RITHAVO_PROFILE_MANAGEMENT_ENABLED is on and the
   dedicated service secret is configured. While the feature is off the API answers 404 and nothing is added to the page. */
(function () {
  "use strict";
  var host = document.getElementById("public-profile-entry");
  if (!host || !window.fetch) { return; }
  fetch("/api/my-public-profile", { credentials: "same-origin", cache: "no-store", headers: { "Accept": "application/json" } }).then(function (res) {
    if (res.status !== 200) { return; }
    [["/c/manage", "Public profile: preview and settings", "btn btn-ghost"], ["/c/manage?view=business", "Business profile", "btn btn-ghost"]].forEach(function (l) {
      var a = document.createElement("a");
      a.className = l[2];
      a.href = l[0];
      a.textContent = l[1];
      host.appendChild(a);
    });
  }).catch(function () { /* the page works exactly as before */ });
})();
