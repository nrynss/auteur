/**
 * Fail-closed money guard for the demo harness.
 *
 * The paid SFX tier bills ~$0.10–0.30 per stem and director turns bill a
 * model call. Capture is navigation / hover / select / drag / playback —
 * free. This module never stubs GET traffic (filming mocks is forbidden);
 * it only aborts accidental billable mutations and records violations so
 * the test fails before a second take costs more.
 */
import type { Page, Request } from "@playwright/test";

/**
 * POST/PUT/PATCH paths that spend money or start provider-backed work.
 * Intentionally free writes (not matched): PUT …/timeline (draft commit
 * storage), PUT …/budget (config only), unclaimed claim/adopt (ops).
 */
const BILLABLE_PATH =
  /\/api\/v1\/projects\/[^/]+\/(director|mixes|exports|analyze)(?:\?|$)|\/events\/[^/]+\/regenerate(?:\?|$)/;

/**
 * Button / dialog labels we must never activate.
 * Allowed playback labels (not forbidden): "Foley mix", "Original", "Preview",
 * "Play", "Pause" — "Foley mix" is explicitly allowlisted before this pattern.
 */
const FORBIDDEN_CLICK =
  /\b(mix|export|regenerate|confirm cost|confirm &|send|start mix|start export)\b/i;

export interface Safety {
  violations: string[];
  assertClean: () => void;
}

function pathOf(req: Request): string {
  try {
    return new URL(req.url()).pathname;
  } catch {
    return req.url();
  }
}

export function isBillableRequest(req: Request): boolean {
  const method = req.method().toUpperCase();
  if (method !== "POST" && method !== "PUT" && method !== "PATCH") {
    return false;
  }
  // putTimeline (PUT …/timeline) is a free storage write — not billable.
  // We still avoid clicking Commit in the clips; this only filters money paths.
  const p = pathOf(req);
  if (method === "PUT" && /\/timeline$/.test(p)) return false;
  return BILLABLE_PATH.test(p);
}

/**
 * Install on every capture page. Continues all non-billable traffic
 * unchanged (real backend). Aborts billable mutations.
 */
export async function installSafety(page: Page): Promise<Safety> {
  const violations: string[] = [];

  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    if (isBillableRequest(req)) {
      const msg = `BILLABLE blocked: ${req.method()} ${pathOf(req)}`;
      violations.push(msg);
      console.error(`[demo-safety] ${msg}`);
      await route.abort("failed");
      return;
    }
    await route.continue();
  });

  // Defense: if a clip accidentally targets a spend control, refuse the click.
  await page.exposeFunction("__demoSafetyNote", (msg: string) => {
    violations.push(msg);
    console.error(`[demo-safety] ${msg}`);
  });

  await page.addInitScript((patternSource: string) => {
    const re = new RegExp(patternSource, "i");

    const labelOf = (el: EventTarget | null): string => {
      if (!(el instanceof Element)) return "";
      const btn = el.closest("button, [role='button'], a, input[type='submit']");
      if (!btn) return "";
      return (
        (btn.textContent || "") +
        " " +
        (btn.getAttribute("aria-label") || "") +
        " " +
        (btn.getAttribute("title") || "")
      );
    };

    /**
     * Director submit is labelled "⏎", not "Send". Match only the submit
     * control — not the text input or the whole form (input clicks must stay free).
     */
    const isDirectorSubmitControl = (el: EventTarget | null): boolean => {
      if (!(el instanceof Element)) return false;
      const director = el.closest('[aria-label="Director mode"]');
      if (!director) return false;
      const submit = el.closest(
        'button[type="submit"], input[type="submit"]',
      );
      return Boolean(submit && director.contains(submit));
    };

    document.addEventListener(
      "click",
      (e) => {
        if (isDirectorSubmitControl(e.target)) {
          e.preventDefault();
          e.stopImmediatePropagation();
          // @ts-expect-error injected
          window.__demoSafetyNote?.(
            "Forbidden click blocked: director composer submit",
          );
          return;
        }
        const label = labelOf(e.target).trim();
        if (!label) return;
        // Allow "Foley mix" (A/B source toggle) — not the spend Mix action.
        // Allowed playback labels: Foley mix, Original, Preview, Play, Pause.
        if (
          /foley\s*mix/i.test(label) &&
          !/\b(run|start|confirm)\b/i.test(label)
        ) {
          return;
        }
        if (re.test(label)) {
          e.preventDefault();
          e.stopImmediatePropagation();
          // @ts-expect-error injected
          window.__demoSafetyNote?.(
            `Forbidden click blocked: ${label.slice(0, 80)}`,
          );
        }
      },
      true,
    );

    // Catch Enter-to-submit on the director composer (label is ⏎, not "Send").
    document.addEventListener(
      "submit",
      (e) => {
        const form = e.target;
        if (!(form instanceof HTMLFormElement)) return;
        if (!form.closest('[aria-label="Director mode"]')) return;
        e.preventDefault();
        e.stopImmediatePropagation();
        // @ts-expect-error injected
        window.__demoSafetyNote?.(
          "Forbidden submit blocked: director composer form",
        );
      },
      true,
    );
  }, FORBIDDEN_CLICK.source);

  return {
    violations,
    assertClean() {
      if (violations.length) {
        throw new Error(
          `Demo safety violations (no billable action allowed):\n` +
            violations.map((v) => `  - ${v}`).join("\n"),
        );
      }
    },
  };
}
