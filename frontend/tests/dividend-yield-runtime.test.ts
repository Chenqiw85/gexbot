import assert from "node:assert/strict";

import { dividendYieldFromPercentInput } from "../lib/form-utils";

assert.equal(dividendYieldFromPercentInput(""), undefined);
assert.equal(dividendYieldFromPercentInput(" 1.25 "), 0.0125);
assert.equal(dividendYieldFromPercentInput("0"), 0);
assert.equal(dividendYieldFromPercentInput("100"), 1);
assert.throws(() => dividendYieldFromPercentInput("-0.01"), /between 0% and 100%/);
assert.throws(() => dividendYieldFromPercentInput("100.01"), /between 0% and 100%/);
assert.throws(() => dividendYieldFromPercentInput("abc"), /must be a number/);
