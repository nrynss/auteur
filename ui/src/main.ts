import { mount } from "svelte";

import "./app.css";
import App from "./App.svelte";
import { BRAND_NAME } from "./lib/brand";

const target = document.getElementById("app");
if (!target) {
  // Branded like the rest of the rendered surface (task 19): this message
  // only exists when index.html is broken, but a rebranded deployment should
  // not name a product it does not ship.
  throw new Error(`${BRAND_NAME}: missing #app mount point in index.html`);
}

export default mount(App, { target });
