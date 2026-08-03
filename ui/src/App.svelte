<script lang="ts">
  import { onMount } from "svelte";

  import { brandPageTitle } from "./lib/brand";
  import ConfigScreen from "./lib/config/ConfigScreen.svelte";
  import ControlRoom from "./lib/control/ControlRoom.svelte";
  import HelpOverlay from "./lib/help/HelpOverlay.svelte";
  import { closeHelp, help, openHelp } from "./lib/help/helpStore.svelte";
  import PrimitivesGallery from "./lib/PrimitivesGallery.svelte";
  import CreateProject from "./lib/projects/CreateProject.svelte";
  import ProjectsIndex from "./lib/projects/ProjectsIndex.svelte";
  import Unclaimed from "./lib/projects/Unclaimed.svelte";
  import { navigate, parseRoute, type Route } from "./lib/projects/route";

  // Path-based SPA routes (unit 7.6 catch-all + 7.13 index/create + 7.17 archive).
  // / → projects index · /projects/new → create · /projects/{id} → control room
  // · /unclaimed → material no project claims (7.17)
  let route = $state<Route>(parseRoute(window.location.pathname));

  // Page titles use the build-time brand (task 19): a rebranded deployment
  // renders its own name here, and the template is configurable too.
  const pageTitle = $derived(
    route.name === "create"
      ? brandPageTitle("New project")
      : route.name === "project"
        ? brandPageTitle("Control room")
        : route.name === "unclaimed"
          ? brandPageTitle("Orphans")
          : route.name === "config"
            ? brandPageTitle("Configuration")
            : brandPageTitle("Projects"),
  );

  function go(path: string) {
    route = navigate(path);
  }

  onMount(() => {
    const onPop = () => (route = parseRoute(window.location.pathname));
    window.addEventListener("popstate", onPop);

    // `?` opens help from anywhere — except while typing (an input owns `?`)
    // or inside a dialog (the dialog traps its own keys).
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "?") return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return;
      if (t?.closest('[role="dialog"]')) return;
      e.preventDefault();
      openHelp();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("popstate", onPop);
      window.removeEventListener("keydown", onKey);
    };
  });
</script>

<svelte:head>
  <title>{pageTitle}</title>
</svelte:head>

{#if route.name === "project"}
  <ControlRoom id={route.id} />
{:else if route.name === "create"}
  <CreateProject onNavigate={go} />
{:else if route.name === "unclaimed"}
  <Unclaimed onNavigate={go} />
{:else if route.name === "config"}
  <ConfigScreen onNavigate={go} />
{:else}
  <ProjectsIndex onNavigate={go} />
  {#if import.meta.env.DEV}
    <PrimitivesGallery />
  {/if}
{/if}

<HelpOverlay open={help.open} onclose={closeHelp} />
