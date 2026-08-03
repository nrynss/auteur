<script lang="ts">
  /** Read-only deployment configuration (task 20, docs/api.md §3.17): the
   * first question after any deploy — "is this deployment wired up
   * correctly?" — answered as a screen. The checks are the same rows
   * `foleyflow doctor` prints (same states, same fail-closed rules), the
   * build identity comes from /health's stamp, and the integrations list
   * reports PRESENCE only — variable names and booleans, never a value. The
   * service deploys --allow-unauthenticated, so a mutation or value-echoing
   * route would be an open credential endpoint; rotation stays a Secret
   * Manager operation.
   */
  import { onMount } from "svelte";

  import Button from "../components/Button.svelte";
  import Chip from "../components/Chip.svelte";
  import Spinner from "../components/Spinner.svelte";
  import ThemeToggle from "../components/ThemeToggle.svelte";
  import ChecklistRow from "../control/ChecklistRow.svelte";
  import { BRAND_NAME } from "../brand";
  import { ApiError } from "../api/client";
  import HelpHint from "../help/HelpHint.svelte";
  import { getConfig, type ConfigResponse } from "../api/projects";
  import type { ReadinessItem } from "../api/projects";

  interface Props {
    onNavigate: (path: string) => void;
  }

  let { onNavigate }: Props = $props();

  let config = $state<ConfigResponse | null>(null);
  let error = $state<string | null>(null);

  onMount(() => {
    void load();
  });

  async function load() {
    error = null;
    try {
      config = await getConfig();
    } catch (e) {
      config = null;
      error =
        e instanceof ApiError
          ? e.message
          : e instanceof Error
            ? e.message
            : String(e);
    }
  }

  /** The build row doctor also prints. The server cannot compare a deployed
   * build to anyone's local HEAD (the browser is not a git checkout), so the
   * row shows the deployed identity, with doctor's origin-based severity for
   * an unstamped process: warn on a loopback origin, blocked on a non-local
   * one (an unstamped deploy cannot prove freshness). */
  const buildRow = $derived.by<ReadinessItem | null>(() => {
    if (!config) return null;
    if (config.git_sha) {
      return {
        key: "build",
        label: "Deployed build identity",
        state: "ok",
        detail: `git_sha=${config.git_sha.slice(0, 12)}${
          config.build_time ? ` · built ${config.build_time}` : ""
        }`,
      };
    }
    const host = window.location.hostname.toLowerCase();
    const local = host === "localhost" || host === "127.0.0.1" || host === "::1";
    return local
      ? {
          key: "build",
          label: "Deployed build identity",
          state: "warn",
          detail: "unstamped process — cannot verify freshness",
        }
      : {
          key: "build",
          label: "Deployed build identity",
          state: "blocked",
          detail: "unstamped deploy — no build identity",
        };
  });

  /** The brand row: the UI bundle's build-time name (this screen's own
   * BRAND_NAME) vs the API's runtime name (FOLEYFLOW_BRAND_NAME — the /docs
   * title and the UI-not-built page). A disagreement means one deployment
   * renders two names, which reads as two different products — blocked.
   * This is the task-21 acceptance clause beyond the doctor rows. */
  const brandRow = $derived.by<ReadinessItem | null>(() => {
    if (!config) return null;
    if (config.brand === BRAND_NAME) {
      return {
        key: "brand",
        label: "Brand agreement",
        state: "ok",
        detail: `UI and API both serve ${BRAND_NAME}`,
      };
    }
    return {
      key: "brand",
      label: "Brand agreement",
      state: "blocked",
      detail: `UI built as ${BRAND_NAME} but the API serves ${config.brand} — ` +
        "one deployment renders two names",
    };
  });

  const checks = $derived<ReadinessItem[]>(
    [
      ...(config?.checks ?? []),
      ...(brandRow ? [brandRow] : []),
      ...(buildRow ? [buildRow] : []),
    ],
  );
  const blockedCount = $derived(
    checks.filter((c) => c.state === "blocked").length,
  );
</script>

<header class="chrome">
  <a
    class="brand"
    href="/"
    onclick={(e) => {
      e.preventDefault();
      onNavigate("/");
    }}
    >{BRAND_NAME}</a
  >
  <span class="dim">configuration</span>
  <span class="spacer"></span>
  <Button variant="ghost" size="sm" onclick={() => onNavigate("/")}>
    Projects
  </Button>
  <HelpHint />
  <ThemeToggle />
</header>

<main>
  {#if error}
    <div class="state" role="alert">
      <p class="stop">Could not load the deployment sheet — {error}</p>
      <Button variant="primary" size="sm" onclick={() => void load()}>
        Retry
      </Button>
    </div>
  {:else if !config}
    <div class="state">
      <Spinner label="Loading configuration" />
      <span class="dim">Loading configuration…</span>
    </div>
  {:else}
    <p class="eyebrow">Deployment checks</p>
    <ul class="check">
      {#each checks as item (item.key)}
        <ChecklistRow {item} />
      {/each}
    </ul>
    <p class="summary" class:stop={blockedCount > 0} role="status">
      {#if blockedCount === 0}
        Deployment checks pass — this deployment is wired up.
      {:else}
        {blockedCount} {blockedCount === 1 ? "check" : "checks"} blocked — fix
        the STOP items before relying on it.
      {/if}
    </p>

    <p class="eyebrow">Build identity</p>
    <div class="identity">
      <div class="field">
        <span class="flabel">version</span>
        <span class="fval num">{config.version}</span>
      </div>
      <div class="field">
        <span class="flabel">git_sha</span>
        <span class="fval num">{config.git_sha ?? "— (unstamped)"}</span>
      </div>
      <div class="field">
        <span class="flabel">built</span>
        <span class="fval num">{config.build_time ?? "—"}</span>
      </div>
    </div>

    <p class="eyebrow">Integrations</p>
    <ul class="integrations">
      {#each config.integrations as i (i.key)}
        <li>
          <span class="name">{i.label}</span>
          <span class="src">{i.source === "secret-manager" ? "Secret Manager" : "Env var"}</span>
          {#if i.configured}
            <Chip state="ok" label="Configured" detail="vars present" />
          {:else}
            <span class="absent">
              Absent{i.missing.length ? ` — ${i.missing.join(", ")}` : ""}
            </span>
          {/if}
          {#if i.unresolved.length > 0}
            <span class="unresolved" role="alert">
              unresolved Secret Manager reference: {i.unresolved.join(", ")}
            </span>
          {/if}
        </li>
      {/each}
    </ul>

    <p class="note">
      Read-only — this screen reports variable <em>names</em> and presence,
      never values. Writing or rotating credentials stays a Secret Manager
      operation (scripts/setup_secrets.sh); the service deploys without
      authentication, so a mutation route here would be an open credential
      endpoint.
    </p>
  {/if}
</main>

<style>
  .chrome {
    display: flex;
    align-items: center;
    gap: 12px;
    height: var(--chrome-h);
    padding: 0 1rem;
    background: var(--surface);
    border-bottom: 1px solid var(--line);
  }

  .brand {
    font-weight: var(--w-590);
    font-size: var(--text-14);
    color: var(--text);
    text-decoration: none;
  }

  .dim {
    color: var(--faint);
    font-size: var(--text-11);
  }

  .spacer {
    flex: 1 1 auto;
  }

  main {
    max-width: 720px;
    margin: 0 auto;
    padding: 18px 1rem 48px;
  }

  .state {
    display: grid;
    place-content: center;
    gap: 10px;
    justify-items: center;
    padding: 60px 0;
    color: var(--dim);
  }

  .stop {
    color: var(--stop);
    margin: 0;
  }

  .eyebrow {
    font-size: 10px;
    letter-spacing: 0.07em;
    text-transform: uppercase;
    color: var(--faint);
    font-weight: 600;
    margin: 22px 0 8px;
  }

  .check {
    list-style: none;
    margin: 0;
    padding: 0;
    border: 1px solid var(--line);
    border-radius: var(--r);
    overflow: hidden;
  }

  .check :global(li) {
    border-bottom: 1px solid var(--line-soft);
  }

  .check :global(li:last-child) {
    border-bottom: 0;
  }

  .summary {
    margin: 10px 2px 0;
    font-size: var(--text-12);
    color: var(--ok);
  }

  .summary.stop {
    color: var(--stop);
  }

  .identity {
    display: grid;
    gap: 6px;
    padding: 10px 12px;
    border: 1px solid var(--line);
    border-radius: var(--r);
    background: var(--raised);
  }

  .field {
    display: flex;
    align-items: baseline;
    gap: 10px;
  }

  .flabel {
    font-size: 10px;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--faint);
    flex: 0 0 60px;
  }

  .fval {
    font-size: var(--text-12);
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .integrations {
    list-style: none;
    margin: 0;
    padding: 0;
    border: 1px solid var(--line);
    border-radius: var(--r);
    overflow: hidden;
  }

  .integrations li {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 12px;
    border-bottom: 1px solid var(--line-soft);
    font-size: var(--text-12);
    flex-wrap: wrap;
  }

  .integrations li:last-child {
    border-bottom: 0;
  }

  .integrations .name {
    font-weight: var(--w-560);
    min-width: 0;
  }

  .src {
    font-size: 9.5px;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--faint);
    border: 1px solid var(--line);
    border-radius: 999px;
    padding: 1px 7px;
    white-space: nowrap;
  }

  .absent {
    font-size: 9.5px;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--dim);
    border: 1px solid var(--line);
    border-radius: 999px;
    padding: 1px 7px;
    white-space: nowrap;
    background: var(--raised);
  }

  .unresolved {
    flex-basis: 100%;
    font-size: var(--text-11);
    color: var(--warn);
  }

  .note {
    margin: 18px 2px 0;
    font-size: var(--text-11);
    line-height: 1.6;
    color: var(--dim);
  }

  .note em {
    color: var(--text);
    font-style: normal;
  }

  .num {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }
</style>
