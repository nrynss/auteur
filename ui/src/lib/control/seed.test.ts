/** Parity pins for the legacy seed computation (unit 7.10). Every vector is
 * Python's actual output for the same input — assemble._event_seed hashed by
 * hashlib.sha256 — so a JS/Python divergence (rounding mode, hash, encoding)
 * fails here before it silently re-rolls a rhythmic event's texture on a
 * real mix. The tie values (0.0625, 0.3125, 0.5625, …) are the exact dyadic
 * cases where `toFixed` rounds half-up and Python rounds half-even; the rest
 * are realistic timeline times. */
import { describe, expect, it } from "vitest";
import { legacySeed, pyToFixed3 } from "./seed";
import { sha256 } from "./sha256";

const NAME = "rhythmic church bell tolling";

// python3 - <<'EOF' | jq -c '.[]'
// import hashlib, json
// for v in [...]: ...
const VECTORS: Array<[number, number]> = [
  [0.0, 20572028],
  [0.001, 2581224592],
  [0.01, 652308645],
  [0.0625, 973944757],
  [0.09, 934470500],
  [0.1, 1072272495],
  [0.3125, 2207675164],
  [0.4375, 3748344299],
  [0.5625, 3306808941],
  [0.6875, 3535797791],
  [0.8125, 2726333232],
  [0.9375, 3185171665],
  [1.0625, 2051925909],
  [1.1875, 3328328388],
  [1.2345, 1299852736],
  [1.3125, 1256224255],
  [10.95, 3363908823],
  [11.0, 3285744699],
  [15.041667, 3112232308],
  [2.0, 1698605846],
  [3.5, 49932119],
  [5.6005, 2865604667],
  [6.65, 1261277983],
  [7.35, 3785715423],
  [9.88, 3325261930],
];

describe("legacySeed parity with Python", () => {
  it.each(VECTORS)("seed(%s) matches Python's hashlib value", (start, expected) => {
    expect(legacySeed(NAME, start)).toBe(expected);
  });

  it("starts 10 ms apart produce different seeds (they did before 7.1)", () => {
    expect(legacySeed(NAME, 6.65)).not.toBe(legacySeed(NAME, 6.66));
  });

  it("rounds half-even exactly where toFixed would diverge", () => {
    // 0.3125 is an exact dyadic tie: Python → "0.312", JS toFixed → "0.313".
    expect(pyToFixed3(0.3125)).toBe("0.312");
    expect((0.3125).toFixed(3)).toBe("0.313"); // the trap the emulation closes
    expect(pyToFixed3(0.4375)).toBe("0.438"); // tie, half-even rounds up here
    expect(pyToFixed3(1.0625)).toBe("1.062");
    // Non-ties must be untouched by the emulation.
    expect(pyToFixed3(15.041667)).toBe("15.042");
    expect(pyToFixed3(0.001)).toBe("0.001");
    expect(pyToFixed3(0)).toBe("0.000");
  });
});

describe("sha256", () => {
  it("matches the well-known empty-string digest", () => {
    expect(sha256("")).toBe(
      "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    );
  });

  it("matches FIPS vectors", () => {
    expect(sha256("abc")).toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
    expect(
      sha256(
        "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq",
      ),
    ).toBe(
      "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1",
    );
  });

  it("mirrors the exact string a seed hashes", () => {
    // int(sha256("rhythmic church bell tolling|6.650").hexdigest()[:8], 16)
    expect(sha256(`${NAME}|6.650`).slice(0, 8)).toBe("4b2d931f");
  });
});
