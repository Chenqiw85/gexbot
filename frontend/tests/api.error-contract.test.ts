import { parseApiError } from "@/lib/api";

async function apiErrorContract() {
  const response = new Response(
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

  const message: string = await parseApiError("latest", response);
  return message.includes("provider_rate_limited");
}

void apiErrorContract;
