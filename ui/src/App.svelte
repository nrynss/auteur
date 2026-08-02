<script lang="ts">
  import { onMount } from "svelte";

  import ControlRoom from "./lib/control/ControlRoom.svelte";
  import PrimitivesGallery from "./lib/PrimitivesGallery.svelte";
  import CreateProject from "./lib/projects/CreateProject.svelte";
  import ProjectsIndex from "./lib/projects/ProjectsIndex.svelte";
  import { navigate, parseRoute, type Route } from "./lib/projects/route";

  // Path-based SPA routes (unit 7.6 catch-all + 7.13 index/create).
  // / → projects index · /projects/new → create · /projects/{id} → control room
  let route = $state<Route>(parseRoute(window.location.pathname));

  const pageTitle = $derived(
    route.name === "create"
      ? "FoleyFlow — New project"
      : route.name === "project"
        ? "FoleyFlow — Control room"
        : "FoleyFlow — Projects",
  );

  function go(path: string) {
    route = navigate(path);
  }

  onMount(() => {
    const onPop = () => (route = parseRoute(window.location.pathname));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  });
</script>

<svelte:head>
  <title>{pageTitle}</title>
</svelte:head>

{#if route.name === "project"}
  <ControlRoom id={route.id} />
{:else if route.name === "create"}
  <CreateProject onNavigate={go} />
{:else}
  <ProjectsIndex onNavigate={go} />
  {#if import.meta.env.DEV}
    <PrimitivesGallery />
  {/if}
{/if}
