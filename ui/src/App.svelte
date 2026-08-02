<script lang="ts">
  import { onMount } from "svelte";

  import { ApiError, api } from "./lib/api/client";
  import type { components } from "./lib/api/schema";

  type HealthResponse = components["schemas"]["HealthResponse"];
  type ProjectSummary = components["schemas"]["ProjectSummary"];
  type ProjectListResponse = components["schemas"]["ProjectListResponse"];

  let health = $state<HealthResponse | null>(null);
  let projects = $state<ProjectSummary[]>([]);
  let status = $state<"loading" | "ready" | "error">("loading");
  let message = $state("");

  onMount(async () => {
    try {
      // One request proves the SPA ↔ API round trip end to end: schema-driven
      // client, real health payload. Screens land in units 7.8+.
      health = await api<HealthResponse>("/api/v1/health");
      const list = await api<ProjectListResponse>("/api/v1/projects?limit=20");
      projects = list.projects;
      status = "ready";
    } catch (e) {
      status = "error";
      message = e instanceof ApiError ? e.message : String(e);
    }
  });
</script>

<svelte:head>
  <title>FoleyFlow — Control room</title>
</svelte:head>

<header class="chrome">
  <span class="brand">FoleyFlow</span>
  {#if health}
    <span class="dim">control room</span>
    <span class="spacer"></span>
    <span class="mono">storage {health.storage}</span>
    <span class="mono faint">v{health.version}</span>
  {/if}
</header>

<main>
  {#if status === "loading"}
    <p class="dim">Contacting the API…</p>
  {:else if status === "error"}
    <p class="stop">
      Could not reach the API — {message}
    </p>
  {:else}
    <h1>Control room</h1>
    <p class="dim">
      Toolchain scaffold (unit 7.6). The screens — readiness, timeline,
      director mode — land in units 7.7–7.14.
    </p>
    <p>
      {projects.length} project{projects.length === 1 ? "" : "s"} on this
      deployment.
    </p>
    <ul class="projects">
      {#each projects as p (p.id)}
        <li>
          <span class="mono id">{p.id}</span>
          <span>{p.title}</span>
          <span class="dim">{p.status}</span>
          {#if p.duration_s != null}
            <span class="mono faint">{p.duration_s.toFixed(1)}s</span>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</main>

<style>
  .chrome {
    display: flex;
    align-items: baseline;
    gap: 0.75rem;
    height: 46px;
    padding: 0 1rem;
    background: var(--surface);
    border-bottom: 1px solid var(--line);
  }

  .brand {
    font-weight: 590;
    font-size: 14px;
    letter-spacing: 0.01em;
  }

  .spacer {
    flex: 1;
  }

  main {
    max-width: 960px;
    margin: 0 auto;
    padding: 1.5rem 1rem;
  }

  h1 {
    font-size: 14px;
    font-weight: 590;
    margin: 0 0 0.5rem;
  }

  ul.projects {
    list-style: none;
    margin: 0.75rem 0 0;
    padding: 0;
    border-top: 1px solid var(--line);
  }

  ul.projects li {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 0.5rem 1rem;
    padding: 0.5rem 0;
    border-bottom: 1px solid var(--line);
  }

  .mono {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }

  .id {
    color: var(--faint);
    font-size: 11.5px;
    grid-column: 1 / -1;
  }

  .dim {
    color: var(--dim);
  }

  .faint {
    color: var(--faint);
  }

  .stop {
    color: var(--stop);
  }
</style>
