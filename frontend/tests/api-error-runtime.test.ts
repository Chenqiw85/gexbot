import assert from "node:assert/strict";

import { parseApiError } from "../lib/api";

async function main() {
  const rateLimited = new Response(
    JSON.stringify({
      error: {
        code: "provider_rate_limited",
        message: "rate limit",
        rate_limit_available: "0",
        rate_limit_expiry: "30"
      }
    }),
    { status: 429 }
  );

  const rateLimitMessage = await parseApiError("latest", rateLimited);
  assert.match(rateLimitMessage, /provider_rate_limited/);
  assert.match(rateLimitMessage, /rate_limit_available=0/);

  const validationError = new Response(
    JSON.stringify({
      detail: [
        {
          loc: ["body", "min_dte"],
          msg: "Input should be greater than or equal to 0"
        },
        {
          loc: ["body", "max_dte"],
          msg: "Field required"
        }
      ]
    }),
    { status: 422 }
  );

  const validationMessage = await parseApiError("capture", validationError);
  assert.match(validationMessage, /validation_error/);
  assert.match(validationMessage, /body\.min_dte: Input should be greater than or equal to 0/);
  assert.match(validationMessage, /body\.max_dte: Field required/);

  const structuredValidationError = new Response(
    JSON.stringify({
      error: {
        code: "validation_error",
        fields: [
          {
            path: "body.min_dte",
            message: "Input should be greater than or equal to 0"
          }
        ]
      }
    }),
    { status: 422 }
  );

  const structuredValidationMessage = await parseApiError("capture", structuredValidationError);
  assert.match(structuredValidationMessage, /validation_error/);
  assert.match(structuredValidationMessage, /body\.min_dte: Input should be greater than or equal to 0/);

  const providerError = new Response(
    JSON.stringify({
      error: {
        code: "provider_error",
        message: "/markets/options/chains failed status=503: maintenance",
        provider_status_code: 503
      }
    }),
    { status: 502 }
  );

  const providerMessage = await parseApiError("capture", providerError);
  assert.match(providerMessage, /provider_error/);
  assert.match(providerMessage, /provider_status_code=503/);
}

void main();
