/** Path-based SPA routing for the control room shell (unit 7.13).
 * Deep links work through the API's SPA catch-all (unit 7.6). */

export type Route =
  | { name: "home" }
  | { name: "create" }
  | { name: "project"; id: string }
  | { name: "unclaimed" };

/** Parse a pathname into a route. `/projects/new` is create; any other
 * `/projects/{id}` is the control room; `/unclaimed` is the archive of
 * material no project claims (unit 7.17). Everything else is the index. */
export function parseRoute(pathname: string): Route {
  const path = pathname.replace(/\/+$/, "") || "/";
  if (path === "/projects/new") return { name: "create" };
  if (path === "/unclaimed") return { name: "unclaimed" };
  const m = path.match(/^\/projects\/([^/]+)$/);
  if (m) return { name: "project", id: decodeURIComponent(m[1]) };
  return { name: "home" };
}

/** Push a path and return the resulting route (caller assigns to state). */
export function navigate(path: string): Route {
  const next = path.startsWith("/") ? path : `/${path}`;
  if (window.location.pathname !== next) {
    window.history.pushState({}, "", next);
  }
  return parseRoute(next);
}
