<script lang="ts">
  /** Brand — the one chrome word every route shares: the product name as a
   * link home. Single definition, used by every route (task: the brand
   * recedes, the document dominates). It is deliberately smaller and dimmer
   * than any route's own title, so "Auteur Western standoff (silent)" never
   * reads as one compound phrase, and it carries a trailing separator so the
   * product and the document are visibly two things. */
  import { BRAND_NAME } from "../brand";

  interface Props {
    /** SPA navigation callback. Every route passes it so the brand link
     * navigates in-app; when absent the plain href="/" still works. */
    onNavigate?: (path: string) => void;
  }

  let { onNavigate }: Props = $props();

  function home(e: MouseEvent) {
    if (onNavigate) {
      e.preventDefault();
      onNavigate("/");
    }
  }
</script>

<a class="brand" href="/" onclick={home}>{BRAND_NAME}</a>

<style>
  .brand {
    font-size: var(--text-12);
    font-weight: var(--w-560);
    color: var(--dim);
    letter-spacing: 0.01em;
    text-decoration: none;
    white-space: nowrap;
    flex: 0 0 auto;
    /* The separator: the brand is chrome, never part of the document's
     * title. */
    padding-right: 12px;
    border-right: 1px solid var(--line);
    margin-right: 2px;
  }

  .brand:hover,
  .brand:focus-visible {
    color: var(--text);
  }
</style>
