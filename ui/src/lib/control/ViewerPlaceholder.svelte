<script lang="ts">
  /** The video area — deliberately an UNFINISHED slot, not a settled player.
   * The A/B player and browser preview are unit 7.9; putting a fake player
   * here would make the layout look done when it is not. The frame keeps its
   * 16:9 well so the layout holds; the slot says what is missing. */
</script>

<div class="well">
  <div class="slot">
    <span class="tag">video player — unit 7.9</span>
    <p>
      Source, Foley mix, and the browser preview land here. Until then this
      area is an empty slot, not a placeholder pretending to be a player.
    </p>
  </div>
</div>

<style>
  .well {
    flex: 1 1 auto;
    min-height: 0;
    background: var(--sunken);
    display: grid;
    place-items: center;
    padding: 16px;
    position: relative;
  }

  .slot {
    width: 100%;
    max-width: 560px;
    aspect-ratio: 16/9;
    border: 1px dashed var(--line);
    border-radius: var(--r);
    background: var(--ground);
    display: grid;
    place-content: center;
    gap: 8px;
    padding: 24px;
    text-align: center;
  }

  .tag {
    font: 500 10px/1 var(--font-mono);
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--faint);
  }

  p {
    margin: 0;
    font-size: var(--text-11);
    line-height: 1.5;
    color: var(--dim);
    max-width: 40ch;
  }
</style>
