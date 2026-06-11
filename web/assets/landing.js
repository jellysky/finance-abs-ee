// Render the product-family grid from data/products.json
fetch("data/products.json").then(r => r.json()).then(d => {
  const grid = document.getElementById("grid");
  grid.innerHTML = d.products.map(p => {
    const live = p.status === "live";
    const inner = `
      <div class="row"><span class="ac">${p.asset_class || ""}</span>
        <span class="pill ${live ? "live" : "planned"}">${live ? "Live" : "Planned"}</span></div>
      <h3>${p.name}</h3>
      <p>${p.tagline || ""}</p>
      <div class="row"><span class="ticker">${p.ticker || ""}</span>
        <span class="ac">${live ? "View charts →" : "Coming soon"}</span></div>`;
    return live
      ? `<a class="card" href="product.html?slug=${p.slug}">${inner}</a>`
      : `<div class="card" style="opacity:.62">${inner}</div>`;
  }).join("");
}).catch(e => {
  document.getElementById("grid").innerHTML =
    `<div class="card"><p>Could not load products.json — run <code>python web/build_site_data.py</code>.</p></div>`;
});
